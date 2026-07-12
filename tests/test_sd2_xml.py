from datetime import date, datetime
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from app.models import Sd2MonthlyEntry
from app.sd2_xml import NAMESPACE, build_sd2_xml


def complete_entry(**changes):
    values = {
        "sd2_entry_id": "fixed-id",
        "monitoring_period": 1,
        "month": date(2026, 7, 1),
        "budget_item_code": "1.1.1.1",
        "gross_wage": Decimal("25000.50"),
        "employer_contributions": Decimal("8450.17"),
        "other_with_contributions": Decimal("0"),
        "other_without_contributions": Decimal("120.40"),
        "payment_date": date(2026, 8, 12),
        "subject_id": "12345678",
        "last_name": "Nováková",
        "first_name": "Jana",
        "employment_type": "Smlouva",
        "work_time_fund": Decimal("168.000"),
        "project_hours": Decimal("84.50"),
        "description": "Projektová práce",
    }
    values.update(changes)
    return Sd2MonthlyEntry(**values)


def test_build_sd2_xml_matches_official_structure():
    content = build_sd2_xml([complete_entry()], datetime(2026, 7, 12))
    assert content.startswith(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    root = ET.fromstring(content)
    assert root.tag == f"{{{NAMESPACE}}}IMPORT"
    assert root.attrib["DATE"] == "2026-07-12T00:00:00.000"
    record = root.find(f"{{{NAMESPACE}}}SoupiskaDoklad")
    assert record is not None
    values = {node.tag.split("}")[-1]: node.text for node in record}
    assert values["ID_EXT"] == "SD2-fixed-id"
    assert values["TYPDOKLADU"] == "Mzdy"
    assert values["DATUMLZ"] == "2026-07-01T00:00:00.000"
    assert values["DRUHPRACVZTAHU"] == "Smlouva"
    assert values["MZDA"] == "25000.5"
    assert values["FONDPRACDOBY"] == "168"
    assert values["POCETHODINNAPRJ"] == "84.5"


def test_build_sd2_xml_allows_missing_optional_payment_data_and_pads_czech_id():
    content = build_sd2_xml([complete_entry(subject_id="2564546", payment_date=None)])
    assert b"<ns2:IC>02564546</ns2:IC>" in content
    assert b"DATUMUHRADY" not in content


def test_build_sd2_xml_rejects_invalid_subject_id():
    with pytest.raises(ValueError, match="IČ subjektu"):
        build_sd2_xml([complete_entry(subject_id="12A45678")])


def test_build_sd2_xml_rejects_more_than_two_amount_decimals():
    with pytest.raises(ValueError, match="nejvýše dvě desetinná místa"):
        build_sd2_xml([complete_entry(gross_wage=Decimal("1.234"))])


def test_build_sd2_xml_can_create_empty_import():
    content = build_sd2_xml([], datetime(2026, 7, 12))
    root = ET.fromstring(content)
    assert root.tag == f"{{{NAMESPACE}}}IMPORT"
    assert root.findall(f"{{{NAMESPACE}}}SoupiskaDoklad") == []
