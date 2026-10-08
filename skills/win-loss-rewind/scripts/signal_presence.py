#!/usr/bin/env python3
"""One presence brain for Phases 5, 6, 7 and 8: typed signal predicates.

A signal (a Phase 5 hypothesis) names a source, a field, and a predicate:

    {"observability_source": "firecrawl_scrape",
     "queryable_field": "location_count",
     "predicate": {"op": "gte", "value": 2}}

or, for a source that returns dated RECORDS (news items, search results):

    {"observability_source": "serper_news",
     "queryable_field": "items.snippet",
     "predicate": {"op": "regex", "value": "opened (a|our) (new|second) office"}}

Rules:
  * The field must be one the Phase 2 parser actually emits
    (`panel_parsers.FIELD_TYPES` / `RECORD_FIELDS`). Anything else is an
    unsupported selector and is rejected at Gate 4, never silently "absent".
  * The predicate must fit the field's type. A numeric field needs a threshold
    (gte/gt/lte/lt/eq/ne); "100 employees" is not "true". A bare boolean field
    may omit the predicate (it means truthy).
  * Evaluation is tri-state, with a reason:
        present  - observed, and the predicate holds
        absent   - observed, and the predicate does not hold
        unknown  - the source never ran or failed, OR the parser could not
                   extract the field (value None): "not extracted" is never
                   "absent".
"""
from __future__ import annotations

import re

from panel_parsers import FIELD_TYPES, RECORD_FIELDS  # noqa: F401 (re-exported)

PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"

NUMERIC = ("int", "float")
NUMERIC_OPS = ("gte", "gt", "lte", "lt", "eq", "ne")
TEXT_OPS = ("contains", "regex", "eq", "ne")
BOOL_OPS = ("truthy", "falsy", "eq")
LIST_OPS = ("nonempty", "contains")
OPS_BY_TYPE = {"int": NUMERIC_OPS, "float": NUMERIC_OPS, "str": TEXT_OPS, "bool": BOOL_OPS,
               "list": LIST_OPS, "date": ("gte", "lte")}


def _split(sig: dict):
    src = (sig.get("observability_source") or "").strip()
    field = (sig.get("queryable_field") or "").strip()
    if field.startswith("data."):
        field = field[len("data."):]
    return src, field


def field_type(sig: dict):
    """(kind, type, records_key, record_field). kind is "field", "record" or None."""
    src, field = _split(sig)
    types = FIELD_TYPES.get(src)
    if types is None or not field:
        return None, None, None, None
    if field in types and types[field] != "records":
        return "field", types[field], None, None
    head, _, tail = field.partition(".")
    rec = RECORD_FIELDS.get((src, head))
    if rec is not None and tail in rec:
        return "record", rec[tail], head, tail
    return None, None, None, None


def predicate_of(sig: dict, ftype: str | None) -> dict | None:
    pred = sig.get("predicate")
    if pred is None and ftype == "bool":
        return {"op": "truthy"}
    return pred if isinstance(pred, dict) else None


def validate_signal(sig: dict) -> str | None:
    """None if the signal is testable; otherwise the reason it is not."""
    src, field = _split(sig)
    if src not in FIELD_TYPES:
        return f"unsupported source {src!r}: no Phase 2 parser emits it"
    kind, ftype, _, _ = field_type(sig)
    if kind is None:
        return (f"unsupported selector {src}.{field}: the {src} parser emits "
                f"{sorted(FIELD_TYPES[src])}")
    pred = predicate_of(sig, ftype)
    if pred is None:
        if ftype in NUMERIC:
            return f"{src}.{field} is numeric: it needs a predicate with a threshold (e.g. gte 2)"
        return f"{src}.{field} ({ftype}) needs a predicate"
    op = pred.get("op")
    if op not in OPS_BY_TYPE.get(ftype, ()):
        return f"predicate op {op!r} does not fit a {ftype} field (allowed: {OPS_BY_TYPE.get(ftype)})"
    if ftype in NUMERIC and op in NUMERIC_OPS and not isinstance(pred.get("value"), (int, float)):
        return f"predicate on numeric {src}.{field} needs a numeric value"
    if op == "regex":
        try:
            re.compile(str(pred.get("value")))
        except re.error as exc:
            return f"bad regex: {exc}"
    return None


