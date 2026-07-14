from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .models import CofinancingEntry, LumpSumEntry, PaymentRequest, Project, Sd2MonthlyEntry
from .models import BudgetAnalysis, BudgetItem


SHEETS = {
    "USERS": ["email", "role", "active", "created_at"],
    "PROJEKT_UZIVATELE": ["project_id", "email", "role", "active", "created_at", "created_by"],
    "PROJEKTY": ["project_id", "project_code", "project_name", "recipient_name", "financing_type", "total_budget", "public_funding_rate", "lump_sum_rate", "lump_sum_base_code", "current_monitoring_period", "total_monitoring_periods", "active_budget_version_id", "status", "created_at", "updated_at", "created_by"],
    "VERZE_ROZPOCTU": ["budget_version_id", "project_id", "version_number", "version_label", "effective_date", "total_amount", "source_file_name", "source_drive_file_id", "source_sha256", "change_description", "is_active", "created_at", "created_by"],
    "POLOZKY_ROZPOCTU": ["budget_item_id", "project_id", "budget_version_id", "code", "name", "parent_code", "level", "unit_custom", "unit_price", "unit_count", "total_amount", "percentage", "support_combination", "unit_preset", "unit_catalog", "category", "is_leaf", "is_new", "previous_amount", "transfer_locked", "minimum_remaining_amount", "planned_future_spending", "donor_priority", "source_row_number"],
    "ZADOSTI_O_PLATBU": ["payment_request_id", "project_id", "request_number", "sequence_number", "request_version", "request_type", "monitoring_period", "state", "processing_state", "submitted_date", "finalized_date", "is_final_payment", "is_advance_payment", "declared_direct_costs", "approved_direct_costs", "declared_lump_sum", "approved_lump_sum", "approved_other_costs", "clean_other_income", "own_share", "public_payment", "approved_total", "source_file_name", "source_drive_file_id", "source_sha256", "active_revision", "imported_at", "imported_by"],
    "RADKY_ZOP": ["payment_line_id", "payment_request_id", "project_id", "source_table", "source_row_number", "source_page_number", "budget_item_code", "budget_item_name_raw", "accounting_period", "subject_label", "description", "declared_amount", "reduction_amount", "approved_amount", "line_type", "mapping_status", "mapped_budget_item_id"],
    "PLATBY_A_ZALOHY": ["cash_flow_id", "project_id", "payment_request_id", "cash_flow_type", "payment_date", "amount", "note", "created_at"],
    "UTRATA_PAUSALU": ["lump_sum_entry_id", "project_id", "monitoring_period", "entry_date", "entry_mode", "entered_amount", "calculated_period_delta", "cumulative_spent", "note", "created_at", "created_by"],
    "SPOLUFINANCOVANI": ["cofinancing_entry_id", "project_id", "entry_date", "amount", "note", "created_at", "created_by"],
    "SD2_MESICE": ["sd2_entry_id", "project_id", "monitoring_period", "month", "budget_item_code", "gross_wage", "employer_contributions", "other_with_contributions", "other_without_contributions", "payment_date", "created_at", "created_by", "external_id", "subject_id", "last_name", "first_name", "employment_type", "work_time_fund", "project_hours", "description"],
    "SD2_PRILOHY": ["attachment_id", "project_id", "monitoring_period", "file_name", "drive_file_id", "uploaded_at", "uploaded_by"],
    # Keep the original six columns first for compatibility with existing Sheets.
    "PRACOVNICI_ROZPOCTU": ["worker_assignment_id", "project_id", "budget_item_code", "employee_names", "updated_at", "updated_by", "employee_name", "project_fte", "payroll_component_amount", "contract_contains"],
    "NAVRHY_ZMEN": ["proposal_id", "project_id", "source_budget_version_id", "proposal_status", "calculation_mode", "reserve_rate", "total_deficit", "total_transfer", "created_at", "created_by", "note"],
    "NAVRHY_ZMEN_RADKY": ["proposal_line_id", "proposal_id", "source_item_code", "target_item_code", "amount", "reason", "source_available_before", "source_available_after", "target_balance_before", "target_balance_after"],
    "IMPORT_LOG": ["import_id", "project_id", "import_type", "source_file_name", "source_sha256", "status", "error_code", "message", "created_at", "created_by"],
}


