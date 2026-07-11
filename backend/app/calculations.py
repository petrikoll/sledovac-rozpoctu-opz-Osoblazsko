from decimal import Decimal
from math import gcd

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


def _quantum_cents(unit_count: Decimal | None) -> int:
    """Nejmenší změna celku v haléřích při ceně jednotky na 2 místa."""
    if not unit_count or unit_count <= 0:
        return 1
    for price_cents in range(1, 10001):
        total_cents = unit_count * price_cents
        if total_cents == total_cents.to_integral_value():
            return int(total_cents)
    return 1


def _exact_donor_allocations(donors: list[TransferCandidate], total_cents: int) -> dict[str, int]:
    available = []
    for donor in donors:
        raw_cents = int(max(Decimal("0"), donor.budget-donor.spent-donor.planned-donor.minimum_remaining) * 100)
        quantum = _quantum_cents(donor.unit_count)
        capacity = raw_cents - raw_cents % quantum
        if capacity > 0 and not donor.locked:
            available.append((donor, quantum, capacity))
    if sum(capacity for _, _, capacity in available) < total_cents:
        return {}
    available.sort(key=lambda value: (value[0].donor_priority, -value[2], value[0].code))
    # Malý krok si necháme jako dorovnávací zdroj, ostatní použijeme dle priority.
    filler_index = min(range(len(available)), key=lambda i: available[i][1])
    filler, filler_q, filler_capacity = available.pop(filler_index)
    need_from_others = max(0, total_cents - filler_capacity)
    allocations: dict[str, int] = {}
    for donor, quantum, capacity in available:
        amount = min(capacity, need_from_others)
        amount -= amount % quantum
        allocations[donor.code] = amount
        need_from_others -= amount
    if need_from_others > 0:
        return {}
    used_other = sum(allocations.values())
    filler_need = total_cents - used_other
    if filler_need <= filler_capacity and filler_need % filler_q == 0:
        allocations[filler.code] = filler_need
        return {code: amount for code, amount in allocations.items() if amount}
    # Uvolníme po několika krocích z dříve vybraných zdrojů, dokud
    # zbytek není přesně dělitelný krokem dorovnávací položky.
    states: dict[int, tuple[int, dict[str, int]]] = {filler_need % filler_q: (0, {})}
    for donor, quantum, _capacity in available:
        allocated = allocations.get(donor.code, 0)
        if not allocated:
            continue
        period = filler_q // gcd(filler_q, quantum)
        next_states = dict(states)
        for residue, (added, reductions) in states.items():
            for steps in range(1, min(allocated // quantum, period) + 1):
                extra = steps * quantum
                new_added = added + extra
                new_residue = (residue + extra) % filler_q
                if filler_need + new_added > filler_capacity:
                    continue
                if new_residue not in next_states or new_added < next_states[new_residue][0]:
                    next_states[new_residue] = (new_added, {**reductions, donor.code: extra})
        states = next_states
    if 0 not in states:
        return {}
    added, reductions = states[0]
    for code, reduction in reductions.items():
        allocations[code] -= reduction
    allocations[filler.code] = filler_need + added
    return {code: amount for code, amount in allocations.items() if amount}


def propose_transfers(items: list[TransferCandidate], reserve_rate: Decimal = Decimal("0")) -> list[Transfer]:
    deficits = []
    for item in items:
        if item.spent <= item.budget:
            continue
        raw_cents = int((q((item.spent-item.budget) * (Decimal("1") + reserve_rate)) * 100).to_integral_value())
        quantum = _quantum_cents(item.unit_count)
        rounded_cents = ((raw_cents + quantum - 1) // quantum) * quantum
        deficits.append((item, Decimal(rounded_cents) / 100))
    deficits.sort(key=lambda pair: (-pair[1], pair[0].code))
    if not deficits:
        return []
    total_cents = sum(int(need * 100) for _, need in deficits)
    deficit_codes = {target.code for target, _ in deficits}
    donors = [item for item in items if item.code not in deficit_codes]
    allocations = _exact_donor_allocations(donors, total_cents)
    if sum(allocations.values()) != total_cents:
        return []
    result: list[Transfer] = []
    donor_queue = [[code, cents] for code, cents in allocations.items()]
    for target, need in deficits:
        remaining = int(need * 100)
        for donor in donor_queue:
            if remaining <= 0:
                break
            amount = min(remaining, donor[1])
            if amount:
                result.append(Transfer(source_code=donor[0], target_code=target.code,
                                       amount=Decimal(amount) / 100))
                donor[1] -= amount
                remaining -= amount
    return result


def final_settlement(direct: Decimal, lump_sum: Decimal, other: Decimal, income: Decimal,
                     funding_rate: Decimal, payments: Decimal, refunds: Decimal) -> dict[str, Decimal]:
    eligible = q(direct + lump_sum + other)
    base = q(eligible - income)
    entitlement = q(base * funding_rate)
    received = q(payments - refunds)
    return {"eligible_total": eligible, "financing_base": base, "provider_entitlement": entitlement,
            "net_received": received, "settlement": q(entitlement - received)}
