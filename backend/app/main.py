from __future__ import annotations

import hashlib
import os
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests
from google.oauth2 import id_token

from .calculations import final_settlement, lump_sum_metrics, propose_transfers
from .models import CofinancingEntry, LumpSumEntry, Project, ProjectCreate, TransferCandidate
from .pdf_parser import parse_payment_request
from .repository import GoogleSheetsRepository, InMemoryRepository
from .xlsx_parser import export_with_formulas, parse_budget

app = FastAPI(title="Sledovač čerpání rozpočtu OPZ+", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_methods=["*"], allow_headers=["*"])
repo = InMemoryRepository()
google_repo = None
user_roles: dict[str, str] = {}
active_user: ContextVar[dict | None] = ContextVar("active_user", default=None)
if os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") and os.getenv("GOOGLE_SPREADSHEET_ID"):
    google_repo = GoogleSheetsRepository(os.environ["GOOGLE_SPREADSHEET_ID"], os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    google_repo.ensure_schema()
    google_repo.hydrate(repo)
    user_roles = {str(row.get("email", "")).lower(): str(row.get("role", "user"))
                  for row in google_repo._records("USERS") if google_repo._bool(row.get("active", True))}
analyses: dict[str, dict] = {}
MAX_SIZE = 20 * 1024 * 1024


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
    allowed = repo.project_access.get(project_id, set())
    return not allowed or user.get("email", "").lower() in allowed


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


@app.get("/api/projects")
def get_projects(user=Depends(current_user)): return [p for p in repo.projects() if can_view_project(p.project_id, user)]


@app.post("/api/projects", status_code=201)
def create_project(data: ProjectCreate, user=Depends(require_admin)):
    value = Project(**data.model_dump()); repo.save_project(value); return value


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user=Depends(current_user)): return project(project_id, user)


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, changes: dict, user=Depends(require_admin)):
    value = project(project_id, user).model_copy(update={k: v for k, v in changes.items() if k in Project.model_fields})
    value.updated_at = datetime.utcnow(); repo.save_project(value); return value


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
    return [{"version_id": x["version_id"], **x["analysis"].model_dump(exclude={"items"})} for x in repo.budgets[project_id]]


@app.get("/api/projects/{project_id}/budgets/{version_id}")
def budget(project_id: str, version_id: str, user=Depends(current_user)):
    return next((x["analysis"] for x in repo.budgets[project_id] if x["version_id"] == version_id), None) or (_ for _ in ()).throw(HTTPException(404, "Verze rozpočtu nebyla nalezena."))


