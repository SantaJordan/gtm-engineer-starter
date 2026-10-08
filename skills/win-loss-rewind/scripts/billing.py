"""The billing contract: which dossier billing events count as money received.

Dossier billing events follow the dossier-builder's documented format
(skills/dossier-builder/prompts/02b-source-pull.billing.md): `source =
"billing_event"`, the billing system's verbatim `type`, and `fields` with an
`amount` already converted to USD (the source currency kept in
`original_currency` / `original_amount`).

Rules (explicit, never a substring match):
  * Only SUCCESS_TYPES add revenue; REFUND_TYPES subtract it.
  * Every other type (invoice_unpaid, invoice_open, invoice_void,
    payment_failed, subscription_created, ...) adds nothing and is counted.
  * Amount: an explicit `fields.amount_usd` wins; else `fields.amount` (USD by
    the builder contract); else `amount_cents` / 100. Strings like
    "$1,200.00" are parsed.
  * A `fields.currency` other than USD on an event with no USD amount is not
    summed (no exchange rate here); it is counted as `non_usd`.
"""
from __future__ import annotations

import re

# payment_succeeded and payment_retry_succeeded are the two success types the
# builder's billing prompt emits; the rest are common billing-system spellings.
SUCCESS_TYPES = frozenset({"payment_succeeded", "payment_retry_succeeded",
                           "invoice_paid", "charge_succeeded", "payment_received",
                           "invoice_payment_succeeded"})
REFUND_TYPES = frozenset({"refund_issued", "charge_refunded", "refund"})


def _norm_type(t) -> str:
    return re.sub(r"[\s.\-]+", "_", str(t or "").strip().lower())


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[,$\s]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return None


def amount_usd(fields: dict):
    """USD amount of one event, or None when it cannot be read as USD."""
    f = fields or {}
    explicit = _num(f.get("amount_usd"))
    if explicit is not None:
        return explicit                  # an explicit USD amount always wins
    currency = str(f.get("currency") or "USD").upper()
    converted = "original_currency" in f or "currency_original" in f
    n = _num(f.get("amount"))
    if n is not None:
        if currency != "USD" and not converted:
            return None                  # an unconverted foreign amount
        return n
    cents = _num(f.get("amount_cents") or f.get("amount_in_cents"))
    if cents is not None and currency == "USD":
        return cents / 100.0
    return None


def summarize(events: list[dict]) -> dict:
    """{revenue, n_paid, first_paid, last_paid, skipped_types, non_usd} over billing events."""
    revenue, paid_dates, skipped, non_usd = 0.0, [], {}, 0
    for e in events:
        if e.get("source") != "billing_event":
            continue
        t = _norm_type(e.get("type"))
        if t not in SUCCESS_TYPES and t not in REFUND_TYPES:
            skipped[t] = skipped.get(t, 0) + 1
            continue
        amt = amount_usd(e.get("fields") or {})
        if amt is None:
            non_usd += 1
            continue
        if t in SUCCESS_TYPES:
            revenue += amt
            paid_dates.append(str(e.get("date") or "")[:10])
        else:
            revenue -= abs(amt)
    paid_dates = sorted(d for d in paid_dates if d)
    return {"revenue": round(revenue, 2), "n_paid": len(paid_dates),
            "first_paid": paid_dates[0] if paid_dates else "",
            "last_paid": paid_dates[-1] if paid_dates else "",
            "skipped_types": skipped, "non_usd": non_usd}
