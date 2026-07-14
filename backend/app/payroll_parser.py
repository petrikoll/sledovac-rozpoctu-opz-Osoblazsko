from __future__ import annotations

import re
import calendar
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
    subject_match = re.search(r"IČ:\s*(\d{6,10})", text)
    subject_id = subject_match.group(1) if subject_match else ""
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


def _plain_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip() for line in text.splitlines() if line.strip()]


def _line_amount(value: str) -> Decimal | None:
    if not re.fullmatch(r"-?[\d ]+(?:,\d+)?", value):
        return None
    try:
        return _number(value)
    except Exception:
        return None


def _contract_type(contract_name: str, month: date, gross_wage: Decimal) -> str:
    normalized = contract_name.upper().replace("Č", "C")
    if normalized.startswith("DPC"):
        return "DPC"
    if normalized.startswith("DPP"):
        if month.year <= 2024:
            return "DPPDo" if gross_wage <= Decimal("10000") else "DPPNad"
        return "DPP"
    return "Smlouva"


def parse_payroll_list_page(text: str, page_number: int = 1) -> list[dict]:
    """Parse one first page of the Pohoda-style 'Výplatní list' report.

    The report may contain several contracts and several wage components under one
    contract. Each component is returned separately so project-only components can
    be selected without importing the employee's unrelated jobs.
    """
    lines = _plain_lines(text)
    if "Výplatní list" not in lines or "Období" not in lines or "Jméno" not in lines:
        return []
    try:
        period_index = lines.index("Období")
        period_match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{4})", lines[period_index + 1])
        if not period_match:
            return []
        month = date(int(period_match.group(2)), int(period_match.group(1)), 1)
        # The period value is followed by date/place of birth and then the name.
        full_name = lines[period_index + 3]
    except (ValueError, IndexError):
        return []
    last_name, first_name = _split_name(full_name)
    subject_match = re.search(r"IČ:\s*(\d{6,10})", text)
    subject_id = subject_match.group(1) if subject_match else ""
    contract_starts = [index for index, line in enumerate(lines) if line == "Název/Druh PP"]
    full_time_fund = Decimal(sum(1 for day in range(1, calendar.monthrange(month.year, month.month)[1] + 1)
                                 if date(month.year, month.month, day).weekday() < 5) * 8)
    results: list[dict] = []
    for contract_number, start in enumerate(contract_starts, 1):
        end = contract_starts[contract_number] if contract_number < len(contract_starts) else len(lines)
        # The exact summary label marks the end of contract detail; component labels
        # such as "Základní mzda" do not match this condition.
        summary = next((index for index in range(start + 1, end) if lines[index] == "Hrubá mzda"), end)
        end = summary
        if start + 1 >= end:
            continue
        contract_name = lines[start + 1]
        fund = Decimal("0")
        position_name = ""
        try:
            fund_index = lines.index("Fond", start, end)
            fund_match_index = next(index for index in range(fund_index + 1, end)
                                    if re.fullmatch(r"[\d.,]+\s*hod\.", lines[index], re.IGNORECASE))
            fund = _number(re.sub(r"\s*hod\.$", "", lines[fund_match_index], flags=re.IGNORECASE))
            if "Středisko" in lines[fund_index:fund_match_index]:
                center_index = lines.index("Středisko", fund_index, fund_match_index)
                if center_index + 1 < fund_match_index:
                    position_name = lines[center_index + 1]
        except (ValueError, StopIteration):
            pass
        components: list[tuple[int, Decimal, str, str, str]] = []
        for index in range(start + 2, end - 1):
            if not re.fullmatch(r"[A-Z]\d{2}", lines[index]):
                continue
            amount = _line_amount(lines[index - 1])
            if amount is None or lines[index] == "Z21":
                continue
            if lines[index] == "V01":
                label = "Dovolená"
                description = " · ".join(lines[max(start + 2, index - 4):index - 1])
            else:
                label = lines[index + 1] if index + 1 < end else ""
                description = lines[index + 2] if index + 2 < end else ""
            if label not in {"Základní mzda", "Časová mzda", "Osobní ohodnocení", "Prémie pevnou částkou", "Dovolená"}:
                continue
            components.append((index, amount, lines[index], label, description))
        contract_gross = sum((component[1] for component in components), Decimal("0"))
        employment_type = _contract_type(contract_name, month, contract_gross)
        insured = employment_type == "Smlouva" or (employment_type == "DPC" and contract_gross >= Decimal("4500")) or (employment_type == "DPP" and contract_gross >= Decimal("12000"))
        occurrences: dict[str, int] = {}
        for component_number, (_, amount, component_code, label, description) in enumerate(components, 1):
            occurrences[component_code] = occurrences.get(component_code, 0) + 1
            hours_match = re.search(r"odprac\.\s*([\d.,]+)\s*hod", description, re.IGNORECASE)
            worked_hours = _number(hours_match.group(1)) if hours_match else fund
            results.append({
                "source_key": f"{page_number}-{contract_number}-{component_number}",
                "page_number": page_number,
                "employee_number": "",
                "full_name": full_name,
                "last_name": last_name,
                "first_name": first_name,
                "subject_id": subject_id,
                "category": contract_name,
                "contract_name": contract_name,
                "position_name": position_name,
                "component_code": component_code,
                "component_occurrence": occurrences[component_code],
                "component_name": label,
                "component_description": description,
                "component_amount": amount,
                "contract_gross": contract_gross,
                "month": month.isoformat(),
                "gross_wage": amount,
                "employer_contributions": (amount * Decimal("0.338")).quantize(Decimal("0.01")) if insured else Decimal("0"),
                "work_time_fund": fund if fund else worked_hours,
                "full_time_fund": full_time_fund,
                "worked_hours": worked_hours,
                "project_hours": worked_hours,
                "employment_type": employment_type,
            })
    return results


