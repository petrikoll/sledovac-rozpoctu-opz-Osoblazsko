from datetime import date
from decimal import Decimal

from app.calculations import final_settlement, lump_sum_metrics, propose_transfers
from app.models import LumpSumEntry, TransferCandidate


def test_cumulative_lump_sum_not_double_counted():
    entries = [LumpSumEntry(monitoring_period="1", entry_date=date(2025, 1, 1), entry_mode="period", entered_amount=Decimal("100000")),
               LumpSumEntry(monitoring_period="2", entry_date=date(2025, 6, 1), entry_mode="cumulative", entered_amount=Decimal("150000"))]
    result = lump_sum_metrics(Decimal("1261440"), Decimal("949613"), Decimal("0.4"), entries)
    assert result["entitlement"] == Decimal("379845.20")
    assert result["spent"] == Decimal("150000.00")


def test_transfers_are_balanced_and_respect_lock():
    items = [TransferCandidate(code="A", budget=Decimal("100"), spent=Decimal("140")),
             TransferCandidate(code="B", budget=Decimal("100"), spent=Decimal("20")),
             TransferCandidate(code="C", budget=Decimal("100"), spent=Decimal("0"), locked=True)]
    transfers = propose_transfers(items)
    assert sum(x.amount for x in transfers) == Decimal("40.00")
    assert all(x.source_code != "C" for x in transfers)


def test_final_settlement():
    result = final_settlement(Decimal("949613"), Decimal("379845.2"), Decimal("0"), Decimal("0"), Decimal(".95"), Decimal("1258286.4"), Decimal("0"))
    assert result["provider_entitlement"] == Decimal("1262985.29")
    assert result["settlement"] == Decimal("4698.89")


def test_newer_cumulative_lump_sum_replaces_previous_state():
    from datetime import date
    from app.models import LumpSumEntry

    entries = [
        LumpSumEntry(monitoring_period="2", entry_date=date(2026, 4, 30), entry_mode="cumulative", entered_amount=Decimal("100000")),
        LumpSumEntry(monitoring_period="3", entry_date=date(2026, 7, 11), entry_mode="cumulative", entered_amount=Decimal("151000")),
    ]
    result = lump_sum_metrics(Decimal("500000"), Decimal("400000"), Decimal("0.40"), entries)
    assert result["spent"] == Decimal("151000.00")
