from decimal import Decimal

from .models import LumpSumEntry, Transfer, TransferCandidate

CENT = Decimal("0.01")


def q(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def lump_sum_metrics(budget: Decimal, direct_approved: Decimal, rate: Decimal,
                     entries: list[LumpSumEntry]) -> dict[str, Decimal]:
    spent = Decimal("0")
    for entry in sorted(entries, key=lambda e: (e.entry_date, e.monitoring_period)):
        spent = spent + entry.entered_amount if entry.entry_mode == "period" else entry.entered_amount
    entitlement = q(direct_approved * rate)
    return {"budget": q(budget), "entitlement": entitlement, "spent": q(spent),
            "available": q(entitlement - spent), "remaining_budget": q(budget - spent),
            "not_yet_entitled": q(budget - entitlement)}


def propose_transfers(items: list[TransferCandidate], reserve_rate: Decimal = Decimal("0")) -> list[Transfer]:
    deficits = sorted(((x, x.spent - x.budget) for x in items if x.spent > x.budget),
                      key=lambda pair: (-pair[1], pair[0].code))
    donors = sorted(items, key=lambda x: (x.donor_priority, -(x.budget-x.spent), x.code))
    result: list[Transfer] = []
    for target, deficit in deficits:
        need = q(deficit * (Decimal("1") + reserve_rate))
        for source in donors:
            if need <= 0 or source.code == target.code or source.locked:
                continue
            already = sum(t.amount for t in result if t.source_code == source.code)
            available = q(source.budget-source.spent-source.planned-source.minimum_remaining-already)
            if available <= 0:
                continue
            amount = min(need, available)
            result.append(Transfer(source_code=source.code, target_code=target.code, amount=q(amount)))
            need -= amount
    return result


def final_settlement(direct: Decimal, lump_sum: Decimal, other: Decimal, income: Decimal,
                     funding_rate: Decimal, payments: Decimal, refunds: Decimal) -> dict[str, Decimal]:
    eligible = q(direct + lump_sum + other)
    base = q(eligible - income)
    entitlement = q(base * funding_rate)
    received = q(payments - refunds)
    return {"eligible_total": eligible, "financing_base": base, "provider_entitlement": entitlement,
            "net_received": received, "settlement": q(entitlement - received)}