def parse_payroll_slips(data: bytes) -> list[dict]:
    """Extract only SD-2-relevant values; personal IDs and bank accounts are discarded."""
    document = fitz.open(stream=data, filetype="pdf")
    rows: list[dict] = []
    for page_number, page in enumerate(document, 1):
        text = page.get_text()
        row = parse_payroll_page(text, page_number)
        if row:
            row["source_key"] = str(page_number)
            row["contract_name"] = row["category"]
            row["position_name"] = ""
            row["component_code"] = ""
            row["component_name"] = "Hrubá mzda"
            row["component_description"] = ""
            row["component_amount"] = row["gross_wage"]
            row["project_hours"] = row["worked_hours"]
            rows.append(row)
        else:
            rows.extend(parse_payroll_list_page(text, page_number))
    if not rows:
        raise ValueError("V PDF nebyla nalezena žádná podporovaná výplatní páska ani výplatní list.")
    return rows


def parse_payslip_insurance(data: bytes) -> list[dict]:
    """Read exact employer insurance totals per employee, month and contract."""
    document = fitz.open(stream=data, filetype="pdf")
    results: list[dict] = []
    for page in document:
        lines = _plain_lines(page.get_text())
        starts = [index for index, line in enumerate(lines) if line == "Osobní číslo"]
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(lines)
            section = lines[start:end]
            try:
                full_name = section[1]
                period_index = section.index("Období")
                period = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{4})", section[period_index + 1])
                if not period:
                    continue
                month = date(int(period.group(2)), int(period.group(1)), 1)
            except (ValueError, IndexError):
                continue
            last_name, first_name = _split_name(full_name)
            contract_labels = [index for index, line in enumerate(section) if line == "Název/Druh PP"]
            for contract_position, label_index in enumerate(contract_labels):
                contract_end = contract_labels[contract_position + 1] if contract_position + 1 < len(contract_labels) else len(section)
                contract_name = section[label_index - 1] if label_index else ""
                block = section[label_index:contract_end]
                amounts: list[Decimal] = []
                for label in ("Sociální zaměstnavatel", "Zdravotní zaměstnavatel"):
                    try:
                        value_index = block.index(label) + 1
                        value = _line_amount(block[value_index]) if value_index < len(block) else None
                        if value is not None:
                            amounts.append(value)
                    except ValueError:
                        pass
                if amounts:
                    results.append({"full_name": full_name, "first_name": first_name, "last_name": last_name,
                                    "month": month.isoformat(), "contract_name": contract_name,
                                    "employer_insurance": sum(amounts, Decimal("0"))})
    return results
