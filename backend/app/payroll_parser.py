from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import fitz


TITLES = {"bc", "mgr", "ing", "mudr", "judr", "phd", "phdr", "dis"}


def _number(value: str) -> Decimal:
    return Decimal(value.replace(" ", "").replace("\xa0", "").replace(",", "."))


def _find_amount(text: str, label: str) -> Decimal:
    match = re.search(rf"{label}\s+(-?[\d\s.,]+)\s*Kč", text, re.IGNORECASE)
    return _number(match.group(1)) if match else Decimal("0")


def _find_hours(text: str, label: str) -> Decimal:
    match = re.search(rf"{label}[^\n]*?([\d.,]+)\s*hod", text, re.IGNORECASE)
    return _number(match.group(1)) if match else Decimal("0")


def _employment_type(category: str, month: date, gross_wage: Decimal) -> str:
    normalized = category.upper().replace("Č", "C")
    if normalized == "DPC":
        return "DPC"
    if normalized == "DPP":
        if month.year <= 2024:
            return "DPPDo" if gross_wage <= Decimal("10000") else "DPPNad"
        return "DPP"
    return "Smlouva"


def _split_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in full_name.split() if part.casefold().strip(".,") not in TITLES]
    if not parts:
        return "", ""
    surname_index = next((index for index, part in enumerate(parts) if part.casefold().endswith("ová")), 0)
    last_name = parts[surname_index]
    first_name = " ".join(part for index, part in enumerate(parts) if index != surname_index)
    return last_name, first_name


def parse_payroll_page(text: str, page_number: int = 1) -> dict | None:
    header = re.search(
        r"│\s*(\d+)\s*│\s*([^│]+?)\s*│[^│]*│\s*([^│]+?)\s*│\s*(\d{4})/(\d{2})\s*│",
        text,
    )
    if not header:
        return None
    employee_number, full_name, category, year, month_number = (part.strip() for part in header.groups())
    last_name, first_name = _split_name(full_name)
    month = date(int(year), int(month_number), 1)
    gross_wage = _find_amount(text, r"Hrubá\s+mzda")
    return {
        "page_number": page_number,
        "employee_number": employee_number,
        "full_name": full_name,
        "last_name": last_name,
        "first_name": first_name,
        "category": category,
        "month": month.isoformat(),
        "gross_wage": gross_wage,
        "employer_contributions": _find_amount(text, r"Pojistné\s+zaměstnavatel"),
        "work_time_fund": _find_hours(text, r"Fond\s+pracovní\s+doby"),
        "worked_hours": _find_hours(text, r"Odpracováno\s+v\s+měsíci"),
        "employment_type": _employment_type(category, month, gross_wage),
    }


def parse_payroll_slips(data: bytes) -> list[dict]:
    """Extract only SD-2-relevant values; personal IDs and bank accounts are discarded."""
    document = fitz.open(stream=data, filetype="pdf")
    rows: list[dict] = []
    for page_number, page in enumerate(document, 1):
        row = parse_payroll_page(page.get_text(), page_number)
        if row:
            rows.append(row)
    if not rows:
        raise ValueError("V PDF nebyla nalezena žádná podporovaná výplatní páska.")
    return rows
