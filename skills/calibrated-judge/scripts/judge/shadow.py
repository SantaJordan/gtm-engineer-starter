#!/usr/bin/env python3
"""The hook seam. THE SHADOW CONTRACT LIVES HERE.

A pipeline calls exactly one thing:

    from judge.shadow import observe

    out = observe("real_website", evidence,
                  incumbent_verdict=verdict, incumbent_confidence=conf,
                  skill="linkedin-validator", hook_point="post_verdict",
                  entity_key=row_id)
    verdict = out["verdict"]        # in off and shadow: byte-identical to input

What `observe` guarantees, in every mode:

  off      (the default) the judge is not called, nothing is logged, nothing is
           spent, and `verdict` is what you passed in.
  shadow   the incumbent has ALREADY decided by the time this runs. The judge
           answers, both verdicts and their agreement are logged, and the
           incumbent's verdict is returned UNCHANGED. `overridden` is False.
  active   the judge's verdict may replace the incumbent's — and only when the
           judge actually reached one, and only for a decision named in
           `CALIBRATED_JUDGE_ACTIVE_DECISIONS`. An unlisted decision rides
           `shadow` however the global mode is set (config.effective_mode).

And the rule that outranks all three: **this function cannot raise.** A judge
that explodes, a disk that is full, a battery that will not load — each becomes
a logged `judge_unavailable` and the incumbent's verdict goes back untouched.
An observer that can break the thing it observes is worse than no observer.
"""
from __future__ import annotations

import uuid

from . import config, log
from .core import JudgeResult, judge

#: Actions that are allowed to replace an incumbent in `active`. `review` and
#: `insufficient` never override: "I am not sure" is not a verdict.
OVERRIDING_ACTIONS = ("accept", "reject")


def _unavailable(detail=""):
    return JudgeResult(verdict=None, action="insufficient", decision_score=None,
                       per_question={}, review_reasons=["judge_unavailable"],
                       model=None, backend=None, battery_version="unknown",
                       composer_version="none", latency_ms=0, cost_usd=0.0,
                       evidence_sha256="", cached=False,
                       error="judge_unavailable", why=str(detail)[:300])


def observe(decision, evidence, *, incumbent_verdict, incumbent_confidence=None,
            skill, hook_point, entity_key=None, run_id=None, icp=None,
            persona=None, mode=None, enrich=None) -> dict:
    """Run the judge beside a decision the incumbent has already made.

    `enrich` is passed straight through to `judge()` (see its docstring): a
    thin packet that came back unsure is rebuilt once and asked once more.
    When that happens BOTH rounds reach the decision log, in order, so the
    file answers "what did it know when it said that" rather than only "what
    did it say".
    """
    # TWO SWITCHES, ONE GATE. `CALIBRATED_JUDGE_MODE` says how far the judge may
    # go at all; `CALIBRATED_JUDGE_ACTIVE_DECISIONS` says WHICH decisions may go
    # that far. A decision nobody has listed rides shadow even while the machine
    # is globally `active`, so turning the house default on cannot quietly hand
    # an unmeasured decision the authority to ship rows.
    active_mode = config.effective_mode(decision, mode)
    out = {"verdict": incumbent_verdict, "confidence": incumbent_confidence,
           "overridden": False, "mode": active_mode, "judge": None}
    if active_mode == config.MODE_OFF:
        return out

    try:
        result = judge(decision, evidence, icp=icp, persona=persona,
                       mode=active_mode, entity_key=entity_key, enrich=enrich)
        if not isinstance(result, JudgeResult):
            result = _unavailable("the judge returned a non-result")
    except Exception as exc:                    # never reaches the caller
        result = _unavailable(f"{type(exc).__name__}: {exc}")

    run_id = run_id or uuid.uuid4().hex[:12]
    try:
        for earlier in (getattr(result, "raw", None) or {}).get("rounds") or ():
            log.append(earlier, skill=skill, hook_point=hook_point,
                       decision=decision, entity_key=entity_key, run_id=run_id,
                       incumbent_verdict=incumbent_verdict,
                       incumbent_confidence=incumbent_confidence,
                       mode=active_mode)
        log.append(result, skill=skill, hook_point=hook_point,
                   decision=decision, entity_key=entity_key, run_id=run_id,
                   incumbent_verdict=incumbent_verdict,
                   incumbent_confidence=incumbent_confidence,
                   mode=active_mode)
    except Exception:                           # a full disk is not an outage
        pass

    out["judge"] = result.as_dict()
    if (active_mode == config.MODE_ACTIVE
            and result.error is None
            and result.action in OVERRIDING_ACTIONS
            and result.verdict is not None):
        out["verdict"] = result.verdict
        out["confidence"] = result.decision_score
        out["overridden"] = True
    return out