class Repository(ABC):
    @abstractmethod
    def projects(self) -> list[Project]: ...
    @abstractmethod
    def save_project(self, project: Project) -> None: ...


class InMemoryRepository(Repository):
    def __init__(self):
        self.project_data: dict[str, Project] = {}
        self.budgets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.payments: dict[str, list[PaymentRequest]] = defaultdict(list)
        self.lump_entries: dict[str, list[LumpSumEntry]] = defaultdict(list)
        self.cofinancing_entries: dict[str, list[CofinancingEntry]] = defaultdict(list)
        self.sd2_entries: dict[str, list[Sd2MonthlyEntry]] = defaultdict(list)
        self.sd2_attachments: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.worker_assignments: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.project_access: dict[str, set[str]] = defaultdict(set)
        self.import_log: list[dict[str, Any]] = []

    def projects(self): return list(self.project_data.values())
    def save_project(self, project): self.project_data[project.project_id] = project


class GoogleSheetsRepository(Repository):
    """Dávkový adaptér; doména nezná souřadnice buněk."""
    def __init__(self, spreadsheet_id: str, service_account_json: str):
        info = json.loads(service_account_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.api = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()
        self.id = spreadsheet_id

    def ensure_schema(self):
        metadata = self.api.get(spreadsheetId=self.id).execute()
        existing = {s["properties"]["title"] for s in metadata.get("sheets", [])}
        requests = [{"addSheet": {"properties": {"title": name}}} for name in SHEETS if name not in existing]
        if requests:
            self.api.batchUpdate(spreadsheetId=self.id, body={"requests": requests}).execute()
        body = {"valueInputOption": "RAW", "data": [{"range": f"'{name}'!A1", "values": [headers]} for name, headers in SHEETS.items()]}
        self.api.values().batchUpdate(spreadsheetId=self.id, body=body).execute()

    def projects(self):
        values = self.api.values().get(spreadsheetId=self.id, range="PROJEKTY!A2:O").execute().get("values", [])
        return [Project(**dict(zip(SHEETS["PROJEKTY"], row))) for row in values]

    def save_project(self, project):
        values = [[str(getattr(project, key, "")) for key in SHEETS["PROJEKTY"]]]
        self.api.values().append(spreadsheetId=self.id, range="PROJEKTY!A:O", valueInputOption="RAW", body={"values": values}).execute()

    @staticmethod
    def _scalar(value: Any) -> Any:
        if value is None: return ""
        if isinstance(value, Decimal): return float(value)
        if isinstance(value, (date, datetime)): return value.isoformat()
        return value

    def append_records(self, sheet: str, records: list[dict[str, Any]]) -> None:
        if not records: return
        rows = [[self._scalar(record.get(column, "")) for column in SHEETS[sheet]] for record in records]
        self.api.values().append(spreadsheetId=self.id, range=f"'{sheet}'!A:{'AZ'}", valueInputOption="RAW",
                                 insertDataOption="INSERT_ROWS", body={"values": rows}).execute()

    def update_record(self, sheet: str, key: str, key_value: str, changes: dict[str, Any]) -> None:
        headers = SHEETS[sheet]; key_col = headers.index(key)
        rows = self.api.values().get(spreadsheetId=self.id, range=f"'{sheet}'!A2:AZ").execute().get("values", [])
        for row_number, row in enumerate(rows, 2):
            if len(row) > key_col and str(row[key_col]) == str(key_value):
                data = [{"range": f"'{sheet}'!{chr(65 + headers.index(name))}{row_number}",
                         "values": [[self._scalar(value)]]} for name, value in changes.items() if name in headers and headers.index(name) < 26]
                self.api.values().batchUpdate(spreadsheetId=self.id, body={"valueInputOption": "RAW", "data": data}).execute()
                return
        raise ValueError(f"Záznam {key_value} v listu {sheet} nebyl nalezen.")

    def delete_record(self, sheet: str, key: str, key_value: str) -> None:
        metadata = self.api.get(spreadsheetId=self.id).execute()
        sheet_id = next(s["properties"]["sheetId"] for s in metadata["sheets"] if s["properties"]["title"] == sheet)
        headers = SHEETS[sheet]; key_col = headers.index(key)
        rows = self.api.values().get(spreadsheetId=self.id, range=f"'{sheet}'!A2:AZ").execute().get("values", [])
        for row_number, row in enumerate(rows, 2):
            if len(row) > key_col and str(row[key_col]) == str(key_value):
                self.api.batchUpdate(spreadsheetId=self.id, body={"requests": [{"deleteDimension": {"range": {
                    "sheetId": sheet_id, "dimension": "ROWS", "startIndex": row_number-1, "endIndex": row_number}}}]}).execute()
                return
        raise ValueError(f"Záznam {key_value} v listu {sheet} nebyl nalezen.")

    def delete_records(self, sheet: str, key: str, key_value: str) -> int:
        """Smaže všechny odpovídající řádky; maže odzadu, aby se neposouvala čísla řádků."""
        metadata = self.api.get(spreadsheetId=self.id).execute()
        sheet_id = next(s["properties"]["sheetId"] for s in metadata["sheets"] if s["properties"]["title"] == sheet)
        headers = SHEETS[sheet]; key_col = headers.index(key)
        rows = self.api.values().get(spreadsheetId=self.id, range=f"'{sheet}'!A2:AZ").execute().get("values", [])
        row_numbers = [number for number, row in enumerate(rows, 2)
                       if len(row) > key_col and str(row[key_col]) == str(key_value)]
        if row_numbers:
            requests = [{"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
                "startIndex": number - 1, "endIndex": number}}} for number in reversed(row_numbers)]
            self.api.batchUpdate(spreadsheetId=self.id, body={"requests": requests}).execute()
        return len(row_numbers)

    def _records(self, sheet: str) -> list[dict[str, Any]]:
        headers = SHEETS[sheet]
        end = chr(64 + len(headers)) if len(headers) <= 26 else "AZ"
        values = self.api.values().get(spreadsheetId=self.id, range=f"'{sheet}'!A2:{end}").execute().get("values", [])
        def normalize(value: Any) -> Any:
            if isinstance(value, str) and re.fullmatch(r"-?\d+,\d+", value.strip()):
                return value.replace(",", ".")
            return value
        return [dict(zip(headers, [normalize(value) for value in row] + [""] * (len(headers) - len(row)))) for row in values if row]

    @staticmethod
    def _bool(value: Any) -> bool:
        return value is True or str(value).lower() in {"true", "ano", "1"}

    def hydrate(self, target: InMemoryRepository) -> None:
        """Načte dávkově trvalý stav do rychlého aplikačního modelu."""
        target.project_data.clear(); target.budgets.clear(); target.payments.clear(); target.lump_entries.clear(); target.cofinancing_entries.clear(); target.sd2_entries.clear(); target.sd2_attachments.clear(); target.worker_assignments.clear(); target.project_access.clear()
        for record in self._records("PROJEKT_UZIVATELE"):
            if self._bool(record.get("active", True)) and record.get("project_id") and record.get("email"):
                target.project_access[str(record["project_id"])].add(str(record["email"]).lower())
        for record in self._records("PROJEKTY"):
            project = Project(**{key: record[key] for key in Project.model_fields if record.get(key) not in ("", None)})
            target.project_data[project.project_id] = project
        items_by_version: dict[str, list[BudgetItem]] = defaultdict(list)
        for record in self._records("POLOZKY_ROZPOCTU"):
            values = {key: record.get(key) for key in BudgetItem.model_fields if record.get(key) not in ("", None)}
            if "is_leaf" in values: values["is_leaf"] = self._bool(values["is_leaf"])
            items_by_version[str(record["budget_version_id"])].append(BudgetItem(**values))
        for version in self._records("VERZE_ROZPOCTU"):
            version_id = str(version["budget_version_id"]); items = items_by_version.get(version_id, [])
            lump = next((x for x in items if x.category == "lump_sum"), None)
            project = target.project_data.get(str(version["project_id"]))
            analysis = BudgetAnalysis(sha256=str(version.get("source_sha256", "")), file_name=str(version.get("source_file_name", "")),
                items=items, total_amount=version.get("total_amount", 0), lump_sum_rate=project.lump_sum_rate if project else None,
                lump_sum_base_code=project.lump_sum_base_code if project else None, leaf_count=sum(x.is_leaf for x in items),
                summary_count=sum(not x.is_leaf for x in items))
            target.budgets[str(version["project_id"])].append({"version_id": version_id, "analysis": analysis,
                "sha256": analysis.sha256, "source": b"", "drive_file_id": version.get("source_drive_file_id", "")})
        lines_by_payment: dict[str, list] = defaultdict(list)
        from .models import PaymentLine
        for record in self._records("RADKY_ZOP"):
            values = {key: record.get(key) for key in PaymentLine.model_fields if record.get(key) not in ("", None)}
            lines_by_payment[str(record["payment_request_id"])].append(PaymentLine(**values))
        for record in self._records("ZADOSTI_O_PLATBU"):
            values = {key: record.get(key) for key in PaymentRequest.model_fields if key != "lines" and record.get(key) not in ("", None)}
            parent = target.project_data.get(str(record["project_id"]))
            # Starší ruční zásahy mohou v tabulce zanechat ŽoP bez existujícího
            # projektu. Takový záznam nelze v aplikaci zobrazit a nesmí zablokovat start.
            if not parent:
                continue
            values.update(project_code=parent.project_code, project_name=parent.project_name, recipient_name=parent.recipient_name)
            for key in ("is_final_payment", "is_advance_payment"):
                if key in values: values[key] = self._bool(values[key])
            request = PaymentRequest(**values, lines=lines_by_payment.get(str(record["payment_request_id"]), []))
            target.payments[str(record["project_id"])].append(request)
        for record in self._records("UTRATA_PAUSALU"):
            values = {key: record.get(key) for key in LumpSumEntry.model_fields if record.get(key) not in ("", None)}
            target.lump_entries[str(record["project_id"])].append(LumpSumEntry(**values))
        for record in self._records("SPOLUFINANCOVANI"):
            values = {key: record.get(key) for key in CofinancingEntry.model_fields if record.get(key) not in ("", None)}
            target.cofinancing_entries[str(record["project_id"])].append(CofinancingEntry(**values))
        for record in self._records("SD2_MESICE"):
            values = {key: record.get(key) for key in Sd2MonthlyEntry.model_fields if record.get(key) not in ("", None)}
            if record.get("project_id"):
                target.sd2_entries[str(record["project_id"])].append(Sd2MonthlyEntry(**values))
        for record in self._records("SD2_PRILOHY"):
            if record.get("project_id"):
                target.sd2_attachments[str(record["project_id"])].append(record)
        try:
            worker_records = self._records("PRACOVNICI_ROZPOCTU")
        except KeyError:  # Compatibility with pre-worker import fixtures and legacy sheets.
            worker_records = []
        for record in worker_records:
            if record.get("project_id") and record.get("budget_item_code"):
                target.worker_assignments[str(record["project_id"])].append(record)
        target.import_log = self._records("IMPORT_LOG")
