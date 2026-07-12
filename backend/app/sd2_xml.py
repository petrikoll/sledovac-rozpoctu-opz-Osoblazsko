from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from xml.etree import ElementTree as ET

from .models import Sd2MonthlyEntry


NAMESPACE = "http://ms21xsd.mssf.cz/ImportXML/SoupiskaDoklad/v_1.1"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
ALLOWED_EMPLOYMENT_TYPES = {"Smlouva", "DPC", "DPP", "DPPDo", "DPPNad"}


def _scale(value: Decimal) -> int:
    return max(0, -value.as_tuple().exponent)


def _decimal(value: Decimal, places: int) -> str:
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _datetime(value) -> str:
    return f"{value:%Y-%m-%d}T00:00:00.000"


def _has_content(entry: Sd2MonthlyEntry) -> bool:
    return any((
        entry.gross_wage,
        entry.employer_contributions,
        entry.other_with_contributions,
        entry.other_without_contributions,
    ))


def validate_sd2_entries(entries: list[Sd2MonthlyEntry]) -> list[str]:
    active = [entry for entry in entries if _has_content(entry)]
    errors: list[str] = []
    if len(active) > 500:
        errors.append("Jedno XML může obsahovat nejvýše 500 záznamů.")
    seen: set[str] = set()
    for position, entry in enumerate(active, 1):
        label = f"Řádek {position} ({entry.budget_item_code}, {entry.month:%m/%Y})"
        external_id = entry.external_id.strip() or f"SD2-{entry.sd2_entry_id}"
        if external_id in seen:
            errors.append(f"{label}: ID z externího systému není jedinečné.")
        seen.add(external_id)
        if entry.subject_id.strip() and (not entry.subject_id.strip().isdigit() or len(entry.subject_id.strip()) > 10):
            errors.append(f"{label}: IČ subjektu může obsahovat nejvýše 10 číslic.")
        if not entry.budget_item_code.strip():
            errors.append(f"{label}: chybí položka rozpočtu.")
        if entry.employment_type is not None and entry.employment_type not in ALLOWED_EMPLOYMENT_TYPES:
            errors.append(f"{label}: vyberte platný pracovněprávní vztah.")
        if entry.work_time_fund < 0 or entry.project_hours < 0:
            errors.append(f"{label}: fond a projektové hodiny nesmějí být záporné.")
        if _scale(entry.work_time_fund) > 3 or _scale(entry.project_hours) > 3:
            errors.append(f"{label}: fond a projektové hodiny mohou mít nejvýše tři desetinná místa.")
        for value, name in (
            (entry.gross_wage, "hrubá mzda"),
            (entry.employer_contributions, "pojistné"),
            (entry.other_with_contributions, "jiné výdaje s odvody"),
            (entry.other_without_contributions, "jiné výdaje bez odvodů"),
        ):
            if _scale(value) > 2:
                errors.append(f"{label}: {name} může mít nejvýše dvě desetinná místa.")
    return errors


def build_sd2_xml(entries: list[Sd2MonthlyEntry], generated_at: datetime | None = None) -> bytes:
    active = [entry for entry in entries if _has_content(entry)]
    errors = validate_sd2_entries(active)
    if errors:
        raise ValueError("\n".join(errors))

    generated_at = generated_at or datetime.now()
    root = ET.Element(
        f"{{{NAMESPACE}}}IMPORT",
        {"DATE": _datetime(generated_at), "xmlns:xsi": XSI_NAMESPACE},
    )

    def add(parent: ET.Element, name: str, value: str) -> None:
        ET.SubElement(parent, f"{{{NAMESPACE}}}{name}").text = value

    for entry in active:
        record = ET.SubElement(root, f"{{{NAMESPACE}}}SoupiskaDoklad")
        add(record, "ID_EXT", entry.external_id.strip() or f"SD2-{entry.sd2_entry_id}")
        add(record, "TYPDOKLADU", "Mzdy")
        if entry.subject_id.strip():
            subject_id = entry.subject_id.strip().zfill(8) if len(entry.subject_id.strip()) <= 8 else entry.subject_id.strip()
            add(record, "IC", subject_id)
        add(record, "POLOZKA", entry.budget_item_code.strip())
        if entry.description.strip():
            add(record, "POPIS", entry.description.strip())
        if entry.payment_date:
            add(record, "DATUMUHRADY", _datetime(entry.payment_date))
        add(record, "DATUMLZ", _datetime(entry.month.replace(day=1)))
        if entry.last_name.strip():
            add(record, "PRIJMENI", entry.last_name.strip())
        if entry.first_name.strip():
            add(record, "JMENO", entry.first_name.strip())
        if entry.employment_type:
            add(record, "DRUHPRACVZTAHU", str(entry.employment_type))
        add(record, "MZDA", _decimal(entry.gross_wage, 2))
        add(record, "FONDPRACDOBY", _decimal(entry.work_time_fund, 3))
        add(record, "POCETHODINNAPRJ", _decimal(entry.project_hours, 3))
        add(record, "JINEVYDAJESODVODY", _decimal(entry.other_with_contributions, 2))
        add(record, "POJISTNE", _decimal(entry.employer_contributions, 2))
        add(record, "JINEVYDAJEBEZODVODU", _decimal(entry.other_without_contributions, 2))

    body = ET.tostring(root, encoding="utf-8", short_empty_elements=False)
    # ElementTree reserves ns0/ns1 prefixes. The prefix itself is semantically
    # irrelevant, but keeping ns2 makes the export visually match the official sample.
    body = body.replace(b"ns0:", b"ns2:").replace(b"xmlns:ns0=", b"xmlns:ns2=")
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body + b"\n"
