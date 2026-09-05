"""One interpretation of payment revisions and approval state for every view."""
import re
import unicodedata


def normalized_status(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def is_approved(payment) -> bool:
    status = normalized_status(f"{payment.state} {payment.processing_state}")
    return bool(re.search(r"\b(proplacena|vyporadana|schvalena)\b", status))


def is_paid(payment) -> bool:
    status = normalized_status(f"{payment.state} {payment.processing_state}")
    return bool(re.search(r"\b(proplacena|vyporadana)\b", status))


def active_payments(payments):
    """Old revisions remain available for inspection, never for addition."""
    latest = {}
    for payment in payments:
        key = payment.request_number.strip() or f"sequence:{payment.sequence_number}"
        previous = latest.get(key)
        if previous is None or payment.request_version > previous.request_version:
            latest[key] = payment
    return list(latest.values())
