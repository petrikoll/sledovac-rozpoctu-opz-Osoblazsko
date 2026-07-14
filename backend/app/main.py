from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from collections import defaultdict
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests
from google.oauth2 import id_token

from .calculations import final_settlement, lump_sum_metrics, propose_transfers, q
from .models import CofinancingEntry, LumpSumEntry, Project, ProjectCreate, ProjectSchedule, Sd2AttachmentRecord, Sd2MonthlyEntry, TransferCandidate
from .pdf_parser import extract_budget_code, parse_payment_request
from .payroll_parser import parse_payroll_slips, parse_payslip_insurance
from .repository import GoogleSheetsRepository, InMemoryRepository
from .sd2_xml import build_sd2_xml
from .storage import GoogleDriveStorage, LocalFileStorage
from .xlsx_parser import export_budget_status, export_final_settlement, export_transfer_proposal, export_with_formulas, parse_budget, parse_financial_plan, validate_budget_structure

app = FastAPI(title="Sledovač čerpání rozpočtu OPZ+", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_methods=["*"], allow_headers=["*"])
repo = InMemoryRepository()
google_repo = None
file_storage = None
user_roles: dict[str, str] = {}
active_user: ContextVar[dict | None] = ContextVar("active_user", default=None)
if os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") and os.getenv("GOOGLE_SPREADSHEET_ID"):
    google_repo = GoogleSheetsRepository(os.environ["GOOGLE_SPREADSHEET_ID"], os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    google_repo.ensure_schema()
    google_repo.hydrate(repo)
    user_roles = {str(row.get("email", "")).lower(): str(row.get("role", "user"))
                  for row in google_repo._records("USERS") if google_repo._bool(row.get("active", True))}
    if os.getenv("GOOGLE_DRIVE_FOLDER_ID"):
        file_storage = GoogleDriveStorage(os.environ["GOOGLE_DRIVE_FOLDER_ID"], os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
elif os.getenv("ENVIRONMENT", "development") == "development":
    file_storage = LocalFileStorage(os.path.join(os.getcwd(), ".uploads"))
analyses: dict[str, dict] = {}
MAX_SIZE = 20 * 1024 * 1024
RECIPIENT_ONLY_USERS = {
    "starosta@divcihrad.cz": "osoblažský cech, z.ú.",
    "emceckovm@gmail.com": "rodinné, komunitní a vzdělávací centrum emcéčko, z.s.",
}
PROJECT_ONLY_USERS = {
    "srssjesenik@gmail.com": "CZ.03.02.01/00/24_065/0004961",
    "mb_ucetni@seznam.cz": "CZ.03.02.01/00/25_106/0006125",
}


def current_user(authorization: str | None = Header(default=None)) -> dict:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id and os.getenv("ENVIRONMENT", "development") == "development":
        user = {"email": "local@localhost", "role": "admin"}; active_user.set(user); return user
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Přihlaste se Google účtem.")
    try:
        info = id_token.verify_oauth2_token(authorization[7:], requests.Request(), client_id)
    except Exception:
        raise HTTPException(401, "Google přihlášení není platné.")
    allowed = {x.strip().lower() for x in os.getenv("ALLOWED_EMAILS", "").split(",") if x.strip()}
    if info.get("email", "").lower() not in allowed:
        raise HTTPException(403, "Tento e-mail nemá povolený přístup.")
    email = info["email"].lower()
    user = {"email": email, "role": user_roles.get(email, "user")}; active_user.set(user); return user


def require_admin(user=Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Tuto změnu může provést pouze administrátor.")
    return user


def require_editor(user=Depends(current_user)) -> dict:
    if user["role"] not in {"admin", "editor"}:
        raise HTTPException(403, "Tuto změnu může provést administrátor nebo editor.")
    return user


def can_view_project(project_id: str, user: dict) -> bool:
    if user.get("role") == "admin": return True
    email = user.get("email", "").lower()
    project_only = PROJECT_ONLY_USERS.get(email)
    if project_only is not None:
        item = repo.project_data.get(project_id)
        return bool(item and item.project_code == project_only)
    recipient_only = RECIPIENT_ONLY_USERS.get(email)
    if recipient_only is not None:
        item = repo.project_data.get(project_id)
        return bool(item and item.recipient_name.strip().casefold() == recipient_only)
    allowed = repo.project_access.get(project_id, set())
    return not allowed or email in allowed


def project(project_id: str, user: dict | None = None) -> Project:
    if project_id not in repo.project_data:
        raise HTTPException(404, "Projekt nebyl nalezen.")
    viewer = user or active_user.get()
    if viewer is not None and not can_view_project(project_id, viewer):
        raise HTTPException(404, "Projekt nebyl nalezen.")
    return repo.project_data[project_id]


async def checked_file(upload: UploadFile, extension: str, mime: set[str]) -> bytes:
    if not upload.filename or not upload.filename.lower().endswith(extension):
        raise HTTPException(415, f"Povolen je pouze soubor {extension}.")
    if upload.content_type not in mime:
        raise HTTPException(415, "MIME typ souboru neodpovídá povolenému formátu.")
    data = await upload.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise HTTPException(413, "Soubor je větší než povolených 20 MB.")
    return data


@app.get("/api/health")
def health(): return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/api/me")
def me(user=Depends(current_user)): return user


@app.get("/api/projects")
def get_projects(user=Depends(current_user)): return [p for p in repo.projects() if can_view_project(p.project_id, user)]


@app.post("/api/projects", status_code=201)
def create_project(data: ProjectCreate, user=Depends(require_admin)):
    value = Project(**data.model_dump()); repo.save_project(value)
    if google_repo:
        google_repo.append_records("PROJEKTY", [{**value.model_dump(), "created_by": user["email"]}])
    return value


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user=Depends(current_user)): return project(project_id, user)


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, changes: dict, user=Depends(require_admin)):
    allowed = {k: v for k, v in changes.items() if k in Project.model_fields}
    value = project(project_id, user).model_copy(update=allowed)
    value.updated_at = datetime.utcnow(); repo.save_project(value)
    if google_repo:
        google_repo.update_record("PROJEKTY", "project_id", project_id, {**allowed, "updated_at": value.updated_at})
    return value


def next_month(value: date) -> date:
    return date(value.year + (1 if value.month == 12 else 0), 1 if value.month == 12 else value.month + 1, 1)


@app.get("/api/projects/{project_id}/schedule")
def get_project_schedule(project_id: str, user=Depends(current_user)):
    project(project_id, user)
    return repo.project_schedules.get(project_id, {"project_start_date": None, "project_end_date": None, "periods": []})


@app.put("/api/projects/{project_id}/schedule")
def save_project_schedule(project_id: str, value: ProjectSchedule, user=Depends(require_admin)):
    selected_project = project(project_id, user)
    if value.project_start_date > value.project_end_date:
        raise HTTPException(422, "Začátek projektu musí být před jeho koncem.")
    periods = sorted(value.periods, key=lambda item: item.monitoring_period)
    expected_numbers = list(range(1, selected_project.total_monitoring_periods + 1))
    if [item.monitoring_period for item in periods] != expected_numbers:
        raise HTTPException(422, "Harmonogram musí obsahovat všechna monitorovací období právě jednou.")
    expected_start = value.project_start_date.replace(day=1)
    project_end_month = value.project_end_date.replace(day=1)
    for item in periods:
        start_month = item.start_month.replace(day=1)
        end_month = item.end_month.replace(day=1)
        if start_month != item.start_month or end_month != item.end_month:
            raise HTTPException(422, "Měsíce období musí být uloženy jako první den měsíce.")
        if start_month != expected_start:
            raise HTTPException(422, f"{item.monitoring_period}. období nenavazuje na předchozí období.")
        if end_month < start_month:
            raise HTTPException(422, f"Konec {item.monitoring_period}. období je před jeho začátkem.")
        expected_start = next_month(end_month)
    if periods[-1].end_month != project_end_month:
        raise HTTPException(422, "Rozdělení období nepokrývá všechny měsíce až do konce projektu.")
    stored = value.model_dump(mode="json")
    repo.project_schedules[project_id] = stored
    if google_repo:
        google_repo.delete_records("HARMONOGRAM_PROJEKTU", "project_id", project_id)
        now = datetime.utcnow().isoformat()
        google_repo.append_records("HARMONOGRAM_PROJEKTU", [{
            "project_id": project_id,
            "project_start_date": value.project_start_date,
            "project_end_date": value.project_end_date,
            **item.model_dump(),
            "updated_at": now,
            "updated_by": user["email"],
        } for item in periods])
    return stored


@app.post("/api/projects/{project_id}/budgets/analyze")
async def analyze_budget(project_id: str, file: UploadFile = File(...), user=Depends(require_editor)):
    project(project_id, user); data = await checked_file(file, ".xlsx", {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"})
    try: result = parse_budget(data, file.filename)
    except Exception as exc: raise HTTPException(422, str(exc))
    token = str(uuid4()); result.token = token; analyses[token] = {"kind": "budget", "project_id": project_id, "data": data, "result": result}
    return result


@app.post("/api/projects/{project_id}/budgets/import")
def import_budget(project_id: str, body: dict, user=Depends(require_editor)):
    token = body.get("token"); cached = analyses.pop(token, None)
    if not cached or cached["kind"] != "budget" or cached["project_id"] != project_id: raise HTTPException(410, "Analýza vypršela nebo neexistuje.")
    result = cached["result"]
    if any(b["sha256"] == result.sha256 for b in repo.budgets[project_id]): raise HTTPException(409, "Tento soubor již byl importován.")
    version_id = str(uuid4()); repo.budgets[project_id].append({"version_id": version_id, "analysis": result, "sha256": result.sha256, "source": cached["data"]})
    p = project(project_id); p.total_budget = result.total_amount; p.active_budget_version_id = version_id; p.lump_sum_rate = result.lump_sum_rate or p.lump_sum_rate; p.lump_sum_base_code = result.lump_sum_base_code or p.lump_sum_base_code
    if google_repo:
        now = datetime.utcnow().isoformat()
        google_repo.append_records("VERZE_ROZPOCTU", [{"budget_version_id": version_id, "project_id": project_id,
            "version_number": len(repo.budgets[project_id]), "version_label": f"Verze {len(repo.budgets[project_id])}",
            "total_amount": result.total_amount, "source_file_name": result.file_name, "source_sha256": result.sha256,
            "is_active": True, "created_at": now, "created_by": user["email"]}])
        google_repo.append_records("POLOZKY_ROZPOCTU", [{**item.model_dump(), "budget_item_id": str(uuid4()),
            "project_id": project_id, "budget_version_id": version_id, "transfer_locked": False,
            "minimum_remaining_amount": 0, "planned_future_spending": 0, "donor_priority": 100} for item in result.items])
        google_repo.update_record("PROJEKTY", "project_id", project_id, {"total_budget": result.total_amount,
            "active_budget_version_id": version_id, "lump_sum_rate": p.lump_sum_rate, "lump_sum_base_code": p.lump_sum_base_code,
            "updated_at": now})
    return {"version_id": version_id, "imported_items": len(result.items)}


@app.get("/api/projects/{project_id}/budgets")
def budgets(project_id: str, user=Depends(current_user)):
    project(project_id, user)
    return [{"version_id": x["version_id"], **x["analysis"].model_dump(exclude={"items"})} for x in repo.budgets[project_id]]


@app.get("/api/projects/{project_id}/budgets/{version_id}")
def budget(project_id: str, version_id: str, user=Depends(current_user)):
    project(project_id, user)
    return next((x["analysis"] for x in repo.budgets[project_id] if x["version_id"] == version_id), None) or (_ for _ in ()).throw(HTTPException(404, "Verze rozpočtu nebyla nalezena."))


@app.delete("/api/projects/{project_id}/budgets/{version_id}", status_code=204)
def delete_budget(project_id: str, version_id: str, user=Depends(require_admin)):
    p = project(project_id, user)
    versions = repo.budgets[project_id]
    removed = next((value for value in versions if value["version_id"] == version_id), None)
    if not removed:
        raise HTTPException(404, "Verze rozpočtu nebyla nalezena.")
    versions.remove(removed)
    if p.active_budget_version_id == version_id:
        replacement = versions[-1] if versions else None
        p.active_budget_version_id = replacement["version_id"] if replacement else None
        p.total_budget = replacement["analysis"].total_amount if replacement else Decimal("0")
        if replacement:
            p.lump_sum_rate = replacement["analysis"].lump_sum_rate or p.lump_sum_rate
            p.lump_sum_base_code = replacement["analysis"].lump_sum_base_code or p.lump_sum_base_code
        p.updated_at = datetime.utcnow()
    if google_repo:
        google_repo.delete_records("POLOZKY_ROZPOCTU", "budget_version_id", version_id)
        google_repo.delete_record("VERZE_ROZPOCTU", "budget_version_id", version_id)
        google_repo.update_record("PROJEKTY", "project_id", project_id, {
            "active_budget_version_id": p.active_budget_version_id or "", "total_budget": p.total_budget,
            "lump_sum_rate": p.lump_sum_rate, "lump_sum_base_code": p.lump_sum_base_code,
            "updated_at": p.updated_at})


@app.get("/api/projects/{project_id}/budgets/{version_id}/download")
def download_budget(project_id: str, version_id: str, user=Depends(current_user)):
    analysis = budget(project_id, version_id, user)
    return StreamingResponse(BytesIO(export_with_formulas(analysis)), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="Rozpocet_se_vzorci.xlsx"'})


def _financial_plan_changes(plan: dict, sequence_number: int) -> dict | None:
    row = next((item for item in plan["rows"] if item["sequence_number"] == sequence_number), None)
    if not row:
        return None
    return {
        "financial_plan_coverage_actual": row["coverage_actual"],
        "financial_plan_settlement_actual": row["settlement_actual"],
        "financial_plan_state": row["state"],
        "financial_plan_source_file_name": plan["file_name"],
        "financial_plan_source_sha256": plan["sha256"],
    }


def _plain_status(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value or "")
                   if not unicodedata.combining(char)).lower()


def _provider_payment_from_plan(payment_item, funding_rate: Decimal) -> Decimal:
    """Resolve actual provider cash while preserving official cent rounding from the PDF."""
    status = payment_item.financial_plan_state or f"{payment_item.state} {payment_item.processing_state}"
    if not any(marker in _plain_status(status) for marker in ("proplacena", "vyporadana")):
        return Decimal("0")
    calculated = q(payment_item.financial_plan_coverage_actual * funding_rate)
    pdf_amount = payment_item.public_payment
    if pdf_amount > 0 and abs(pdf_amount - calculated) <= Decimal("0.02"):
        return pdf_amount
    return calculated


def _apply_financial_plan(project_id: str, plan: dict) -> int:
    applied = 0
    for payment_item in repo.payments[project_id]:
        changes = _financial_plan_changes(plan, payment_item.sequence_number)
        if not changes:
            continue
        for key, value in changes.items():
            setattr(payment_item, key, value)
        if google_repo:
            google_repo.update_record("ZADOSTI_O_PLATBU", "payment_request_id", payment_item.payment_request_id, changes)
        applied += 1
    return applied


@app.post("/api/projects/{project_id}/payment-requests/analyze")
async def analyze_payment(project_id: str, file: UploadFile = File(...), financial_plan: UploadFile | None = File(None), user=Depends(require_editor)):
    p = project(project_id); data = await checked_file(file, ".pdf", {"application/pdf", "application/octet-stream"})
    try: result = parse_payment_request(data, file.filename)
    except Exception as exc: raise HTTPException(422, str(exc))
    if p.project_code != result.project_code: raise HTTPException(422, "Žádost o platbu patří k jinému projektu.")
    plan = None
    if financial_plan and financial_plan.filename:
        plan_data = await checked_file(financial_plan, ".xlsx", {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"})
        try: plan = parse_financial_plan(plan_data, financial_plan.filename)
        except Exception as exc: raise HTTPException(422, str(exc))
        changes = _financial_plan_changes(plan, result.sequence_number)
        if not changes:
            raise HTTPException(422, f"Ve Finančním plánu chybí řádek pro ŽoP č. {result.sequence_number}.")
        plan_row = next(item for item in plan["rows"] if item["sequence_number"] == result.sequence_number)
        if bool(plan_row["is_final_payment"]) != bool(result.is_final_payment):
            raise HTTPException(422, "PDF ŽoP a Finanční plán se neshodují v označení závěrečné platby.")
        for key, value in changes.items():
            setattr(result, key, value)
    token = str(uuid4())
    analyses[token] = {"kind": "payment", "project_id": project_id, "result": result, "financial_plan": plan}
    provider_payment = (_provider_payment_from_plan(result, p.public_funding_rate)
                        if result.financial_plan_coverage_actual is not None else None)
    return {"token": token, "financial_plan_required": result.is_final_payment,
            "financial_plan_attached": plan is not None,
            "financial_plan_provider_payment": provider_payment, **result.model_dump()}


@app.post("/api/projects/{project_id}/payment-requests/import")
def import_payment(project_id: str, body: dict, user=Depends(require_editor)):
    cached = analyses.pop(body.get("token"), None)
    if not cached or cached["kind"] != "payment" or cached["project_id"] != project_id: raise HTTPException(410, "Analýza vypršela nebo neexistuje.")
    result = cached["result"]; existing = repo.payments[project_id]
    if result.is_final_payment and not cached.get("financial_plan"):
        raise HTTPException(422, "K závěrečné ŽoP je povinný také export XLSX z Finančního plánu v IS KP21+.")
    if any(x.source_sha256 == result.source_sha256 for x in existing): raise HTTPException(409, "Stejný PDF soubor již byl importován.")
    if any(x.request_number == result.request_number and x.request_version == result.request_version for x in existing): raise HTTPException(409, "Stejná verze ŽoP již byla importována.")
    if cached.get("financial_plan"):
        _apply_financial_plan(project_id, cached["financial_plan"])
    existing.append(result)
    if google_repo:
        values = result.model_dump(exclude={"lines"}); values.update(project_id=project_id, monitoring_period=max(1, result.sequence_number-1),
            approved_other_costs=0, active_revision=True, imported_at=datetime.utcnow().isoformat(), imported_by=user["email"])
        google_repo.append_records("ZADOSTI_O_PLATBU", [values])
        google_repo.append_records("RADKY_ZOP", [{**line.model_dump(), "payment_line_id": str(uuid4()),
            "payment_request_id": result.payment_request_id, "project_id": project_id} for line in result.lines])
    return result


@app.post("/api/projects/{project_id}/payment-requests/financial-plan")
async def import_financial_plan(project_id: str, file: UploadFile = File(...), user=Depends(require_editor)):
    project(project_id, user)
    data = await checked_file(file, ".xlsx", {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"})
    try: plan = parse_financial_plan(data, file.filename)
    except Exception as exc: raise HTTPException(422, str(exc))
    applied = _apply_financial_plan(project_id, plan)
    if not applied:
        raise HTTPException(422, "Finanční plán neobsahuje žádné pořadové číslo již importované ŽoP.")
    return {"source_file_name": plan["file_name"], "updated_payment_requests": applied, "rows": plan["rows"]}


@app.get("/api/projects/{project_id}/payment-requests")
def payments(project_id: str, user=Depends(current_user)):
    p = project(project_id, user)
    return [{**item.model_dump(),
             "financial_plan_provider_payment": (
                 _provider_payment_from_plan(item, p.public_funding_rate)
                 if item.financial_plan_coverage_actual is not None else None)}
            for item in repo.payments[project_id]]


@app.get("/api/projects/{project_id}/payment-requests/{request_id}")
def payment(project_id: str, request_id: str, user=Depends(current_user)):
    return next((x for x in repo.payments[project_id] if x.payment_request_id == request_id), None) or (_ for _ in ()).throw(HTTPException(404, "ŽoP nebyla nalezena."))


@app.post("/api/projects/{project_id}/lump-sum-spending", status_code=201)
def add_lump(project_id: str, entry: LumpSumEntry, user=Depends(require_editor)):
    project(project_id); repo.lump_entries[project_id].append(entry)
    if google_repo:
        metrics = lump_sum_metrics(Decimal("0"), Decimal("0"), Decimal("0"), repo.lump_entries[project_id])
        previous = metrics["spent"] - entry.entered_amount if entry.entry_mode == "period" else Decimal("0")
        google_repo.append_records("UTRATA_PAUSALU", [{**entry.model_dump(),
            "project_id": project_id, "calculated_period_delta": entry.entered_amount - previous if entry.entry_mode == "cumulative" else entry.entered_amount,
            "cumulative_spent": metrics["spent"], "created_at": datetime.utcnow().isoformat(), "created_by": user["email"]}])
    return entry


@app.get("/api/projects/{project_id}/lump-sum-spending")
def lump(project_id: str, user=Depends(current_user)): return repo.lump_entries[project_id]


@app.patch("/api/projects/{project_id}/lump-sum-spending/{entry_id}")
def patch_lump(project_id: str, entry_id: str, changes: dict, user=Depends(require_editor)):
    allowed = {k: v for k, v in changes.items() if k in {"monitoring_period", "entry_date", "entry_mode", "entered_amount", "note"}}
    entries = repo.lump_entries[project_id]
    index = next((i for i, entry in enumerate(entries) if entry.lump_sum_entry_id == entry_id), None)
    if index is None: raise HTTPException(404, "Záznam paušálu nebyl nalezen.")
    entries[index] = LumpSumEntry.model_validate({**entries[index].model_dump(), **allowed})
    if google_repo: google_repo.update_record("UTRATA_PAUSALU", "lump_sum_entry_id", entry_id, allowed)
    return entries[index]


@app.delete("/api/projects/{project_id}/lump-sum-spending/{entry_id}", status_code=204)
def delete_lump(project_id: str, entry_id: str, user=Depends(require_editor)):
    entries = repo.lump_entries[project_id]
    if not any(entry.lump_sum_entry_id == entry_id for entry in entries): raise HTTPException(404, "Záznam paušálu nebyl nalezen.")
    repo.lump_entries[project_id] = [entry for entry in entries if entry.lump_sum_entry_id != entry_id]
    if google_repo: google_repo.delete_record("UTRATA_PAUSALU", "lump_sum_entry_id", entry_id)


def cofinancing_target(project_id: str) -> Decimal:
    p = project(project_id)
    version = next((b for b in repo.budgets[project_id] if b["version_id"] == p.active_budget_version_id), None)
    if not version: return Decimal("0")
    direct_base = next((item.total_amount for item in version["analysis"].items if item.code == p.lump_sum_base_code), Decimal("0"))
    return (direct_base * (Decimal("1") - p.public_funding_rate)).quantize(Decimal("0.01"))


@app.get("/api/projects/{project_id}/cofinancing")
def get_cofinancing(project_id: str, user=Depends(current_user)):
    target = cofinancing_target(project_id); entries = repo.cofinancing_entries[project_id]
    secured = sum((x.amount for x in entries), Decimal("0")).quantize(Decimal("0.01"))
    return {"target": target, "secured": secured, "remaining": max(Decimal("0"), target-secured),
        "percentage": secured/target*100 if target else 0, "entries": entries}


@app.post("/api/projects/{project_id}/cofinancing", status_code=201)
def add_cofinancing(project_id: str, entry: CofinancingEntry, user=Depends(require_editor)):
    project(project_id); repo.cofinancing_entries[project_id].append(entry)
    if google_repo: google_repo.append_records("SPOLUFINANCOVANI", [{**entry.model_dump(), "project_id": project_id,
        "created_at": datetime.utcnow().isoformat(), "created_by": user["email"]}])
    return entry


@app.patch("/api/projects/{project_id}/cofinancing/{entry_id}")
def patch_cofinancing(project_id: str, entry_id: str, changes: dict, user=Depends(require_editor)):
    allowed = {k: v for k, v in changes.items() if k in {"entry_date", "amount", "note"}}
    entries = repo.cofinancing_entries[project_id]
    index = next((i for i, entry in enumerate(entries) if entry.cofinancing_entry_id == entry_id), None)
    if index is None: raise HTTPException(404, "Záznam spolufinancování nebyl nalezen.")
    entries[index] = CofinancingEntry.model_validate({**entries[index].model_dump(), **allowed})
    if google_repo: google_repo.update_record("SPOLUFINANCOVANI", "cofinancing_entry_id", entry_id, allowed)
    return entries[index]


@app.delete("/api/projects/{project_id}/cofinancing/{entry_id}", status_code=204)
def delete_cofinancing(project_id: str, entry_id: str, user=Depends(require_editor)):
    entries = repo.cofinancing_entries[project_id]
    if not any(entry.cofinancing_entry_id == entry_id for entry in entries): raise HTTPException(404, "Záznam spolufinancování nebyl nalezen.")
    repo.cofinancing_entries[project_id] = [entry for entry in entries if entry.cofinancing_entry_id != entry_id]
    if google_repo: google_repo.delete_record("SPOLUFINANCOVANI", "cofinancing_entry_id", entry_id)


SD2_PROJECT_CODE = "CZ.03.02.01/00/25_106/0006125"
SD2_CODES = {"1.1.1.1", "1.1.1.2", "1.1.1.3", "1.1.2.1", "1.1.3.1"}


def sd2_budget_items(project_id: str):
    p = repo.project_data[project_id]
    version = next((value for value in repo.budgets[project_id] if value["version_id"] == p.active_budget_version_id), None)
    if not version:
        return []
    return [item for item in version["analysis"].items
            if item.is_leaf and item.category == "direct" and re.match(r"^1\.1\.[123](?:\.|$)", item.code)]


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in value if not unicodedata.combining(char)).casefold()
    parts = [part for part in normalized.split() if part.strip(".,") not in {"bc", "mgr", "ing", "mudr", "judr", "phd", "phdr", "dis"}]
    return " ".join(parts)


def _mosty_payroll_rows(rows: list[dict], allowed: set[str]) -> list[dict]:
    """Convert payroll-list components into final SD-2 records for Mosty v rodině."""
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        person = normalized_name(f'{row.get("first_name", "")} {row.get("last_name", "")}')
        groups[(person, str(row["month"]), normalized_name(str(row.get("contract_name", ""))))].append(row)

    result: list[dict] = []

    def ignored(group: list[dict]) -> None:
        for source in group:
            value = dict(source); value["budget_item_code"] = "__ignore__"; value["match_status"] = "ignored"
            result.append(value)

    def final_row(source: dict, code: str, gross: Decimal, fund: Decimal, hours: Decimal,
                  target_wage: Decimal, correction: Decimal = Decimal("0"), note: str = "",
                  project_bonus_available: Decimal = Decimal("0"), project_bonus_label: str = "") -> None:
        value = dict(source)
        exact_insurance = source.get("contract_employer_insurance")
        insurance_rate = (Decimal(str(exact_insurance)) / gross
                          if exact_insurance is not None and gross else Decimal("0.338"))
        insurance = (target_wage * insurance_rate).quantize(Decimal("0.01"))
        vacation_hours = Decimal(str(source.get("vacation_hours", 0)))
        project_vacation_hours = ((vacation_hours * hours / fund).quantize(Decimal("0.01"))
                                  if fund else Decimal("0"))
        value.update({
            "source_key": f'{source["source_key"]}-sd2-{code}', "budget_item_code": code if code in allowed else "",
            "match_status": "matched" if code in allowed else "unmatched", "gross_wage": gross,
            "contract_gross": gross, "work_time_fund": fund, "project_hours": hours,
            "component_amount": target_wage, "other_with_contributions": correction,
            "employer_contributions": insurance,
            "employer_contribution_rate": insurance_rate,
            "project_bonus_available": project_bonus_available,
            "project_bonus_label": project_bonus_label,
            "project_vacation_hours": project_vacation_hours,
            "component_description": note or source.get("component_description", ""),
        })
        result.append(value)

    for (person, _, contract), group in groups.items():
        gross = max((Decimal(str(row.get("contract_gross", 0))) for row in group), default=Decimal("0"))
        fund = max((Decimal(str(row.get("work_time_fund", 0))) for row in group), default=Decimal("0"))
        full_fund = max((Decimal(str(row.get("full_time_fund", fund))) for row in group), default=fund)
        if person == normalized_name("Iva Holcová") and "dpc mosty v rodine" in contract:
            hours = max((Decimal(str(row.get("worked_hours", 0))) for row in group), default=Decimal("0"))
            final_row(group[0], "1.1.2.4", gross, hours, hours, gross, note="DPČ Mosty v rodině")
        elif person == normalized_name("Jana Sedlářová") and "ps mosty v rodine" in contract:
            final_row(group[0], "1.1.1.1", gross, fund, fund, gross, note="Celý pracovní poměr v projektu")
        elif person == normalized_name("Silvie Malíková") and contract == "ps":
            component = next((row for row in group if row.get("component_code") == "M01" and int(row.get("component_occurrence", 0)) == 3), None)
            if not component or not fund:
                ignored(group); continue
            bonus_components = [row for row in group if row.get("component_code") in {"C01", "M06"}
                                or any(token in normalized_name(str(row.get("component_name", "")))
                                       for token in ("premie", "odmena", "bonus", "osobni ohodnoceni"))]
            available_bonus = sum((Decimal(str(row.get("component_amount", 0))) for row in bonus_components), Decimal("0"))
            bonus_label = ", ".join(dict.fromkeys(str(row.get("component_name", "")).strip()
                                                     for row in bonus_components if str(row.get("component_name", "")).strip()))
            target = Decimal(str(component["component_amount"])); hours = (full_fund * Decimal("0.2")).quantize(Decimal("0.01"))
            calculated = (gross / fund * hours).quantize(Decimal("0.01"))
            final_row(component, "1.1.1.3", gross, fund, hours, target, target - calculated,
                      "Projektový HPP 0,2; korekce dorovnává odlišnou sazbu projektové části",
                      available_bonus, bonus_label)
        elif person == normalized_name("Martina Pírková") and "ps mosty v rodine" in contract:
            bonus = sum((Decimal(str(row["component_amount"])) for row in group if row.get("component_code") == "M06"), Decimal("0"))
            mapping = ((1, "1.1.1.7", Decimal("0.5")), (2, "1.1.1.5", Decimal("0.3")), (3, "1.1.1.4", Decimal("0.2")))
            for occurrence, code, share in mapping:
                component = next((row for row in group if row.get("component_code") == "M01" and int(row.get("component_occurrence", 0)) == occurrence), None)
                if not component:
                    continue
                target = Decimal(str(component["component_amount"])) + (bonus * share).quantize(Decimal("0.01"))
                hours = (full_fund * share).quantize(Decimal("0.01")); calculated = (gross / fund * hours).quantize(Decimal("0.01")) if fund else Decimal("0")
                final_row(component, code, gross, fund, hours, target, target - calculated,
                          f'Projektový podíl {int(share * 100)} % včetně podílu osobního ohodnocení')
        else:
            ignored(group)
    return result


@app.get("/api/projects/{project_id}/sd2-monthly")
def sd2_monthly(project_id: str, period: int, user=Depends(current_user)):
    project(project_id, user)
    return [entry for entry in repo.sd2_entries[project_id] if entry.monitoring_period == period]


@app.put("/api/projects/{project_id}/sd2-monthly")
def save_sd2_monthly(project_id: str, body: dict, user=Depends(require_editor)):
    project(project_id, user)
    entries = [Sd2MonthlyEntry.model_validate(value) for value in body.get("entries", [])]
    allowed_codes = {item.code for item in sd2_budget_items(project_id)}
    if not entries or any(entry.budget_item_code not in allowed_codes for entry in entries):
        raise HTTPException(422, "Neplatná položka podkladu SD2.")
    period = entries[0].monitoring_period
    if any(entry.monitoring_period != period for entry in entries): raise HTTPException(422, "Uložte najednou pouze jedno období.")
    existing = repo.sd2_entries[project_id]
    index = {(x.monitoring_period, x.month, x.budget_item_code): i for i, x in enumerate(existing)}
    appended = []
    for entry in entries:
        key = (entry.monitoring_period, entry.month, entry.budget_item_code)
        if key in index:
            entry.sd2_entry_id = existing[index[key]].sd2_entry_id
            existing[index[key]] = entry
            if google_repo: google_repo.update_record("SD2_MESICE", "sd2_entry_id", entry.sd2_entry_id, entry.model_dump())
        else:
            existing.append(entry); appended.append(entry)
    if google_repo and appended:
        google_repo.append_records("SD2_MESICE", [{**entry.model_dump(), "project_id": project_id, "created_at": datetime.utcnow().isoformat(), "created_by": user["email"]} for entry in appended])
    return [entry for entry in existing if entry.monitoring_period == period]


@app.delete("/api/projects/{project_id}/sd2-period", status_code=204)
def delete_sd2_period(project_id: str, period: int, user=Depends(require_editor)):
    project(project_id, user)
    entries = [entry for entry in repo.sd2_entries[project_id] if entry.monitoring_period == period]
    attachments = [value for value in repo.sd2_attachments[project_id]
                   if int(value.get("monitoring_period", 0)) == period]
    if google_repo:
        for entry in entries:
            google_repo.delete_record("SD2_MESICE", "sd2_entry_id", entry.sd2_entry_id)
        for attachment in attachments:
            google_repo.delete_record("SD2_PRILOHY", "attachment_id", str(attachment.get("attachment_id", "")))
    repo.sd2_entries[project_id] = [entry for entry in repo.sd2_entries[project_id]
                                    if entry.monitoring_period != period]
    repo.sd2_attachments[project_id] = [value for value in repo.sd2_attachments[project_id]
                                        if int(value.get("monitoring_period", 0)) != period]


@app.get("/api/projects/{project_id}/sd2-xml")
def download_sd2_xml(project_id: str, period: int, user=Depends(current_user)):
    project(project_id, user)
    entries = [entry for entry in repo.sd2_entries[project_id] if entry.monitoring_period == period]
    try:
        content = build_sd2_xml(entries)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="SD-2_obdobi_{period}.xml"'}
    return StreamingResponse(BytesIO(content), media_type="application/xml; charset=utf-8", headers=headers)


@app.post("/api/projects/{project_id}/sd2-xml")
def create_sd2_xml(project_id: str, period: int, body: dict, user=Depends(current_user)):
    project(project_id, user)
    entries = [Sd2MonthlyEntry.model_validate(value) for value in body.get("entries", [])]
    allowed_codes = {item.code for item in sd2_budget_items(project_id)}
    if any(entry.budget_item_code not in allowed_codes for entry in entries):
        raise HTTPException(422, "XML obsahuje neplatnou rozpočtovou položku.")
    if any(entry.monitoring_period != period for entry in entries):
        raise HTTPException(422, "XML lze vytvořit pouze pro jedno monitorovací období.")
    try:
        content = build_sd2_xml(entries)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="SD-2_obdobi_{period}.xml"'}
    return StreamingResponse(BytesIO(content), media_type="application/xml; charset=utf-8", headers=headers)


@app.post("/api/projects/{project_id}/payroll-slips/analyze")
async def analyze_payroll_slips(project_id: str, period: int, files: list[UploadFile] = File(...), user=Depends(require_editor)):
    selected_project = project(project_id, user)
    rows: list[dict] = []; insurance_rows: list[dict] = []; file_names: list[str] = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(415, "Vyberte pouze soubory PDF.")
        data = await file.read(MAX_SIZE + 1)
        if len(data) > MAX_SIZE:
            raise HTTPException(413, "Některý soubor je větší než povolených 20 MB.")
        file_names.append(file.filename)
        try:
            rows.extend(parse_payroll_slips(data))
        except ValueError:
            pass
        insurance_rows.extend(parse_payslip_insurance(data))
    if not rows:
        raise HTTPException(422, "Nebyl nalezen podporovaný výplatní list.")
    payslip_by_key = {(normalized_name(f'{item["first_name"]} {item["last_name"]}'), item["month"], normalized_name(item["contract_name"])): item for item in insurance_rows}
    for row in rows:
        key = (normalized_name(f'{row.get("first_name", "")} {row.get("last_name", "")}'), row["month"], normalized_name(str(row.get("contract_name", ""))))
        payslip = payslip_by_key.get(key)
        if payslip:
            row["contract_employer_insurance"] = payslip.get("employer_insurance", 0)
            row["vacation_hours"] = payslip.get("vacation_hours", row.get("vacation_hours", 0))
            fund = Decimal(str(row.get("work_time_fund", 0)))
            full_fund = Decimal(str(row.get("full_time_fund", 0)))
            daily_hours = fund / (full_fund / Decimal("8")) if fund and full_fund else Decimal("0")
            row["vacation_days"] = (Decimal(str(row["vacation_hours"])) / daily_hours).quantize(Decimal("0.01")) if daily_hours else Decimal("0")
    assignments = repo.worker_assignments[project_id]
    assignment_rules: dict[str, list[dict]] = defaultdict(list)
    for assignment in assignments:
        stored_name = str(assignment.get("employee_name", "")).strip()
        names = [stored_name] if stored_name else re.split(r"[,;\n]+", str(assignment.get("employee_names", "")))
        for name in names:
            if name.strip():
                rule = {
                    "budget_item_code": str(assignment.get("budget_item_code", "")),
                    "project_fte": assignment.get("project_fte"),
                    "payroll_component_amount": assignment.get("payroll_component_amount"),
                    "contract_contains": str(assignment.get("contract_contains", "")).strip(),
                }
                assignment_rules[normalized_name(name)].append(rule)
                reversed_name = normalized_name(" ".join(reversed(name.strip().split())))
                if reversed_name != normalized_name(name):
                    assignment_rules[reversed_name].append(rule)
    items = sd2_budget_items(project_id)
    allowed = {item.code for item in items}
    if normalized_name(selected_project.project_name) == normalized_name("Mosty v rodině"):
        rows = _mosty_payroll_rows(rows, allowed)
        return {"period": period, "file_name": ", ".join(file_names), "rows": rows,
                "budget_items": [{"code": item.code, "name": item.name} for item in items]}
    resolved_rows = []
    for row in rows:
        parsed_name = normalized_name(f'{row.get("first_name", "")} {row.get("last_name", "")}')
        candidates = assignment_rules.get(parsed_name, assignment_rules.get(normalized_name(row["full_name"]), []))
        contract_name = normalized_name(str(row.get("contract_name", "")))
        candidates = [rule for rule in candidates if not rule["contract_contains"] or normalized_name(rule["contract_contains"]) in contract_name]
        amount = Decimal(str(row.get("component_amount", row.get("gross_wage", 0))))
        exact = [rule for rule in candidates if str(rule.get("payroll_component_amount") or "").strip() and Decimal(str(rule["payroll_component_amount"])) == amount]
        without_amount = [rule for rule in candidates if not str(rule.get("payroll_component_amount") or "").strip()]
        matched_rule = exact[0] if len(exact) == 1 else (without_amount[0] if not exact and len(without_amount) == 1 and len(candidates) == 1 else None)
        matched = str(matched_rule["budget_item_code"]) if matched_rule else ""
        row["budget_item_code"] = matched if matched in allowed else ""
        row["match_status"] = "matched" if row["budget_item_code"] else "unmatched"
        if matched_rule and str(matched_rule.get("project_fte") or "").strip():
            fte = Decimal(str(matched_rule["project_fte"]))
            row["project_fte"] = fte
            row["project_hours"] = (Decimal(str(row.get("work_time_fund", 0))) * fte).quantize(Decimal("0.01"))
        resolved_rows.append(row)
    rows = resolved_rows
    return {"period": period, "file_name": ", ".join(file_names), "rows": rows,
            "budget_items": [{"code": item.code, "name": item.name} for item in items]}


@app.get("/api/projects/{project_id}/sd2-attachments")
def sd2_attachments(project_id: str, period: int, user=Depends(current_user)):
    project(project_id, user)
    return [value for value in repo.sd2_attachments[project_id] if int(value.get("monitoring_period", 0)) == period]


@app.post("/api/projects/{project_id}/sd2-attachments", status_code=201)
async def upload_sd2_attachment(project_id: str, period: int, file: UploadFile = File(...), user=Depends(require_editor)):
    project(project_id, user)
    if not file.filename or not file.filename.lower().endswith((".zip", ".rar")):
        raise HTTPException(415, "Povolen je pouze archiv ZIP nebo RAR.")
    data = await file.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE: raise HTTPException(413, "Soubor je větší než povolených 20 MB.")
    if not file_storage: raise HTTPException(503, "Uložiště souborů není nastavené.")
    try:
        drive_file_id = file_storage.upload(f"SD2_{project_id}_{period}_{file.filename}", data, file.content_type or "application/octet-stream")
    except Exception as exc:
        raise HTTPException(502, f"Archiv se nepodařilo uložit do Google Drive: {exc}")
    attachment = {"attachment_id": str(uuid4()), "project_id": project_id, "monitoring_period": period,
        "file_name": file.filename, "drive_file_id": drive_file_id,
        "uploaded_at": datetime.utcnow().isoformat(), "uploaded_by": user["email"]}
    repo.sd2_attachments[project_id].append(attachment)
    if google_repo: google_repo.append_records("SD2_PRILOHY", [attachment])
    return attachment


@app.post("/api/projects/{project_id}/sd2-attachments/record", status_code=201)
def record_sd2_attachment(project_id: str, period: int, value: Sd2AttachmentRecord, user=Depends(require_editor)):
    """Record an archive that the browser stored in the signed-in user's Google Drive.

    The archive itself never reaches this server or the service account.
    """
    project(project_id, user)
    if not value.file_name.lower().endswith((".zip", ".rar")):
        raise HTTPException(415, "Povolen je pouze archiv ZIP nebo RAR.")
    if not value.drive_file_id.strip():
        raise HTTPException(422, "Chybí identifikátor souboru na Google Drive.")
    attachment = {"attachment_id": str(uuid4()), "project_id": project_id, "monitoring_period": period,
        "file_name": value.file_name, "drive_file_id": value.drive_file_id.strip(),
        "uploaded_at": datetime.utcnow().isoformat(), "uploaded_by": user["email"]}
    repo.sd2_attachments[project_id].append(attachment)
    if google_repo:
        google_repo.append_records("SD2_PRILOHY", [attachment])
    return attachment


@app.get("/api/projects/{project_id}/dashboard")
def dashboard(project_id: str, user=Depends(current_user)):
    p = project(project_id); active = [x for x in repo.payments[project_id] if not x.is_advance_payment]
    direct = sum((x.approved_direct_costs for x in active), Decimal("0")); ls = sum((x.approved_lump_sum for x in active), Decimal("0")); spent = direct + ls
    ls_budget = next((i.total_amount for b in repo.budgets[project_id] for i in b["analysis"].items if b["version_id"] == p.active_budget_version_id and i.category == "lump_sum"), Decimal("0"))
    own_rate = Decimal("1") - p.public_funding_rate
    direct_cofinancing = (direct * own_rate).quantize(Decimal("0.01"))
    indirect_cofinancing = (ls * own_rate).quantize(Decimal("0.01"))
    cofinancing_total = (spent * own_rate).quantize(Decimal("0.01"))
    lump_sum_without_cofinancing = (ls_budget * p.public_funding_rate).quantize(Decimal("0.01"))
    # Haléřový rozdíl po dílčím zaokrouhlení připadne k nepřímým nákladům.
    indirect_cofinancing += cofinancing_total - direct_cofinancing - indirect_cofinancing
    return {"total_budget": p.total_budget, "approved_spending": spent, "remaining": p.total_budget-spent,
        "percentage": spent/p.total_budget*100 if p.total_budget else 0, "direct_approved": direct, "lump_sum_approved": ls,
        "own_funding_rate": own_rate, "direct_cofinancing": direct_cofinancing,
        "indirect_cofinancing": indirect_cofinancing, "cofinancing_total": cofinancing_total,
        "lump_sum_without_cofinancing": lump_sum_without_cofinancing,
        **lump_sum_metrics(ls_budget, direct, p.lump_sum_rate, repo.lump_entries[project_id])}


@app.get("/api/projects/{project_id}/budget-status")
def budget_status(project_id: str, version_id: str | None = None, user=Depends(current_user)):
    p = project(project_id, user); selected_version = version_id or p.active_budget_version_id
    version = next((b for b in repo.budgets[project_id] if b["version_id"] == selected_version), None)
    if not version: return []
    items = version["analysis"].items; by_code = {item.code: item for item in items}
    spent: dict[str, Decimal] = {code: Decimal("0") for code in by_code}; periods: dict[str, dict[str, Decimal]] = {code: {} for code in by_code}
    for payment in repo.payments[project_id]:
        if payment.is_advance_payment: continue
        period = str(max(1, payment.sequence_number - 1))
        for line in payment.lines:
            code = line.budget_item_code or ""
            if code not in by_code:
                code = extract_budget_code(line.budget_item_name_raw or "") or code
            if code in by_code:
                spent[code] += line.approved_amount
                periods[code][period] = periods[code].get(period, Decimal("0")) + line.approved_amount
        lump_item = next((item for item in items if item.category == "lump_sum"), None)
        if lump_item:
            spent[lump_item.code] += payment.approved_lump_sum
            periods[lump_item.code][period] = periods[lump_item.code].get(period, Decimal("0")) + payment.approved_lump_sum
    submitted_periods = {max(1, payment.sequence_number - 1) for payment in repo.payments[project_id] if not payment.is_advance_payment}
    for entry in repo.sd2_entries[project_id]:
        if entry.monitoring_period in submitted_periods or entry.budget_item_code not in spent:
            continue
        amount = entry.total_amount
        spent[entry.budget_item_code] += amount
        period = str(entry.monitoring_period)
        periods[entry.budget_item_code][period] = periods[entry.budget_item_code].get(period, Decimal("0")) + amount
    # Souhrny jsou vždy odvozené z bezprostředních potomků.
    for item in sorted(items, key=lambda x: x.level, reverse=True):
        if item.parent_code in spent:
            spent[item.parent_code] += spent[item.code]
            for period, amount in periods[item.code].items(): periods[item.parent_code][period] = periods[item.parent_code].get(period, Decimal("0")) + amount
    def change_info(item):
        if not item.is_leaf:
            return {"has_budget_change": False, "change_note": ""}
        if item.is_new:
            return {"has_budget_change": True, "change_note": "Nová položka v této verzi rozpočtu."}
        if item.previous_amount is not None and item.previous_amount != item.total_amount:
            previous = f"{item.previous_amount:,.2f}".replace(",", " ").replace(".", ",")
            difference = item.total_amount - item.previous_amount
            difference_text = f"{difference:+,.2f}".replace(",", " ").replace(".", ",")
            return {"has_budget_change": True, "change_note": f"Původně {previous} Kč, změna {difference_text} Kč."}
        return {"has_budget_change": False, "change_note": ""}

    return [{**item.model_dump(), **change_info(item),
        "budget_version_id": version["version_id"], "cumulative_spent": spent[item.code],
        "remaining": item.total_amount-spent[item.code], "spent_percent": spent[item.code]/item.total_amount*100 if item.total_amount else 0,
        "expected_final_remaining": item.total_amount-spent[item.code]-item.planned_future_spending, "periods": periods[item.code]}
        for item in items]


@app.get("/api/projects/{project_id}/budget-status.xlsx")
def download_budget_status(project_id: str, version_id: str | None = None, user=Depends(current_user)):
    p = project(project_id, user)
    selected_version = version_id or p.active_budget_version_id
    version = next((value for value in repo.budgets[project_id] if value["version_id"] == selected_version), None)
    if not version:
        raise HTTPException(404, "Verze rozpočtu nebyla nalezena.")
    items = version["analysis"].items
    by_code = {item.code: item for item in items}
    monthly: dict[str, dict[str, Decimal]] = {code: {} for code in by_code}
    for entry in repo.sd2_entries[project_id]:
        if entry.budget_item_code not in monthly or not entry.total_amount:
            continue
        month = entry.month.isoformat()
        monthly[entry.budget_item_code][month] = monthly[entry.budget_item_code].get(month, Decimal("0")) + entry.total_amount
    for item in sorted(items, key=lambda value: value.level, reverse=True):
        if item.parent_code not in monthly:
            continue
        for month, amount in monthly[item.code].items():
            monthly[item.parent_code][month] = monthly[item.parent_code].get(month, Decimal("0")) + amount
    rows = budget_status(project_id, selected_version, user)
    content = export_budget_status(rows, monthly)
    headers = {"Content-Disposition": 'attachment; filename="Cerpani_rozpoctu_mesicne.xlsx"'}
    return StreamingResponse(BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


@app.get("/api/projects/{project_id}/worker-assignments")
def worker_assignments(project_id: str, user=Depends(current_user)):
    project(project_id, user)
    return repo.worker_assignments[project_id]


@app.put("/api/projects/{project_id}/worker-assignments")
def save_worker_assignments(project_id: str, body: dict, user=Depends(require_editor)):
    project(project_id, user)
    records = []
    for item in body.get("assignments", []):
        code = str(item.get("budget_item_code", "")).strip()
        name = str(item.get("employee_name") or item.get("employee_names", "")).strip()
        fte_raw = item.get("project_fte")
        amount_raw = item.get("payroll_component_amount")
        try:
            fte = Decimal(str(fte_raw)) if str(fte_raw or "").strip() else None
            amount = Decimal(str(amount_raw)) if str(amount_raw or "").strip() else None
        except Exception as exc:
            raise HTTPException(422, "ProjektovĂ˝ Ăşvazek a mzdovĂˇ sloĹľka musĂ­ bĂ˝t ÄŤĂ­sla.") from exc
        if fte is not None and not Decimal("0") <= fte <= Decimal("1"):
            raise HTTPException(422, "ProjektovĂ˝ Ăşvazek musĂ­ bĂ˝t od 0 do 1.")
        if amount is not None and amount < 0:
            raise HTTPException(422, "MzdovĂˇ sloĹľka nesmĂ­ bĂ˝t zĂˇpornĂˇ.")
        if code and name:
            records.append({"worker_assignment_id": str(uuid4()), "project_id": project_id,
                "budget_item_code": code, "employee_names": name,
                "updated_at": datetime.utcnow().isoformat(), "updated_by": user["email"],
                "employee_name": name, "project_fte": fte, "payroll_component_amount": amount,
                "contract_contains": str(item.get("contract_contains", "")).strip()})
    repo.worker_assignments[project_id] = records
    if google_repo:
        google_repo.delete_records("PRACOVNICI_ROZPOCTU", "project_id", project_id)
        if records:
            google_repo.append_records("PRACOVNICI_ROZPOCTU", records)
    return records


@app.post("/api/projects/{project_id}/budget-change/analyze")
async def analyze_budget_change(project_id: str, file: UploadFile = File(...), user=Depends(require_editor)):
    p = project(project_id); data = await checked_file(file, ".xlsx", {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"})
    try: result = parse_budget(data, file.filename)
    except Exception as exc: raise HTTPException(422, str(exc))
    current = next((b["analysis"] for b in repo.budgets[project_id] if b["version_id"] == p.active_budget_version_id), None)
    if not current: raise HTTPException(409, "Projekt nemá aktivní rozpočet.")
    old = {x.code: x for x in current.items}; status = {x["code"]: x for x in budget_status(project_id, user=user)}; changes = []
    for item in result.items:
        item.is_new = item.code not in old
        item.previous_amount = old[item.code].total_amount if item.code in old else None
    for code in sorted(set(old) | {x.code for x in result.items}):
        before = old.get(code); after = next((x for x in result.items if x.code == code), None)
        old_amount = before.total_amount if before else Decimal("0"); new_amount = after.total_amount if after else Decimal("0")
        kind = "nová" if not before else "odstraněná" if not after else "přejmenovaná" if before.name != after.name and old_amount == new_amount else "zvýšena" if new_amount > old_amount else "snížena" if new_amount < old_amount else "beze změny"
        spent_value = Decimal(str(status.get(code, {}).get("cumulative_spent", 0)))
        changes.append({"code": code, "name": (after or before).name, "old_amount": old_amount, "new_amount": new_amount,
            "difference": new_amount-old_amount, "status": kind, "spent": spent_value, "new_remaining": new_amount-spent_value})
    errors = validate_budget_structure(result)
    if result.total_amount != current.total_amount: errors.append("Celková částka změnového rozpočtu musí být přesně shodná s aktivní verzí, včetně haléřů.")
    errors += [f"Položku {x['code']} nelze snížit pod dosavadní čerpání." for x in changes if x["new_amount"] < x["spent"]]
    token = str(uuid4()); result.token = token; analyses[token] = {"kind": "budget_change", "project_id": project_id, "data": data, "result": result}
    return {"token": token, "total_amount": result.total_amount, "current_total": current.total_amount, "changes": changes, "errors": errors, "warnings": result.warnings}


@app.post("/api/projects/{project_id}/budget-change/import")
def import_budget_change(project_id: str, body: dict, user=Depends(require_editor)):
    cached = analyses.get(body.get("token"))
    if not cached or cached["kind"] != "budget_change": raise HTTPException(410, "Analýza změnového rozpočtu vypršela.")
    p = project(project_id); current = next(b["analysis"] for b in repo.budgets[project_id] if b["version_id"] == p.active_budget_version_id)
    result = cached["result"]
    structure_errors = validate_budget_structure(result)
    if structure_errors:
        raise HTTPException(422, " ".join(structure_errors))
    if result.total_amount != current.total_amount: raise HTTPException(422, "Změnový rozpočet nemá přesně stejnou celkovou částku, včetně haléřů.")
    cached["kind"] = "budget"
    return import_budget(project_id, body, user)


@app.post("/api/projects/{project_id}/change-proposals/generate")
def generate_proposal(project_id: str, body: dict, user=Depends(require_editor)):
    project(project_id)
    # Přesouvat lze pouze mezi skutečnými koncovými položkami přímých
    # výdajů. Součtové/informační řádky (např. kód 3), paušál a
    # nezpůsobilé výdaje nejsou samostatným zdrojem rozpočtového přesunu.
    items = [TransferCandidate(code=x["code"], budget=x["total_amount"], spent=x["cumulative_spent"],
        planned=x["planned_future_spending"], minimum_remaining=x["minimum_remaining_amount"],
        locked=x["transfer_locked"], donor_priority=x["donor_priority"], unit_count=x.get("unit_count")) for x in budget_status(project_id, user=user)
        if x["is_leaf"] and x["category"] == "direct" and x["code"].startswith("1.1.")]
    transfers = propose_transfers(items, Decimal(str(body.get("reserve_rate", 0))))
    deficits = [{"code": x.code, "amount": x.spent-x.budget} for x in items if x.spent > x.budget]
    total_transfer = sum((x.amount for x in transfers), Decimal("0"))
    total_deficit = sum((x["amount"] for x in deficits), Decimal("0"))
    transfer_reserve = total_transfer - total_deficit if total_transfer >= total_deficit else Decimal("0")
    balanced = total_transfer >= total_deficit
    proposal_id = str(uuid4())
    p = project(project_id)
    active = next((b["analysis"] for b in repo.budgets[project_id] if b["version_id"] == p.active_budget_version_id), None)
    deltas: dict[str, Decimal] = {}
    for transfer in transfers:
        deltas[transfer.source_code] = deltas.get(transfer.source_code, Decimal("0")) - transfer.amount
        deltas[transfer.target_code] = deltas.get(transfer.target_code, Decimal("0")) + transfer.amount
    feasibility_errors = []
    if active:
        for item in active.items:
            if item.code not in deltas or not item.unit_count or item.unit_count <= 0:
                continue
            if item.unit_count != item.unit_count.quantize(Decimal("0.01")):
                feasibility_errors.append(
                    f"Položka {item.code}: původní počet jednotek má více než dvě desetinná místa.")
                continue
            proposed_total = item.total_amount + deltas[item.code]
            proposed_unit_price = proposed_total / item.unit_count
            if proposed_unit_price != proposed_unit_price.quantize(Decimal("0.01")):
                feasibility_errors.append(
                    f"Položka {item.code}: při nezměněném počtu {item.unit_count} nelze novou cenu za jednotku vyjádřit na haléře.")
    feasible = balanced and not feasibility_errors
    analyses[proposal_id] = {"kind": "transfer_proposal", "project_id": project_id,
        "analysis": active, "transfers": transfers, "balanced": balanced, "feasible": feasible}
    return {"proposal_id": proposal_id, "deficits": deficits, "transfers": transfers,
            "total_transfer": total_transfer, "balanced": balanced, "feasible": feasible,
            "feasibility_errors": feasibility_errors, "transfer_reserve": transfer_reserve}


@app.get("/api/projects/{project_id}/change-proposals/{proposal_id}/download")
def download_proposal(project_id: str, proposal_id: str, user=Depends(require_editor)):
    project(project_id, user)
    cached = analyses.get(proposal_id)
    if not cached or cached.get("kind") != "transfer_proposal" or cached.get("project_id") != project_id:
        raise HTTPException(404, "Návrh přesunů nebyl nalezen. Vytvořte jej znovu.")
    if not cached.get("analysis") or not cached.get("transfers") or not cached.get("balanced"):
        raise HTTPException(409, "Návrh nepokrývá celý deficit bezpečnými přesuny.")
    data = export_transfer_proposal(cached["analysis"], cached["transfers"])
    return StreamingResponse(BytesIO(data), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Navrh_zmeny_rozpoctu.xlsx"'})


def _normalized_status(value: str) -> str:
    return _plain_status(value)


def _payment_is_approved(payment) -> bool:
    status = _normalized_status(f"{payment.state} {payment.processing_state}")
    return any(marker in status for marker in ("proplacena", "vyporadana", "schvalena"))


def _payment_is_paid(payment) -> bool:
    status = _normalized_status(f"{payment.state} {payment.processing_state}")
    return any(marker in status for marker in ("proplacena", "vyporadana"))


def _settlement_breakdown(project_id: str, user: dict) -> dict:
    p = project(project_id, user)
    rows = []
    approved_direct = approved_lump = approved_income = Decimal("0")
    projected_direct = projected_lump = projected_income = Decimal("0")
    received = initial_advance = Decimal("0")
    final_requests = []

    for payment in sorted(repo.payments[project_id], key=lambda item: item.sequence_number):
        approved = _payment_is_approved(payment)
        paid = _payment_is_paid(payment)
        declared_direct = payment.declared_direct_costs
        declared_lump = payment.declared_lump_sum
        declared_total = declared_direct + declared_lump
        recognized_direct = payment.approved_direct_costs if approved and not payment.is_advance_payment else Decimal("0")
        recognized_lump = payment.approved_lump_sum if approved and not payment.is_advance_payment else Decimal("0")
        recognized_total = recognized_direct + recognized_lump
        projected_row_total = recognized_total if approved else (declared_total if not payment.is_advance_payment else Decimal("0"))
        received_payment = (_provider_payment_from_plan(payment, p.public_funding_rate)
                            if payment.financial_plan_coverage_actual is not None
                            else (payment.public_payment if paid else Decimal("0")))

        approved_direct += recognized_direct
        approved_lump += recognized_lump
        if approved and not payment.is_advance_payment:
            approved_income += payment.clean_other_income
        projected_direct += recognized_direct if approved else (declared_direct if not payment.is_advance_payment else Decimal("0"))
        projected_lump += recognized_lump if approved else (declared_lump if not payment.is_advance_payment else Decimal("0"))
        if not payment.is_advance_payment:
            projected_income += payment.clean_other_income
        received += received_payment
        if payment.is_advance_payment:
            initial_advance += received_payment
        if payment.is_final_payment:
            final_requests.append(payment)

        if payment.is_advance_payment:
            explanation = "Úvodní záloha se nezapočítává do čerpání rozpočtu, ale započítává se mezi přijaté platby."
            payment_type = "Úvodní záloha"
        elif approved:
            explanation = "Schválené výdaje se započítávají do čerpání; částka na krytí výdajů se započítává mezi přijaté platby."
            payment_type = "Závěrečná ŽoP" if payment.is_final_payment else "Vyúčtování"
        else:
            explanation = "ŽoP dosud není schválena. Prokazované výdaje jsou použity pouze v orientačním scénáři."
            payment_type = "Závěrečná ŽoP" if payment.is_final_payment else "Neschválená ŽoP"
        rows.append({
            "sequence_number": payment.sequence_number, "status": payment.processing_state or payment.state,
            "type": payment_type, "source_file_name": payment.source_file_name,
            "is_advance_payment": payment.is_advance_payment, "is_final_payment": payment.is_final_payment,
            "is_approved": approved, "is_paid": paid,
            "declared_direct": declared_direct, "declared_lump_sum": declared_lump,
            "declared_total": declared_total, "approved_direct": recognized_direct,
            "approved_lump_sum": recognized_lump, "approved_total": recognized_total,
            "clean_other_income": payment.clean_other_income,
            "pdf_public_payment": payment.public_payment,
            "financial_plan_coverage_actual": payment.financial_plan_coverage_actual,
            "financial_plan_settlement_actual": payment.financial_plan_settlement_actual,
            "financial_plan_source_file_name": payment.financial_plan_source_file_name,
            "received_payment": received_payment, "approved_for_settlement": recognized_total,
            "projected_for_settlement": projected_row_total, "explanation": explanation,
        })

    approved_result = final_settlement(approved_direct, approved_lump, Decimal("0"), approved_income,
                                       p.public_funding_rate, received, Decimal("0"))
    projected_result = final_settlement(projected_direct, projected_lump, Decimal("0"), projected_income,
                                        p.public_funding_rate, received, Decimal("0"))
    return {
        **projected_result,
        "approved_eligible_total": approved_result["eligible_total"],
        "approved_provider_entitlement": approved_result["provider_entitlement"],
        "current_settlement": approved_result["settlement"],
        "submitted_pending_total": projected_result["eligible_total"] - approved_result["eligible_total"],
        "initial_advance": initial_advance, "net_received": received, "rows": rows,
        "orientacni": not final_requests or any(not _payment_is_approved(item) for item in final_requests),
        "has_final_payment": bool(final_requests),
        "final_payment_approved": bool(final_requests) and all(_payment_is_approved(item) for item in final_requests),
    }


@app.get("/api/projects/{project_id}/final-settlement")
def settlement(project_id: str, user=Depends(current_user)):
    return _settlement_breakdown(project_id, user)


@app.get("/api/projects/{project_id}/final-settlement.xlsx")
def download_final_settlement(project_id: str, user=Depends(current_user)):
    p = project(project_id, user)
    breakdown = _settlement_breakdown(project_id, user)
    content = export_final_settlement({
        "project_name": p.project_name, "project_code": p.project_code,
        "total_budget": p.total_budget, "public_funding_rate": p.public_funding_rate,
    }, breakdown)
    return StreamingResponse(BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Vypocet_zaverecneho_vyporadani.xlsx"'})


@app.get("/api/import-log")
def import_log(user=Depends(current_user)): return repo.import_log


static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    assets_dir = os.path.join(static_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_fallback(full_path: str):
        """React Router fallback pro přímé odkazy a obnovení vnořených stránek."""
        requested = os.path.abspath(os.path.join(static_dir, full_path))
        root = os.path.abspath(static_dir)
        if requested.startswith(root + os.sep) and os.path.isfile(requested):
            return FileResponse(requested)
        return FileResponse(os.path.join(static_dir, "index.html"))
