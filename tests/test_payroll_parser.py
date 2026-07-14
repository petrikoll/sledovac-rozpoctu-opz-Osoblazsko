from decimal import Decimal

from app.payroll_parser import parse_payroll_list_page, parse_payroll_page


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


def test_parse_payroll_list_keeps_repeated_wage_components_separate():
    text = """
Výplatní list
Jméno
Osobní číslo
Z0019
Narození
Období
6 / 2026
16.03.1969, Vsetín
Bc. Martina Pírková
Název/Druh PP
PS Mosty v rodině
Fond
Pracovní pozice
Středisko
Sociální pracovník
176 hod.
25 500
M01
Základní mzda
měsíčně 25 500 Kč, základní prac. doba 176 hod
14 500
M01
Základní mzda
měsíčně 14 500 Kč, základní prac. doba 176 hod
1 800
M06
Osobní ohodnocení
měsíčně 1 800 Kč
Hrubá mzda
41 800
"""
    rows = parse_payroll_list_page(text)

    assert [row["component_occurrence"] for row in rows] == [1, 2, 1]
    assert [row["component_amount"] for row in rows] == [Decimal("25500"), Decimal("14500"), Decimal("1800")]
    assert rows[0]["first_name"] == "Martina"
    assert rows[0]["last_name"] == "Pírková"
    assert rows[0]["full_time_fund"] == Decimal("176")
