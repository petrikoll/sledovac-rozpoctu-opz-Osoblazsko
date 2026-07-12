from decimal import Decimal

from app.payroll_parser import parse_payroll_page


def test_parse_payroll_page_extracts_sd2_values_without_sensitive_data():
    text = """
│ 00414 │ Malíková Silvie │ 825329/5875 │ VPP │ 2023/11 │
 Fond pracovní doby      22.00 dny  52.80 hod
 Odpracováno v měsíci    22.00 dny  48.50 hod
 Hrubá mzda       12000 Kč =
 Pojistné zaměstnavatel   4056 Kč
 Číslo účtu 111111/0100
"""
    row = parse_payroll_page(text)
    assert row is not None
    assert row["first_name"] == "Silvie"
    assert row["last_name"] == "Malíková"
    assert row["month"] == "2023-11-01"
    assert row["gross_wage"] == Decimal("12000")
    assert row["employer_contributions"] == Decimal("4056")
    assert row["work_time_fund"] == Decimal("52.80")
    assert row["worked_hours"] == Decimal("48.50")
    assert "bank_account" not in row and "personal_id" not in row


def test_parse_payroll_page_handles_reversed_name_and_legacy_dpp():
    text = """
│ 00415 │ Vendula Černochová Mgr. │ 000000/0000 │ DPP │ 2023/11 │
 Fond pracovní doby 7.00 hod
 Odpracováno v měsíci 7.00 hod
 Hrubá mzda 3500 Kč
 Pojistné zaměstnavatel 0 Kč
"""
    row = parse_payroll_page(text)
    assert row is not None
    assert row["first_name"] == "Vendula"
    assert row["last_name"] == "Černochová"
    assert row["employment_type"] == "DPPDo"
