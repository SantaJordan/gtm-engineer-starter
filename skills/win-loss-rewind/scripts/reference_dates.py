#!/usr/bin/env python3
"""
Reference dates + evidence-visibility tags — shared by Phases 2/6/7.

The backcast question is always anchored to a REFERENCE DATE per customer:
"what was provably visible at reference_date − hypothesized_lead_time_days?"

Anchor rule per outcome (see references/time-travel.md § Reference dates):
    won      → close_date
    lost     → close_date, else first_call_date (low confidence)
    churned  → churn_date if the roster carries one, else close_date
               (approximation — close_date is the SIGNING date, and precursor
               signals for churn should anchor to signing anyway: the question
               is "was this a bad fit we could have seen before we signed them")
    healthy  → renewal_date if present, else close_date (their signing date —
               deliberately NOT "today": healthy is functionally still-won)

Evidence-visibility tags (the honesty core — see references/time-travel.md):
    ARCHIVED_BEFORE      an archive.org capture at-or-before the reference
                         date shows the signal. FIRM.
    RECORD_DATED_BEFORE  the record's own intrinsic date (filing date, permit
                         date, publish date) is at-or-before the reference
                         date, after any publication-lag haircut. FIRM.
    RETRO_ASSUMED        only today's web was observed; visibility at the
                         reference date is an assumption. NEVER firm.

Only the two FIRM tags may count as signal-present in firm-lift computation.
"""

from __future__ import annotations

from datetime import date, datetime

# Canonical tag constants — rendered in markdown as [ARCHIVED-BEFORE] etc.
ARCHIVED_BEFORE = "ARCHIVED_BEFORE"
RECORD_DATED_BEFORE = "RECORD_DATED_BEFORE"
RETRO_ASSUMED = "RETRO_ASSUMED"
FIRM_TAGS = {ARCHIVED_BEFORE, RECORD_DATED_BEFORE}
ALL_TAGS = FIRM_TAGS | {RETRO_ASSUMED}


def tag_to_markdown(tag: str) -> str:
    """ARCHIVED_BEFORE → [ARCHIVED-BEFORE] (the bracket convention used in reports)."""
    return f"[{tag.replace('_', '-')}]" if tag in ALL_TAGS else f"[{tag}]"


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d",
                 "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y")


def parse_date(value) -> date | None:
    """Lenient date parse across the formats real CRM exports use."""
    if value in (None, "", 0):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # ISO with time component
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def resolve_reference_date(row: dict) -> tuple[date | None, str]:
    """
    Anchor rule per outcome. `row` is a customer roster row OR a panel row
    (both carry outcome + close_date; churn/renewal/first-call dates only if
    the operator's CSV had them — Phase 0 interrogation asks).

    Returns (reference_date | None, provenance_note). None = unanchorable;
    Phase 6 must treat that customer's cells as unknown, never guess.
    """
    outcome = (row.get("outcome") or row.get("deal_outcome") or "").strip().lower()
    close = parse_date(row.get("close_date"))
    churn = parse_date(row.get("churn_date") or row.get("cancellation_date"))
    renewal = parse_date(row.get("renewal_date") or row.get("last_renewal_date"))
    first_call = parse_date(row.get("first_call_date"))

    if outcome in ("won", "10x"):
        if close:
            return close, "close_date"
    elif outcome == "lost":
        if close:
            return close, "close_date"
        if first_call:
            return first_call, "first_call_date (LOW CONFIDENCE — lost rows often lack close_date)"
    elif outcome == "churned":
        # Precursor signals anchor to SIGNING — "could we have seen the bad
        # fit before we signed them" — so close_date is primary; a distinct
        # churn_date is kept as provenance when close_date is missing.
        if close:
            return close, "close_date (signing)"
        if churn:
            return churn, "churn_date (no signing date on row — approximation)"
    elif outcome == "healthy":
        if close:
            return close, "close_date (signing — healthy is functionally still-won)"
        if renewal:
            return renewal, "renewal_date (no signing date on row)"
    else:
        # Unknown outcome — take close_date if present, flagged.
        if close:
            return close, f"close_date (outcome {outcome!r} unrecognized)"

    return None, f"unanchorable: outcome={outcome!r} with no usable date column"


def knowable_by(record_date: date | None, publication_lag_days: int = 0) -> date | None:
    """
    The date the WORLD could see a dated record = record's intrinsic date plus
    the source's publication-lag haircut (a permit filed in March may not show
    in the portal until June). Lag per source lives in
    references/source-temporal-classification.json.
    """
    if record_date is None:
        return None
    from datetime import timedelta
    return record_date + timedelta(days=publication_lag_days)


def strike_window_days(reference_date: date | None, knowable_by_date: date | None) -> int | None:
    """Days between the moment the signal became knowable and the reference date."""
    if reference_date is None or knowable_by_date is None:
        return None
    return (reference_date - knowable_by_date).days
