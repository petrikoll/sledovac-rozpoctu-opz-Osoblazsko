from __future__ import annotations

import hashlib
import re
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import fitz
import pdfplumber

from .models import PaymentLine, PaymentRequest

MONEY = r"(-?[0-9][0-9 \u00a0]*,[0-9]{2})"
BUDGET_CODE = re.compile(r"\b(1(?:\s*\.\s*\d+){2,})\b")


def money(value: str | None) -> Decimal:
    return Decimal((value or "0").replace(" ", "").replace("\u00a0", "").replace(",", "."))


def after(text: str, label: str, default: str = "") -> str:
    match = re.search(re.escape(label) + r"\s*:?[ \t]*\n([^\n]+)", text, re.I)
    return match.group(1).strip() if match else default


def amount_after(text: str, label: str, occurrence: int = 0) -> Decimal:
    normalized = re.sub(r"\s+", " ", text)
    matches = list(re.finditer(re.escape(label) + r"\s+" + MONEY, normalized, re.I))
    return money(matches[occurrence].group(1)) if len(matches) > occurrence else Decimal("0")


def parse_date(value: str):
    try:
        return datetime.strptime(value.strip(), "%d. %m. %Y").date()
    except (ValueError, AttributeError):
        return None


def extract_budget_code(text: str) -> str | None:
    matches = BUDGET_CODE.findall(text or "")
    if not matches:
        return None
    # V tabulkách PDF bývají mezery i uprostřed kódu (např. 1.1.4.1 .1).
    # Nejdelší nalezený kód je konkrétní rozpočtová položka.
    return re.sub(r"\s+", "", max(matches, key=lambda value: value.count(".")))


def _sd2_lines(path: Path) -> list[PaymentLine]:
    lines: list[PaymentLine] = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if "SD1" not in text and "SD2" not in text and not any(x.source_page_number == page_no - 1 for x in lines):
                continue
            for table in page.extract_tables() or []:
                for row_no, row in enumerate(table or [], 1):
                    cells = [re.sub(r"\s+", " ", c or "").strip() for c in row]
                    joined = " | ".join(cells)
                    code = extract_budget_code(joined)
                    amounts = re.findall(MONEY, joined)
                    first = next((c for c in cells if c.isdigit()), None)
                    if not code or len(amounts) < 1 or not first:
                        continue
                    values = [money(v) for v in amounts[-3:]]
                    declared = values[0] if len(values) >= 3 else values[-1]
                    reduction = values[-2] if len(values) >= 2 else Decimal("0")
                    approved = values[-1]
                    lines.append(PaymentLine(source_row_number=int(first), source_page_number=page_no,
                        budget_item_code=code, budget_item_name_raw=joined,
                        declared_amount=declared, reduction_amount=reduction, approved_amount=approved))
    return lines


def parse_payment_request(source: str | Path | bytes, file_name: str | None = None) -> PaymentRequest:
    if isinstance(source, bytes):
        data = source
        doc = fitz.open(stream=data, filetype="pdf")
        path = None
        name = file_name or "zadost.pdf"
    else:
        path = Path(source)
        data = path.read_bytes()
        doc = fitz.open(path)
        name = file_name or path.name
    pages = [page.get_text("text") for page in doc]
    text = "\n".join(pages)
    if len(text.strip()) < 100:
        raise ValueError("PDF neobsahuje textovou vrstvu. Pro import je nejprve potřeba OCR.")
    summary = next((p for p in pages if "Souhrnná soupiska" in p), "")
    sequence = int(after(text, "Pořadové číslo ŽoP", "0"))
    approved_direct = amount_after(summary, "Schválené způsobilé výdaje přímé")
    approved_lump = amount_after(summary, "Schválené další výdaje stanovené sazbou či paušálem")
    declared_direct = amount_after(summary, "Prokazované způsobilé výdaje přímé")
    declared_lump = amount_after(summary, "Prokazované další výdaje stanovené sazbou či paušálem")
    approved_total = amount_after(summary, "Schválené způsobilé výdaje")
    if sequence == 1:
        approved_total = Decimal("0")
    elif not summary:
        raise ValueError("V PDF nebyla nalezena Souhrnná soupiska.")
    elif approved_total <= 0 or approved_direct + approved_lump <= 0:
        raise ValueError("Ze Souhrnné soupisky se nepodařilo načíst schválené výdaje.")
    own = amount_after(text, "Vlastní podíl příjemce")
    public = amount_after(text, "Částka zálohy")
    request = PaymentRequest(project_code=after(text, "Číslo projektu"), project_name=after(text, "Název projektu"),
        recipient_name=after(text, "Příjemce projektu"), sequence_number=sequence,
        request_number=after(text, "Číslo žádosti o platbu"), request_version=int(after(text, "Verze ŽoP", "1")),
        request_type=after(text, "Typ žádosti o platbu"), state=after(text, "Stav"),
        processing_state=after(text, "Stav zpracování"), submitted_date=parse_date(after(text, "Datum předložení")),
        finalized_date=parse_date(after(text, "Datum finalizace")),
        is_final_payment=bool(re.search(r"Závěrečná platba\s*\nAno", text, re.I)),
        is_advance_payment=bool(re.search(r"Zálohová platba\s*\nAno", text, re.I)),
        declared_direct_costs=declared_direct, approved_direct_costs=approved_direct,
        declared_lump_sum=declared_lump, approved_lump_sum=approved_lump,
        own_share=own, public_payment=public, approved_total=approved_total,
        source_sha256=hashlib.sha256(data).hexdigest(), source_file_name=name,
        lines=[])
    if path:
        request.lines = _sd2_lines(path)
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp:
            temp.write(data); temp_path = Path(temp.name)
        try:
            request.lines = _sd2_lines(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
    return request
