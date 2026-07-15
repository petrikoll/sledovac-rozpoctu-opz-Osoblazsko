from decimal import Decimal

from app.payroll_parser import parse_detailed_payslip_page, parse_payroll_list_page, parse_payroll_page


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
Čerpáno dov.
16 hod.
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
    assert rows[0]["total_fte"] == Decimal("1.0000")
    assert rows[0]["vacation_hours"] == Decimal("16")
    assert rows[0]["vacation_days"] == Decimal("2.00")


def test_parse_osoblazsky_cech_detailed_payslip():
    text = """
AugustýnovAugustýnová Andrea
Pracovník
Kategorie HPP
Hlavní pracovní poměr
Měsíc Červen 2026
Parametry měsíce
176,00
176,00
Prémie
0,00
Datum a podpis
07.07.2026
Hrubý příjem
42 000,00
Náklady zaměstnavatele
56 196,00
Hrubý příjem
Soc. + zdr. poj. zaměstnavatele
Spoření na stáří - zaměstnavatel
Podíl (Čistá mzda/Náklady)
42 000,00
14 196,00
0,00
59,4 %
Osoblažský cech, z.ú. (IČO: 01937324)
Zaměstnavatel:
"""

    row = parse_detailed_payslip_page(text)

    assert row is not None
    assert row["first_name"] == "Andrea"
    assert row["last_name"] == "Augustýnová"
    assert row["month"] == "2026-06-01"
    assert row["employment_type"] == "Smlouva"
    assert row["gross_wage"] == Decimal("42000")
    assert row["employer_contributions"] == Decimal("14196")
    assert row["work_time_fund"] == Decimal("176")
    assert row["project_hours"] == Decimal("176")
    assert row["payment_date"] == "2026-07-07"
    assert row["subject_id"] == "01937324"


def test_parse_osoblazsky_cech_shortened_contract():
    text = """
Tichavská
Tichavská Lenka, Bc.
Pracovník
Kategorie PP_D
Pracovní poměr důchodce
Měsíc Červen 2026
Odpracovaná doba celkem
Fond pracovní doby
22,00
70,40
0,00
70,40
Parametry měsíce
70,40
70,40
Prémie
0,00
Datum a podpis
07.07.2026
Hrubý příjem
23 900,00
Náklady zaměstnavatele
31 979,00
Hrubý příjem
Soc. + zdr. poj. zaměstnavatele
Spoření na stáří - zaměstnavatel
Podíl (Čistá mzda/Náklady)
23 900,00
8 079,00
0,00
59,4 %
Osoblažský cech, z.ú. (IČO: 01937324)
Zaměstnavatel:
"""

    row = parse_detailed_payslip_page(text)

    assert row is not None
    assert row["first_name"] == "Lenka"
    assert row["last_name"] == "Tichavská"
    assert row["month"] == "2026-06-01"
    assert row["employment_type"] == "Smlouva"
    assert row["gross_wage"] == Decimal("23900")
    assert row["employer_contributions"] == Decimal("8079")
    assert row["work_time_fund"] == Decimal("70.40")
    assert row["full_time_fund"] == Decimal("176")
    assert row["total_fte"] == Decimal("0.4000")
    assert row["project_hours"] == Decimal("70.40")
    assert row["payment_date"] == "2026-07-07"


def test_parse_osoblazsky_cech_dpct_under_limit():
    text = """
Ing.Laštov Laštovica Petr
Pracovník
Kategorie DPC_1
Dohoda o pracovní činnosti - do limitu
Měsíc Červen 2026
Odpracovaná doba celkem
Fond pracovní doby
22,00
64,00
64,00
0,00
Parametry měsíce
64,00
176,00
Prémie
0,00
Datum a podpis
07.07.2026
Hrubý příjem
24 000,00
Náklady zaměstnavatele
32 112,00
Hrubý příjem
Soc. + zdr. poj. zaměstnavatele
Spoření na stáří - zaměstnavatel
Podíl (Čistá mzda/Náklady)
24 000,00
8 112,00
0,00
59,7 %
Osoblažský cech, z.ú. (IČO: 01937324)
Zaměstnavatel:
"""

    row = parse_detailed_payslip_page(text)

    assert row is not None
    assert row["first_name"] == "Petr"
    assert row["last_name"] == "Laštovica"
    assert row["category"] == "DPC_1"
    assert row["employment_type"] == "DPC"
    assert row["gross_wage"] == Decimal("24000")
    assert row["employer_contributions"] == Decimal("8112")
    assert row["work_time_fund"] == Decimal("64")
    assert row["full_time_fund"] == Decimal("176")
    assert row["project_hours"] == Decimal("64")
    assert row["payment_date"] == "2026-07-07"