def _holds(value, ftype: str, pred: dict) -> bool:
    op, want = pred.get("op"), pred.get("value")
    if op == "truthy":
        return bool(value)
    if op == "falsy":
        return not value
    if op == "nonempty":
        return bool(value)
    if op == "eq":
        return value == want
    if op == "ne":
        return value != want
    if ftype in NUMERIC:
        v = float(value)
        return {"gte": v >= want, "gt": v > want, "lte": v <= want, "lt": v < want}[op]
    if op == "contains":
        if isinstance(value, list):
            return any(str(want).lower() in str(x).lower() for x in value)
        return str(want).lower() in str(value).lower()
    if op == "regex":
        return re.search(str(want), str(value), re.I) is not None
    if ftype == "date":
        return (str(value) >= str(want)) if op == "gte" else (str(value) <= str(want))
    return False


def _blob(sig: dict, panel: dict):
    src, _ = _split(sig)
    sigs = panel.get("signal_panel") or panel.get("signals") or {}
    if not isinstance(sigs, dict) or src not in sigs:
        return None, "source_missing"
    blob = sigs[src]
    if not isinstance(blob, dict) or blob.get("success") is False or \
            (isinstance(blob.get("error"), str) and blob["error"].strip()):
        return None, "source_failed"
    data = blob.get("data") if isinstance(blob.get("data"), dict) else blob
    return data, None


def evaluate(sig: dict, panel: dict) -> dict:
    """{"state", "reason", "value", "matches"}; `matches` = records satisfying the predicate."""
    err = validate_signal(sig)
    if err:
        return {"state": UNKNOWN, "reason": f"invalid_signal: {err}", "value": None, "matches": []}
    data, why = _blob(sig, panel)
    if data is None:
        return {"state": UNKNOWN, "reason": why, "value": None, "matches": []}
    kind, ftype, rkey, rfield = field_type(sig)
    pred = predicate_of(sig, ftype)
    if kind == "field":
        value = data.get(_split(sig)[1])
        if value is None:
            return {"state": UNKNOWN, "reason": "not_extracted", "value": None, "matches": []}
        return {"state": PRESENT if _holds(value, ftype, pred) else ABSENT,
                "reason": "evaluated", "value": value, "matches": []}
    records = data.get(rkey)
    if records is None:
        return {"state": UNKNOWN, "reason": "not_extracted", "value": None, "matches": []}
    observed = [r for r in records if isinstance(r, dict) and r.get(rfield) is not None]
    matches = [r for r in observed if _holds(r.get(rfield), ftype, pred)]
    if matches:
        return {"state": PRESENT, "reason": "evaluated", "value": len(matches), "matches": matches}
    if not observed and records:
        return {"state": UNKNOWN, "reason": "not_extracted", "value": None, "matches": []}
    return {"state": ABSENT, "reason": "evaluated", "value": 0, "matches": []}


def signal_state(sig: dict, panel: dict) -> str:
    return evaluate(sig, panel)["state"]


def signal_present(sig: dict, panel: dict) -> bool:
    """True only for an observed signal whose predicate holds. Unknown reads as False."""
    return signal_state(sig, panel) == PRESENT


def signal_present_tristate(sig: dict, panel: dict) -> bool | None:
    """True / False / None, where None means not observed or not extracted."""
    state = signal_state(sig, panel)
    return None if state == UNKNOWN else state == PRESENT


def record_date_field(sig: dict) -> str | None:
    """The date field of the record type a record-level signal reads."""
    kind, _, rkey, _ = field_type(sig)
    if kind != "record":
        return None
    src, _ = _split(sig)
    for name, t in RECORD_FIELDS[(src, rkey)].items():
        if t == "date":
            return name
    return None
