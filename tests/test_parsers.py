from decimal import Decimal
from io import BytesIO
from pathlib import Path
import zipfile

import openpyxl

from app.pdf_parser import parse_payment_request
from app.xlsx_parser import export_with_formulas, fallback_rows, parse_budget

SAMPLES = Path(__file__).parents[1] / "samples"


def test_real_budget():
    result = parse_budget(SAMPLES / "Export_2026-07-11_084920.xlsx")
    assert result.total_amount == Decimal("4415040")
    assert result.lump_sum_rate == Decimal("0.4")
    assert result.lump_sum_base_code == "1.1"
    values = {x.code: x for x in result.items}
    assert values["1.1"].total_amount == Decimal("3153600")
    assert values["1.2"].total_amount == Decimal("1261440")
    assert values["1.1.1.1"].total_amount == Decimal("2232000")
    assert values["1.1.1.2"].total_amount == Decimal("921600")


def test_fallback_and_formula_export():
    data = (SAMPLES / "Export_2026-07-11_084920.xlsx").read_bytes()
    assert fallback_rows(data)
    result = parse_budget(data)
    wb = openpyxl.load_workbook(BytesIO(export_with_formulas(result)), data_only=False)
    rows = {wb["Export"].cell(r, 1).value: r for r in range(2, wb["Export"].max_row + 1)}
    assert wb["Export"].cell(rows["1.1.1"], 6).value.startswith("=")
    assert wb["Export"].cell(rows["1.2"], 6).value == f"=F{rows['1.1']}*I{rows['1.2']}/100"


def test_payment_samples():
    p0 = parse_payment_request(SAMPLES / "ZOP_PRJ0.pdf")
    p1 = parse_payment_request(SAMPLES / "ZOP_PRJ1.pdf")
    p2 = parse_payment_request(SAMPLES / "ZOP_PRJ2.pdf")
    assert p0.sequence_number == 1 and p0.is_advance_payment and p0.approved_total == 0
    assert p0.public_payment == Decimal("1258286.40")
    assert (p1.approved_direct_costs, p1.approved_lump_sum) == (Decimal("435105.00"), Decimal("174042.00"))
    assert (p2.approved_direct_costs, p2.approved_lump_sum) == (Decimal("514508.00"), Decimal("205803.20"))
    assert p1.approved_direct_costs + p2.approved_direct_costs == Decimal("949613")
    assert p1.approved_lump_sum + p2.approved_lump_sum == Decimal("379845.20")
    assert p1.approved_total + p2.approved_total == Decimal("1329458.20")
