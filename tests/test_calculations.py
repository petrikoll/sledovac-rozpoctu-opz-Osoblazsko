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


def test_transfers_can_use_a_real_surplus_from_another_direct_branch():
    items = [
        TransferCandidate(code="1.1.1.1.3", budget=Decimal("100"), spent=Decimal("140")),
        TransferCandidate(code="1.1.1.1.2", budget=Decimal("100"), spent=Decimal("50")),
        TransferCandidate(code="1.1.4.1.1", budget=Decimal("1000"), spent=Decimal("0")),
    ]

    transfers = propose_transfers(items)

    assert sum(transfer.amount for transfer in transfers) == Decimal("40.00")
    assert all(transfer.target_code == "1.1.1.1.3" for transfer in transfers)


def test_multiple_partial_transfers_respect_unit_price_cents():
    items = [
        TransferCandidate(code="target-a", budget=Decimal("578016"), spent=Decimal("621493.20"), unit_count=Decimal("36")),
        TransferCandidate(code="target-b", budget=Decimal("50000"), spent=Decimal("102500"), unit_count=Decimal("100")),
        TransferCandidate(code="source-a", budget=Decimal("100000"), spent=Decimal("69105"), unit_count=Decimal("8")),
        TransferCandidate(code="source-b", budget=Decimal("120000"), spent=Decimal("21182.16"), unit_count=Decimal("24")),
        TransferCandidate(code="source-c", budget=Decimal("85600"), spent=Decimal("70000"), unit_count=Decimal("85.6")),
        TransferCandidate(code="source-d", budget=Decimal("200880"), spent=Decimal("150000"), unit_count=Decimal("216")),
    ]

    transfers = propose_transfers(items)

    assert sum(t.amount for t in transfers) == Decimal("95977.20")
    by_source = {}
    for transfer in transfers:
        by_source[transfer.source_code] = by_source.get(transfer.source_code, Decimal("0")) + transfer.amount
    by_code = {item.code: item for item in items}
    for code, moved in by_source.items():
        proposed_price = (by_code[code].budget - moved) / by_code[code].unit_count
        assert proposed_price == proposed_price.quantize(Decimal("0.01"))


def test_target_can_receive_small_reserve_to_fit_unit_price():
    items = [
        TransferCandidate(code="target", budget=Decimal("100"), spent=Decimal("110.01"), unit_count=Decimal("3")),
        TransferCandidate(code="source", budget=Decimal("100"), spent=Decimal("0"), unit_count=Decimal("1")),
    ]

    transfers = propose_transfers(items)

    assert sum(transfer.amount for transfer in transfers) == Decimal("10.02")


def test_final_settlement():
    result = final_settlement(Decimal("949613"), Decimal("379845.2"), Decimal("0"), Decimal("0"), Decimal(".95"), Decimal("1258286.4"), Decimal("0"))
    assert result["provider_entitlement"] == Decimal("1262985.29")
    assert result["settlement"] == Decimal("4698.89")


def test_final_settlement_refund_after_final_payment_request():
    result = final_settlement(
        Decimal("5583706.14"), Decimal("1403426.54"), Decimal("0"),
        Decimal("0"), Decimal("1"), Decimal("7476098.00"), Decimal("0"),
    )
    assert result["eligible_total"] == Decimal("6987132.68")
    assert result["settlement"] == Decimal("-488965.32")


def test_newer_cumulative_lump_sum_replaces_previous_state():
    from datetime import date
    from app.models import LumpSumEntry

    entries = [
        LumpSumEntry(monitoring_period="2", entry_date=date(2026, 4, 30), entry_mode="cumulative", entered_amount=Decimal("100000")),
        LumpSumEntry(monitoring_period="3", entry_date=date(2026, 7, 11), entry_mode="cumulative", entered_amount=Decimal("151000")),
    ]
    result = lump_sum_metrics(Decimal("500000"), Decimal("400000"), Decimal("0.40"), entries)
    assert result["spent"] == Decimal("151000.00")