@app.get("/api/projects/{project_id}/budgets/{version_id}/download")
def download_budget(project_id: str, version_id: str, user=Depends(current_user)):
    analysis = budget(project_id, version_id, user)
    return StreamingResponse(BytesIO(export_with_formulas(analysis)), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="Rozpocet_se_vzorci.xlsx"'})


@app.post("/api/projects/{project_id}/payment-requests/analyze")
async def analyze_payment(project_id: str, file: UploadFile = File(...), user=Depends(require_editor)):
    p = project(project_id); data = await checked_file(file, ".pdf", {"application/pdf", "application/octet-stream"})
    try: result = parse_payment_request(data, file.filename)
    except Exception as exc: raise HTTPException(422, str(exc))
    if p.project_code != result.project_code: raise HTTPException(422, "Žádost o platbu patří k jinému projektu.")
    token = str(uuid4()); analyses[token] = {"kind": "payment", "project_id": project_id, "result": result}; return {"token": token, **result.model_dump()}


@app.post("/api/projects/{project_id}/payment-requests/import")
def import_payment(project_id: str, body: dict, user=Depends(require_editor)):
    cached = analyses.pop(body.get("token"), None)
    if not cached or cached["kind"] != "payment" or cached["project_id"] != project_id: raise HTTPException(410, "Analýza vypršela nebo neexistuje.")
    result = cached["result"]; existing = repo.payments[project_id]
    if any(x.source_sha256 == result.source_sha256 for x in existing): raise HTTPException(409, "Stejný PDF soubor již byl importován.")
    if any(x.request_number == result.request_number and x.request_version == result.request_version for x in existing): raise HTTPException(409, "Stejná verze ŽoP již byla importována.")
    existing.append(result)
    if google_repo:
        values = result.model_dump(exclude={"lines"}); values.update(project_id=project_id, monitoring_period=max(1, result.sequence_number-1),
            approved_other_costs=0, active_revision=True, imported_at=datetime.utcnow().isoformat(), imported_by=user["email"])
        google_repo.append_records("ZADOSTI_O_PLATBU", [values])
        google_repo.append_records("RADKY_ZOP", [{**line.model_dump(), "payment_line_id": str(uuid4()),
            "payment_request_id": result.payment_request_id, "project_id": project_id} for line in result.lines])
    return result


@app.get("/api/projects/{project_id}/payment-requests")
def payments(project_id: str, user=Depends(current_user)): return repo.payments[project_id]


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


@app.get("/api/projects/{project_id}/dashboard")
def dashboard(project_id: str, user=Depends(current_user)):
    p = project(project_id); active = [x for x in repo.payments[project_id] if not x.is_advance_payment]
    direct = sum((x.approved_direct_costs for x in active), Decimal("0")); ls = sum((x.approved_lump_sum for x in active), Decimal("0")); spent = direct + ls
    ls_budget = next((i.total_amount for b in repo.budgets[project_id] for i in b["analysis"].items if b["version_id"] == p.active_budget_version_id and i.category == "lump_sum"), Decimal("0"))
    own_rate = Decimal("1") - p.public_funding_rate
    direct_cofinancing = (direct * own_rate).quantize(Decimal("0.01"))
    indirect_cofinancing = (ls * own_rate).quantize(Decimal("0.01"))
    cofinancing_total = (spent * own_rate).quantize(Decimal("0.01"))
    # Haléřový rozdíl po dílčím zaokrouhlení připadne k nepřímým nákladům.
    indirect_cofinancing += cofinancing_total - direct_cofinancing - indirect_cofinancing
    return {"total_budget": p.total_budget, "approved_spending": spent, "remaining": p.total_budget-spent,
        "percentage": spent/p.total_budget*100 if p.total_budget else 0, "direct_approved": direct, "lump_sum_approved": ls,
        "own_funding_rate": own_rate, "direct_cofinancing": direct_cofinancing,
        "indirect_cofinancing": indirect_cofinancing, "cofinancing_total": cofinancing_total,
        **lump_sum_metrics(ls_budget, direct, p.lump_sum_rate, repo.lump_entries[project_id])}


@app.get("/api/projects/{project_id}/budget-status")
def budget_status(project_id: str, user=Depends(current_user)):
    p = project(project_id); version = next((b for b in repo.budgets[project_id] if b["version_id"] == p.active_budget_version_id), None)
    if not version: return []
    items = version["analysis"].items; by_code = {item.code: item for item in items}
    spent: dict[str, Decimal] = {code: Decimal("0") for code in by_code}; periods: dict[str, dict[str, Decimal]] = {code: {} for code in by_code}
    for payment in repo.payments[project_id]:
        if payment.is_advance_payment: continue
        period = str(max(1, payment.sequence_number - 1))
        for line in payment.lines:
            code = line.budget_item_code or ""
            if code in by_code:
                spent[code] += line.approved_amount
                periods[code][period] = periods[code].get(period, Decimal("0")) + line.approved_amount
        lump_item = next((item for item in items if item.category == "lump_sum"), None)
        if lump_item:
            spent[lump_item.code] += payment.approved_lump_sum
            periods[lump_item.code][period] = periods[lump_item.code].get(period, Decimal("0")) + payment.approved_lump_sum
    # Souhrny jsou vždy odvozené z bezprostředních potomků.
    for item in sorted(items, key=lambda x: x.level, reverse=True):
        if item.parent_code in spent:
            spent[item.parent_code] += spent[item.code]
            for period, amount in periods[item.code].items(): periods[item.parent_code][period] = periods[item.parent_code].get(period, Decimal("0")) + amount
    return [{**item.model_dump(), "budget_version_id": version["version_id"], "cumulative_spent": spent[item.code],
        "remaining": item.total_amount-spent[item.code], "spent_percent": spent[item.code]/item.total_amount*100 if item.total_amount else 0,
        "expected_final_remaining": item.total_amount-spent[item.code]-item.planned_future_spending, "periods": periods[item.code]}
        for item in items]


@app.post("/api/projects/{project_id}/budget-change/analyze")
async def analyze_budget_change(project_id: str, file: UploadFile = File(...), user=Depends(require_editor)):
    p = project(project_id); data = await checked_file(file, ".xlsx", {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"})
    try: result = parse_budget(data, file.filename)
    except Exception as exc: raise HTTPException(422, str(exc))
    current = next((b["analysis"] for b in repo.budgets[project_id] if b["version_id"] == p.active_budget_version_id), None)
    if not current: raise HTTPException(409, "Projekt nemá aktivní rozpočet.")
    old = {x.code: x for x in current.items}; status = {x["code"]: x for x in budget_status(project_id, user)}; changes = []
    for code in sorted(set(old) | {x.code for x in result.items}):
        before = old.get(code); after = next((x for x in result.items if x.code == code), None)
        old_amount = before.total_amount if before else Decimal("0"); new_amount = after.total_amount if after else Decimal("0")
        kind = "nová" if not before else "odstraněná" if not after else "přejmenovaná" if before.name != after.name and old_amount == new_amount else "zvýšena" if new_amount > old_amount else "snížena" if new_amount < old_amount else "beze změny"
        spent_value = Decimal(str(status.get(code, {}).get("cumulative_spent", 0)))
        changes.append({"code": code, "name": (after or before).name, "old_amount": old_amount, "new_amount": new_amount,
            "difference": new_amount-old_amount, "status": kind, "spent": spent_value, "new_remaining": new_amount-spent_value})
    errors = []
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
    if result.total_amount != current.total_amount: raise HTTPException(422, "Změnový rozpočet nemá přesně stejnou celkovou částku, včetně haléřů.")
    cached["kind"] = "budget"
    return import_budget(project_id, body, user)


@app.post("/api/projects/{project_id}/change-proposals/generate")
def generate_proposal(project_id: str, body: dict, user=Depends(require_editor)):
    project(project_id); supplied = body.get("items", [])
    if supplied: items = [TransferCandidate(**x) for x in supplied]
    else:
        items = [TransferCandidate(code=x["code"], budget=x["total_amount"], spent=x["cumulative_spent"],
            planned=x["planned_future_spending"], minimum_remaining=x["minimum_remaining_amount"],
            locked=x["transfer_locked"], donor_priority=x["donor_priority"]) for x in budget_status(project_id, user)
            if x["is_leaf"] and x["category"] != "lump_sum"]
    transfers = propose_transfers(items, Decimal(str(body.get("reserve_rate", 0))))
    deficits = [{"code": x.code, "amount": x.spent-x.budget} for x in items if x.spent > x.budget]
    return {"proposal_id": str(uuid4()), "deficits": deficits, "transfers": transfers,
            "total_transfer": sum((x.amount for x in transfers), Decimal("0")), "balanced": True}


@app.get("/api/projects/{project_id}/final-settlement")
def settlement(project_id: str, user=Depends(current_user)):
    p = project(project_id); values = [x for x in repo.payments[project_id] if not x.is_advance_payment]
    direct = sum((x.approved_direct_costs for x in values), Decimal("0")); ls = sum((x.approved_lump_sum for x in values), Decimal("0")); income = sum((x.clean_other_income for x in values), Decimal("0")); received = sum((x.public_payment for x in repo.payments[project_id]), Decimal("0"))
    return {**final_settlement(direct, ls, Decimal("0"), income, p.public_funding_rate, received, Decimal("0")), "orientacni": True,
        "has_final_payment": any(x.is_final_payment for x in values)}


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
