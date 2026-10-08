"""One scoring function for calibration (Phase 7) and prospect scoring (Phase 8).

The threshold is calibrated on training panels with `score()`, and prospects are
scored with the same `score()`, so a panel that fits in training fits in
production. Unobserved signals (state "unknown": the source never ran, failed,
or the field was not extracted) leave the denominator rather than counting as
absent. `coverage` is the share of rubric weight that was observed. A panel
with no observed signal at all is `insufficient_evidence`, never a "no".
"""
from __future__ import annotations

from signal_presence import PRESENT, UNKNOWN, signal_state


def score(rubric: dict, panel: dict) -> dict:
    diff, got, observed, total = [], 0.0, 0.0, 0.0
    for sig in (rubric or {}).get("signals") or []:
        state = signal_state(sig, panel)
        w = float(sig.get("weight") or 0.0)
        total += w
        diff.append({"signal": sig.get("signal"), "source": sig.get("observability_source"),
                     "field": sig.get("queryable_field"), "weight": sig.get("weight"),
                     "present": state == PRESENT, "state": state})
        if state == UNKNOWN:
            continue
        observed += w
        if state == PRESENT:
            got += w
    return {"score": (got / observed) if observed > 0 else 0.0,
            "coverage": (observed / total) if total > 0 else 0.0,
            "insufficient_evidence": observed == 0,
            "n_unknown": sum(1 for d in diff if d["state"] == UNKNOWN),
            "diff": diff}


def verdict(rubric: dict, panel: dict, threshold: float) -> dict:
    """score() plus the decision: looks_like, not_like, or insufficient_evidence."""
    s = score(rubric, panel)
    s["threshold"] = threshold
    if s["insufficient_evidence"]:
        s["status"], s["looks_like"] = "insufficient_evidence", False
    else:
        s["looks_like"] = s["score"] >= threshold
        s["status"] = "looks_like" if s["looks_like"] else "not_like"
    return s
