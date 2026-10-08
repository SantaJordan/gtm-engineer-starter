#!/usr/bin/env python3
"""The FALLBACK composer — used only while `bakeoff.composer` does not exist.

It implements the same contract as the bake-off's composer v2, because the two
have to be interchangeable:

  * THREE outcomes — `accept`, `reject`, `insufficient`. Nothing else.
  * A review reason beats BOTH an accept and a reject. Uncertainty is routed to
    a human; it is never spent as a rejection.
  * NO manufactured probability. `final_probability` is always None; there is
    no support-fraction arithmetic pretending to be a calibrated number.
    `decision_score` is a probability the model actually returned, carried
    through unchanged.
  * A rejection needs a SPECIFIC named claim above its own threshold, and
    rejecting a candidate is never an absence claim about the world.

When `bakeoff.composer` lands, `core` prefers it and this module steps aside;
`composer_version` in the log records which one decided.
"""
from __future__ import annotations

from . import config

VERSION = "judge-min-1"

#: Choice/score options that read as "yes" for a question asked affirmatively.
AFFIRMATIVE = ("true", "yes", "accept", "accept_candidate", "same_entity_or_dba",
               "current", "match")


def answer_value(ans) -> tuple[str, float | None, dict | None, float | None]:
    """One jev answer -> (kind, affirmative probability, probs, confidence).

    The affirmative probability is the number a threshold is applied to. For a
    noul it is P(true). For a choice or a score it is the winning option's
    probability *when that option reads as yes* — a confident "no" is not
    weak evidence for "yes", so it returns 0.0, and an unreadable answer
    returns None (unavailable, never False).
    """
    if not isinstance(ans, dict):
        return "unknown", None, None, None
    kind = str(ans.get("type") or "noul").strip().lower()

    if kind in ("noul", "boolean", "bool"):
        p = ans.get("probability")
        if p is None:
            p = ans.get("p", ans.get("p_true"))
        try:
            return "noul", (None if p is None else float(p)), None, None
        except (TypeError, ValueError):
            return "noul", None, None, None

    probs = ans.get("probabilities")
    probs = {str(k): float(v) for k, v in probs.items()} if isinstance(probs, dict) else None
    conf = ans.get("confidence")
    if conf is None and probs:
        conf = max(probs.values())
    try:
        conf = None if conf is None else float(conf)
    except (TypeError, ValueError):
        conf = None

    chosen = ans.get("choice")
    if chosen is None and probs:
        chosen = max(probs, key=probs.get)
    chosen = str(chosen or "").strip().lower()
    if not chosen or conf is None:
        return kind, None, probs, conf
    return kind, (conf if chosen in AFFIRMATIVE else 0.0), probs, conf


def question_role(q, decision) -> str:
    """-> support | reject | review | meta, across BOTH battery schemas.

    v2 says it outright: `role` in {support, guard, meta, review} and `action`
    in {reject, review}. v1 only has `polarity`, so the list of guards that may
    reject rather than merely flag lives in `config.REJECT_CLAIMS` — the same
    doctrine, written down where v1 could not write it.
    """
    role = str(q.get("role") or "").strip().lower()
    action = str(q.get("action") or "").strip().lower()
    if role:
        if role == "support":
            return "support"
        if role == "meta":
            return "meta"
        if role == "guard":
            return "reject" if action == "reject" else "review"
        return "review"
    if q.get("polarity", "good") == "bad":
        if q["id"] == "evidence_insufficient":
            return "meta"
        return ("reject" if q["id"] in config.REJECT_CLAIMS.get(decision, ())
                else "review")
    return "support"


def compose(decision, questions, answers, facts=None, *, battery=None,
            thresholds=None, **_ignored) -> dict:
    """questions: the battery's question dicts. answers: jev-shaped answers."""
    th = thresholds or config.thresholds()
    facts = facts or {}

    raw_probabilities, values = {}, {}
    for q in questions:
        qid = q["id"]
        if qid not in answers:
            continue                       # unavailable — never False
        kind, p, probs, conf = answer_value(answers[qid])
        if p is None and probs is None:
            continue
        raw_probabilities[qid] = probs if probs is not None else p
        values[qid] = {"q": q, "p": p, "probs": probs, "confidence": conf}

    review_reasons, rejections, supports = [], [], []
    for qid, v in values.items():
        role = question_role(v["q"], decision)
        p = v["p"]
        if p is None:
            continue
        if role == "reject":
            if p >= th["reject_claim"]:
                rejections.append((p, qid))
        elif role in ("review", "meta"):
            if p >= th["review_trigger"]:
                review_reasons.append(qid)
        elif p >= th["accept_support"]:
            supports.append((p, qid))

    # Facts the builder computed in code can also demand a human. They are
    # reasons to look, never reasons to reject.
    if facts.get("profile_empty") is True:
        review_reasons.append("empty_profile")
    if facts.get("visible_text_truncated") is True:
        review_reasons.append("evidence_truncated")
    if facts.get("injection_flags"):
        review_reasons.append("injection")

    out = {"outcome": "insufficient", "verdict": None, "decision_score": None,
           "final_probability": None, "why": "", "review_reasons": [],
           "raw_probabilities": raw_probabilities, "rejection_claim": None,
           "composer_version": VERSION}

    if review_reasons:
        out["review_reasons"] = sorted(set(review_reasons))
        out["why"] = ("a review reason outranks both an accept and a reject: "
                      + ", ".join(out["review_reasons"]))
    elif rejections:
        p, qid = max(rejections)
        out.update(outcome="reject", decision_score=p, rejection_claim=qid,
                   why=f"{qid} at {p:.2f} clears the rejection claim band")
    elif supports:
        p = min(s[0] for s in supports)
        out.update(outcome="accept", decision_score=p,
                   why=(f"{len(supports)} support question(s) cleared "
                        f"{th['accept_support']:.2f}; the weakest is {p:.2f}"))
    else:
        out["why"] = ("no support question cleared the accept band and no "
                      "rejection claim was made — the honest answer is a "
                      "rejection claim nobody can support")

    out["verdict"] = verdict_for(decision, out, battery=battery)
    return out


def verdict_for(decision, out, battery=None) -> str | None:
    """The outcome, spoken in the decision's own vocabulary."""
    if out.get("verdict") is not None and battery is None:
        return out["verdict"]
    bat = battery or {}
    outcome = out.get("outcome")
    if outcome == "accept":
        return bat.get("accept_verdict")
    if outcome == "reject":
        # Disqualifying THIS candidate is not proof that none exists.
        if decision in config.ABSENCE_VERDICTS:
            return config.EVIDENCE_INSUFFICIENT
        return bat.get("reject_verdict")
    return config.UNSURE_VERDICT.get(decision, config.EVIDENCE_INSUFFICIENT)
