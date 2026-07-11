"""Obnoví ověřený projekt z dodaných podkladů přímo do Google Sheets a Drive."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.repository import SHEETS, GoogleSheetsRepository
from app.xlsx_parser import parse_budget
from app.pdf_parser import parse_payment_request


def scalar(value):
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def row(sheet: str, values: dict) -> list:
    return [scalar(values.get(column, "")) for column in SHEETS[sheet]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--folder", required=True)
    args = parser.parse_args()
    root = Path(__file__).parents[1]
    key_text = Path(args.key).read_text(encoding="utf-8")
    info = json.loads(key_text)
    sheets_repo = GoogleSheetsRepository(args.sheet, key_text)
    sheets_repo.ensure_schema()
    sheets = sheets_repo.api.values()
    now = datetime.now(timezone.utc).isoformat()

    # Neprovádět druhou migraci stejného projektu.
    existing = sheets.get(spreadsheetId=args.sheet, range="PROJEKTY!B2:B").execute().get("values", [])
    project_code = "CZ.03.02.01/00/24_065/0004961"
    if any(values and values[0] == project_code for values in existing):
        print("OK: projekt již v Google Sheets existuje; migrace nebyla zopakována.")
        return

    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False).files()
    source_files = [root / "samples" / "Export_2026-07-11_084920.xlsx"] + [root / "samples" / f"ZOP_PRJ{i}.pdf" for i in range(3)]
    found = drive.list(
        q=f"'{args.folder}' in parents and trashed = false", fields="files(id,name)"
    ).execute().get("files", [])
    drive_ids = {item["name"]: item["id"] for item in found}
    missing = [path.name for path in source_files if path.name not in drive_ids]
    if missing:
        raise RuntimeError("Ve sdílené složce chybí: " + ", ".join(missing))

    project_id = "9f55d666-96ad-4c94-8fca-5979f62a6c96"
    version_id = str(uuid4())
    budget = parse_budget(source_files[0])
    creator = "petr.lastovica@gmail.com"
    data: dict[str, list[list]] = {name: [] for name in SHEETS}
    data["USERS"] = [
        row("USERS", {"email": creator, "role": "admin", "active": True, "created_at": now}),
        row("USERS", {"email": "srssjesenik@gmail.com", "role": "user", "active": True, "created_at": now}),
    ]
    data["PROJEKTY"].append(row("PROJEKTY", {
        "project_id": project_id, "project_code": project_code, "project_name": "JESENICKO PROTI DLUHŮM - III",
        "recipient_name": "Středisko rozvoje sociálních služeb, o.p.s.", "financing_type": "ex-ante",
        "total_budget": budget.total_amount, "public_funding_rate": Decimal("0.95"), "lump_sum_rate": Decimal("0.40"),
        "lump_sum_base_code": "1.1", "current_monitoring_period": 3, "total_monitoring_periods": 3,
        "active_budget_version_id": version_id, "status": "aktivní", "created_at": now, "updated_at": now, "created_by": creator,
    }))
    data["VERZE_ROZPOCTU"].append(row("VERZE_ROZPOCTU", {
        "budget_version_id": version_id, "project_id": project_id, "version_number": 1, "version_label": "Původní rozpočet",
        "total_amount": budget.total_amount, "source_file_name": budget.file_name,
        "source_drive_file_id": drive_ids[budget.file_name], "source_sha256": budget.sha256,
        "change_description": "Původní schválený rozpočet", "is_active": True, "created_at": now, "created_by": creator,
    }))
    item_ids = {}
    for item in budget.items:
        item_id = str(uuid4()); item_ids[item.code] = item_id
        values = item.model_dump()
        values.update({"budget_item_id": item_id, "project_id": project_id, "budget_version_id": version_id,
                       "is_new": False, "previous_amount": "", "transfer_locked": False,
                       "minimum_remaining_amount": 0, "planned_future_spending": 0, "donor_priority": 100})
        data["POLOZKY_ROZPOCTU"].append(row("POLOZKY_ROZPOCTU", values))

    requests = [parse_payment_request(path) for path in source_files[1:]]
    for payment in requests:
        values = payment.model_dump(exclude={"lines"})
        values.update({"project_id": project_id, "monitoring_period": max(1, payment.sequence_number - 1),
                       "approved_other_costs": 0, "source_drive_file_id": drive_ids[payment.source_file_name],
                       "active_revision": True, "imported_at": now, "imported_by": creator})
        data["ZADOSTI_O_PLATBU"].append(row("ZADOSTI_O_PLATBU", values))
        for line in payment.lines:
            values = line.model_dump()
            mapped = item_ids.get(line.budget_item_code or "")
            values.update({"payment_line_id": str(uuid4()), "payment_request_id": payment.payment_request_id,
                           "project_id": project_id, "line_type": "direct", "mapping_status": "matched" if mapped else "unmatched",
                           "mapped_budget_item_id": mapped or ""})
            data["RADKY_ZOP"].append(row("RADKY_ZOP", values))
        if payment.public_payment:
            data["PLATBY_A_ZALOHY"].append(row("PLATBY_A_ZALOHY", {
                "cash_flow_id": str(uuid4()), "project_id": project_id, "payment_request_id": payment.payment_request_id,
                "cash_flow_type": "initial_advance" if payment.is_advance_payment else "advance",
                "payment_date": payment.finalized_date or payment.submitted_date, "amount": payment.public_payment,
                "note": f"Import ŽoP č. {payment.sequence_number}", "created_at": now,
            }))
        data["IMPORT_LOG"].append(row("IMPORT_LOG", {
            "import_id": str(uuid4()), "project_id": project_id, "import_type": "payment_request",
            "source_file_name": payment.source_file_name, "source_sha256": payment.source_sha256,
            "status": "success", "message": f"Importována ŽoP č. {payment.sequence_number}", "created_at": now, "created_by": creator,
        }))
    data["UTRATA_PAUSALU"].append(row("UTRATA_PAUSALU", {
        "lump_sum_entry_id": str(uuid4()), "project_id": project_id, "monitoring_period": 3,
        "entry_date": "2026-07-11", "entry_mode": "cumulative", "entered_amount": Decimal("151000"),
        "calculated_period_delta": Decimal("151000"), "cumulative_spent": Decimal("151000"),
        "note": "Kumulativní stav dle účetnictví k 11. 7. 2026", "created_at": now, "created_by": creator,
    }))
    data["IMPORT_LOG"].insert(0, row("IMPORT_LOG", {
        "import_id": str(uuid4()), "project_id": project_id, "import_type": "budget",
        "source_file_name": budget.file_name, "source_sha256": budget.sha256, "status": "success",
        "message": f"Importováno {len(budget.items)} položek", "created_at": now, "created_by": creator,
    }))
    payload = {"valueInputOption": "USER_ENTERED", "data": [
        {"range": f"'{name}'!A2", "majorDimension": "ROWS", "values": rows}
        for name, rows in data.items() if rows
    ]}
    sheets.batchUpdate(spreadsheetId=args.sheet, body=payload).execute()
    print(f"OK: uloženo 1 projekt, {len(budget.items)} položek, {len(requests)} ŽoP a 1 záznam paušálu; nahrány 4 soubory.")


if __name__ == "__main__":
    main()
