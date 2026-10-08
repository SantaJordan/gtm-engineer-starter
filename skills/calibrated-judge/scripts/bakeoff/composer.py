#!/usr/bin/env python3
"""COMPOSER v2 — answers into an outcome, with the v1 composer kept for replay.

The adversarial review reproduced eight v1 outputs with synthetic answers and
no model call. Each one is a test in `tests/test_bakeoff_composer_v2.py`. The
four rules those eight defects come down to:

  1. **Three internal outcomes, not two.** accept this candidate / reject this
     candidate / insufficient evidence. Rejecting a candidate website is not a
     claim that the organization has no website; that needs its own absence
     proof, which this harness does not have, so it returns
     EVIDENCE_INSUFFICIENT instead of NO_WEBSITE.
  2. **Review is not rejection.** headline-only evidence, an empty profile,
     name-only overlap, a detected injection attempt and an unresolved
     contradiction all route to MAYBE/UNSURE. None of them can produce a NO.
  3. **A rejection needs a specific claim above its own threshold.** Sparse or
     absent support is an abstention, never a rejection.
  4. **No probability is manufactured.** v1 emitted 0.1, `0.5 + share/2` and
     `share/2` as if they were calibrated. v2 emits `final_probability: null`
     plus a `decision_score` that is explicitly the deciding claim's own
     probability, and keeps every raw per-question probability in `raw`.

There is no "half the questions must be answered" quota. Only applicable
guards are evaluated, and a guard that was never asked is not evidence of
anything.
"""
from __future__ import annotations

import math

from . import batteries as B
from .decisions import DECISIONS, EVIDENCE_INSUFFICIENT

COMPOSER_VERSION_V2 = "v2"
COMPOSER_VERSION_V3 = "v3"
COMPOSER_VERSION_V4 = "v4"
#: the module default advanced to v4; v1, v2 and v3 keep their
#: own names so a replay asks for itself explicitly and can never drift.
COMPOSER_VERSION = "v4"
COMPOSER_VERSIONS = ("v1", "v2", "v3", "v4")
_TYPED_VERSIONS = ("v2", "v3", "v4")

#: the FULL site-status vocabulary. v1 could only ever emit two of these.
SITE_STATUS_VOCAB = ("live_official", "parked_or_placeholder", "social_only",
                     "different_org", "dead", "unknown")

#: identifier types that are exclusive enough to settle an identity on their
#: own. A shared office phone is not one of them.
_EXCLUSIVE_IDS = ("npi", "fdid", "ein")

#: what each decision emits when the composer declines to commit.
_ABSTAIN_TOKEN = {
    "real_website": EVIDENCE_INSUFFICIENT,
    "site_status": EVIDENCE_INSUFFICIENT,
    "employment_now": "UNSURE",
    "same_identity": "UNSURE",
    "role_holder_now": EVIDENCE_INSUFFICIENT,
}


# --------------------------------------------------------------- validation
def _finite_prob(value):
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    x = float(value)
    if math.isnan(x) or math.isinf(x) or x < 0.0 or x > 1.0:
        return None
    return x


def validate_answers(questions, raw_answers, thresholds=None):
    """-> (values, invalid, raw_probabilities).

    `values` holds only answers that are well-formed AND clear their type's
    confidence gate. Everything refused is named in `invalid`, with the reason,
    so a malformed answer can never quietly read as FALSE.
    """
    T = thresholds or B.THRESHOLDS_V2
    by_id = {q["id"]: q for q in questions or ()}
    values, invalid, raws = {}, {}, {}

    for qid, ans in (raw_answers or {}).items():
        q = by_id.get(qid)
        if q is None:
            invalid[qid] = "answer to a question that was not asked"
            continue
        if not isinstance(ans, dict):
            invalid[qid] = f"answer is {type(ans).__name__}, not a typed answer"
            continue
        atype = str(ans.get("type") or "").strip().lower()
        if atype in ("boolean", "bool"):
            atype = "noul"
        if not atype and q["kind"] == "noul" and "probability" in ans:
            # An untyped {"probability": x} against a noul question is not
            # ambiguous: there is exactly one shape it can be. A DECLARED type
            # that disagrees is still refused below — this only fills a blank,
            # it never overrides an answer that names its own type.
            atype = "noul"
        if atype != q["kind"]:
            invalid[qid] = f"answer type {atype!r} != question kind {q['kind']!r}"
            continue

        if q["kind"] == "noul":
            p = _finite_prob(ans.get("probability"))
            if p is None:
                invalid[qid] = "probability is not a finite number in [0, 1]"
                continue
            raws[qid] = p
            values[qid] = {"kind": "noul", "p": p}
            continue

        probs = ans.get("probabilities") or {}
        clean = {str(k): _finite_prob(v) for k, v in probs.items()}
        if not clean or any(v is None for v in clean.values()):
            invalid[qid] = "option probabilities are missing or out of range"
            continue
        raws[qid] = clean

        if q["kind"] == "choice":
            option = str(ans.get("choice") or "").strip()
            if option not in q["criteria"]:
                invalid[qid] = f"chosen option {option!r} is not in the criteria"
                continue
            conf = _finite_prob(ans.get("confidence"))
            if conf is None:
                conf = clean.get(option)
            if conf is None:
                invalid[qid] = "choice carries no usable confidence"
                continue
            if conf < T["choice_min_confidence"]:
                continue                      # gated out: discarded, not FALSE
            values[qid] = {"kind": "choice", "option": option, "conf": conf}
            continue

        # score: keep the LEVEL probabilities. Never band a score as P(true).
        top = max(clean.values())
        if top < T["score_min_confidence"]:
            continue
        values[qid] = {"kind": "score", "levels": clean, "conf": top}

    return values, invalid, raws


# ------------------------------------------------------------------ readers
def _p(values, qid):
    v = (values or {}).get(qid)
    return v["p"] if v and v["kind"] == "noul" else None


def _level(values, qid, level):
    v = (values or {}).get(qid)
    if not v or v["kind"] != "score":
        return None
    return v["levels"].get(level)


def _option(values, qid):
    v = (values or {}).get(qid)
    if not v or v["kind"] != "choice":
        return None, None
    return v["option"], v["conf"]


def _at_least(p, threshold):
    return p is not None and p >= threshold


# ------------------------------------------------------------------ outcome
def _out(outcome, verdict=None, *, score=None, why="", reasons=(), claim=None):
    return {"outcome": outcome, "verdict": verdict, "decision_score": score,
            "why": why, "review_reasons": list(reasons), "rejection_claim": claim}


def _guard_fires(values, q, T):
    """Did this taxonomy guard fire on this row?

    A noul guard fires above `review_at`. A Choice guard fires when the option
    it settled on is not one of the question's own CLEAN options — the scope
    question's `this_entity` is the only answer that is not a finding.
    """
    if q["kind"] == "choice":
        option, conf = _option(values, q["id"])
        if option is None:
            return False
        if option in (q.get("clean_options") or ()):
            return False
        return _at_least(conf, T["review_at"])
    return _at_least(_p(values, q["id"]), T["review_at"])


def _taxonomy_guard_pass(questions, values, T, version, reasons):
    """-> (fired ids, ids that force a review).

    The doctrine, and the two fields that carry it:

      `measured`  whether this guard's false-fire rate has been scored against
                  a key. Nothing has been. A MEASURED guard routes the row to a
                  human — outcome `review` — and never to a rejection.
      `route`     what an UNMEASURED guard does. `"review"` is what the four
                  structural guards SHIPPED in v3 and were measured with, so v3
                  keeps it; a replay that silently re-routes is not a replay.
                  `"shadow"` records that it fired and changes nothing at all,
                  which is the uniform v4 rule: a guard nobody has scored may
                  not withhold a correct answer, because the error that shrinks
                  a TAM is a confident absence.
    """
    guards = (B.TAXONOMY_GUARDS_V3 if version == "v3" else B.TAXONOMY_GUARDS_V4)
    fired, force_review = [], []
    for qid in sorted(guards):
        q = B.question_in(questions, qid)
        if q is None or not _guard_fires(values, q, T):
            continue
        fired.append(q["id"])
        meta = guards[qid]
        if meta.get("measured") is True:
            force_review.append(q["id"])
            reasons.append(q["id"])
        elif meta.get("route") == "review":
            reasons.append(q["id"])
    return fired, force_review


def compose(decision_name, questions, answers, facts, thresholds=None,
            version=COMPOSER_VERSION, battery_version=None):
    """-> the composed outcome for one row. `verdict_for()` turns it into a token."""
    if version == "v1":
        return compose_v1(decision_name, answers)
    if version not in _TYPED_VERSIONS:
        raise KeyError(f"unknown composer version {version!r}")

    T = dict(B.thresholds_for(version))
    overrides = thresholds or {}
    if "accept" in overrides:
        # An explicit accept line is the caller's, and it beats v4's per
        # decision / per question tables rather than being silently overridden
        # by them. Used by the threshold grid search, which must mean what it
        # says.
        T.pop("accept_by_decision", None)
        T.pop("accept_by_question", None)
    T.update(overrides)
    #: resolve v4's per-decision accept line into the scalar every rule reads,
    #: so the number a result reports is the number that governed it.
    T["accept"] = B.accept_for(decision_name, T)
    facts = facts or {}
    values, invalid, raws = validate_answers(questions, answers, T)

    reasons = []
    if _at_least(_p(values, "page_contains_classification_instructions"),
                 T["review_at"]):
        reasons.append("injection")
    if _at_least(_p(values, "evidence_insufficient"), T["review_at"]):
        reasons.append("evidence_insufficient")

    fired, force_review = [], []
    if version in ("v3", "v4"):
        fired, force_review = _taxonomy_guard_pass(questions, values, T,
                                                   version, reasons)

    if force_review:
        # Returned BEFORE any composition rule runs, so the taxonomy's own
        # governing line holds structurally and not by inspection: a guard
        # routes to review, never to a rejection — not even a CODE one.
        result = _out("review", None, reasons=reasons,
                      why="measured taxonomy guard(s) fired: "
                          + ", ".join(force_review))
    else:
        dispatch = _DISPATCH_BY_VERSION[version]
        fn = dispatch.get(decision_name)
        if fn is None:
            raise KeyError(f"no {version} composition rule for decision "
                           f"{decision_name!r}")
        result = fn(values, facts, T, reasons)

    result.update({
        "final_probability": None,          # no calibrated final probability yet
        "raw_probabilities": raws,
        "invalid_answers": invalid,
        "composer_version": version,
        "battery_version": battery_version or version,
        "thresholds": {k: T[k] for k in ("accept", "reject_claim", "review_at",
                                         "choice_min_confidence",
                                         "score_min_confidence")},
        "applicable_question_ids": [q["id"] for q in questions or ()],
    })
    if version in ("v3", "v4"):
        result["taxonomy_guards_fired"] = fired
    return result


def verdict_for(decision_name, composed):
    """The composed outcome -> the token this decision's vocabulary uses."""
    if composed.get("outcome") == "accept" or composed.get("outcome") == "reject":
        if composed.get("verdict"):
            return composed["verdict"]
    return _ABSTAIN_TOKEN.get(decision_name, EVIDENCE_INSUFFICIENT)


def _reviewed(reasons, why):
    return _out("insufficient", None, why=why, reasons=reasons)


_NO_CLAIM = ("no accept claim above {a} and no rejection claim above {r}")


def _fallback(T, reasons):
    return _out("insufficient", None, reasons=reasons,
                why=_NO_CLAIM.format(a=T["accept"], r=T["reject_claim"]))


# --------------------------------------------------------- real_website v2
def _compose_real_website(values, facts, T, reasons):
    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")
    if not facts.get("has_candidate"):
        return _out("insufficient", None, reasons=reasons,
                    why="no candidate to judge; absence of a website is a "
                        "separate claim this row carries no evidence for")

    if facts.get("is_social") is True:
        return _out("reject", None, why="code: the effective URL is a social "
                    "profile host, which this decision does not accept as a "
                    "website", claim="social_host_code")

    accept_p = _level(values, "same_organization", "accept_candidate")
    reject_p = _level(values, "same_organization", "reject_candidate")

    claims = {}
    for qid in ("parked_or_for_sale", "directory_listing",
                "different_org_same_name", "page_is_unrelated_business",
                "redirect_terminus_distinct_operator"):
        p = _p(values, qid)
        if _at_least(p, T["reject_claim"]):
            claims[qid] = p
    if _at_least(reject_p, T["reject_claim"]):
        claims["same_organization:reject_candidate"] = reject_p

    accepts = _at_least(accept_p, T["accept"])
    if accepts and claims:
        return _reviewed(reasons + ["unresolved_contradiction"],
                         "an accept claim and a rejection claim both cleared "
                         "their thresholds")
    if claims:
        qid, p = max(claims.items(), key=lambda kv: kv[1])
        return _out("reject", None, score=p, claim=qid,
                    why=f"rejection claim {qid} at {p} >= {T['reject_claim']}; "
                        f"this rejects the CANDIDATE, not the existence of a "
                        f"website")
    if accepts:
        return _out("accept", facts.get("candidate_domain") or None,
                    score=accept_p,
                    why=f"same_organization accept_candidate at {accept_p} "
                        f">= {T['accept']}")
    return _fallback(T, reasons)


# ------------------------------------------------------- employment_now v2
def _compose_employment(values, facts, T, reasons):
    if facts.get("profile_empty") is True:
        reasons.append("empty_profile")
    if not facts.get("experience_match") and \
            _at_least(_p(values, "headline_claims_role"), T["review_at"]):
        reasons.append("headline_only")
    if _at_least(_p(values, "name_mismatch"), T["review_at"]):
        reasons.append("name_mismatch")

    ongoing = _p(values, "role_is_explicitly_ongoing")
    ended = _p(values, "role_explicitly_ended")
    if _at_least(ongoing, T["accept"]) and (
            facts.get("temporal_status") == "ended"
            or _at_least(ended, T["reject_claim"])):
        reasons.append("unresolved_contradiction")

    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")

    claims = {}
    for qid in ("board_or_advisor_only", "role_explicitly_ended"):
        p = _p(values, qid)
        if _at_least(p, T["reject_claim"]):
            claims[qid] = p

    # employer identity — three ALTERNATIVE routes, never two mandatory votes
    same_org = _p(values, "employer_is_same_org")
    relation, rel_conf = _option(values, "employer_relationship")
    employer_ok = (_at_least(same_org, T["accept"])
                   or facts.get("company_url_match") is True
                   or (relation == "same_entity_or_dba"
                       and _at_least(rel_conf, T["accept"])))

    role_ok = _at_least(_p(values, "role_is_employment"), T["accept"])
    time_ok = (facts.get("temporal_status") == "current"
               or _at_least(ongoing, T["accept"]))

    if claims:
        qid, p = max(claims.items(), key=lambda kv: kv[1])
        return _out("reject", "NO", score=p, claim=qid,
                    why=f"rejection claim {qid} at {p} >= {T['reject_claim']}")
    if employer_ok and role_ok and time_ok:
        score = min(x for x in (same_org, rel_conf,
                                _p(values, "role_is_employment"), ongoing)
                    if x is not None)
        return _out("accept", "YES", score=score,
                    why="one attributable, current, qualifying role at an "
                        "allowed employer")
    return _fallback(T, reasons)


# -------------------------------------------------------- same_identity v2
def _compose_identity(values, facts, T, reasons):
    if _at_least(_p(values, "name_only_overlap"), T["review_at"]):
        reasons.append("name_only_overlap")
    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")

    ids = facts.get("identifiers") or {}
    conflict = [k for k in ids.get("conflict") or () if k in _EXCLUSIVE_IDS]
    if conflict:
        return _out("reject", "no", claim=f"identifier_conflict:{conflict[0]}",
                    why=f"code: conflicting exclusive identifier(s) "
                        f"{', '.join(conflict)}")

    agree = [k for k in ids.get("agree") or () if k in _EXCLUSIVE_IDS]
    occupation = _p(values, "occupation_compatible")
    if agree:
        return _out("accept", "yes", claim=f"identifier_agrees:{agree[0]}",
                    why=f"code: matching exclusive identifier {agree[0]}")
    if (facts.get("names_equal") is True and facts.get("locality_equal") is True
            and _at_least(occupation, T["accept"])):
        return _out("accept", "yes", score=occupation,
                    why="same name, same observed locality, compatible "
                        "occupation")
    return _fallback(T, reasons)


# ----------------------------------------------------------- site_status v2
def _compose_site_status(values, facts, T, reasons):
    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")

    transport = facts.get("transport_class")
    if facts.get("is_social") is True:
        return _out("reject", "social_only", claim="social_host_code",
                    why="code: the effective host is a social network")
    if transport == "no_dns":
        return _out("reject", "dead", claim="no_dns",
                    why="code: the name does not resolve — documented "
                        "unavailability of this domain")
    if transport in ("timeout", "5xx", "unobserved"):
        return _out("insufficient", None, reasons=reasons,
                    why=f"transport {transport}: the observation failed, which "
                        f"is not evidence that the site is dead")

    candidates = {}
    p = _p(values, "parked_page")
    if _at_least(p, T["reject_claim"]):
        candidates["parked_or_placeholder"] = ("parked_page", p)
    p = _p(values, "distinct_operator")
    if _at_least(p, T["reject_claim"]):
        candidates["different_org"] = ("distinct_operator", p)
    p = _p(values, "error_page")
    if _at_least(p, T["reject_claim"]) and transport == "4xx":
        candidates["dead"] = ("error_page", p)
    p = _p(values, "official_publisher")
    if _at_least(p, T["accept"]) and transport in ("2xx", "3xx"):
        candidates["live_official"] = ("official_publisher", p)

    if len(candidates) > 1:
        return _reviewed(reasons + ["unresolved_contradiction"],
                         f"conflicting applicable classifications: "
                         f"{', '.join(sorted(candidates))}")
    if not candidates:
        return _fallback(T, reasons)
    label, (claim, score) = next(iter(candidates.items()))
    outcome = "accept" if label == "live_official" else "reject"
    return _out(outcome, label, score=score, claim=claim,
                why=f"{claim} at {score} selects {label}")


# ------------------------------------------------------- role_holder_now v2
def _compose_role_holder(values, facts, T, reasons):
    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")

    claims = {}
    for qid in ("holder_appointment_ended", "role_mismatch"):
        p = _p(values, qid)
        if _at_least(p, T["reject_claim"]):
            claims[qid] = p
    if claims:
        qid, p = max(claims.items(), key=lambda kv: kv[1])
        return _out("reject", "UNKNOWN", score=p, claim=qid,
                    why=f"rejection claim {qid} at {p}")

    spans = list(facts.get("name_spans") or [])
    option, conf = _option(values, "selected_holder")
    if option is None or option == "none_of_these" or option not in spans:
        return _out("insufficient", None, reasons=reasons,
                    why="no extracted name was selected as the holder")

    idx = spans.index(option)
    assigned = _p(values, f"assigns_target_role__{idx}")
    org_ok = _p(values, "holder_is_for_this_org")
    time_ok = _p(values, "appointment_is_ongoing")
    gates = [assigned, org_ok, time_ok]
    if all(_at_least(x, T["accept"]) for x in gates):
        return _out("accept", option, score=min(gates + [conf]),
                    why="a selected, assigned, organization-bound, ongoing "
                        "appointment")
    return _fallback(T, reasons)


_DISPATCH = {
    "real_website": _compose_real_website,
    "employment_now": _compose_employment,
    "same_identity": _compose_identity,
    "site_status": _compose_site_status,
    "role_holder_now": _compose_role_holder,
}


# ==========================================================================
# COMPOSER v3 — the two overshoots phase 1b diagnosed, fixed at the rule
#
# v2 is unchanged above. Only the two decisions that committed NOTHING get a
# new rule; the other three reuse v2's, because "v2 answers nothing" was only
# true of employment_now and same_identity.
# ==========================================================================

#: The states in which the clock question is answered. `weak_current` is a
#: CONDITIONAL pass, spelled out in `_compose_employment_v3`.
_CURRENT = "current"
_WEAK_CURRENT = "weak_current"


def _temporal_v3(facts):
    """v3 reads its own field. v2's `temporal_status` stays where it is so a
    v2 replay keeps reading exactly what it read when it was measured."""
    return facts.get("temporal_status_v3") or facts.get("temporal_status")


def _compose_employment_v3(values, facts, T, reasons):
    """One attributable, CURRENT, qualifying role at an allowed employer.

    What changed, and why: in the replay `temporal_status` came back "unknown"
    on every profile-check row and `role_is_explicitly_ongoing` stayed low, so no
    row cleared the time bar although many cleared the employer and role bars. The gate was unreachable by construction. v3 accepts the
    provider's own currency signal as what it is, gives "since <year>" its own
    conditional state, and sends an `unknown` clock to a human instead of
    letting it silently sink the row.
    """
    if facts.get("profile_empty") is True:
        reasons.append("empty_profile")
    if not facts.get("experience_match") and \
            _at_least(_p(values, "headline_claims_role"), T["review_at"]):
        reasons.append("headline_only")
    if _at_least(_p(values, "name_mismatch"), T["review_at"]):
        reasons.append("name_mismatch")

    status = _temporal_v3(facts)
    ongoing = _p(values, "role_is_explicitly_ongoing")
    ended = _p(values, "role_explicitly_ended")
    if _at_least(ongoing, T["accept"]) and (
            status == "ended" or _at_least(ended, T["reject_claim"])):
        reasons.append("unresolved_contradiction")

    # the rejection claims are read BEFORE the review routing, because an
    # explicit "this role ended" is a claim, not a missing fact.
    claims = {}
    for qid in ("board_or_advisor_only", "role_explicitly_ended"):
        p = _p(values, qid)
        if _at_least(p, T["reject_claim"]):
            claims[qid] = p
    if claims and "unresolved_contradiction" not in reasons:
        qid, p = max(claims.items(), key=lambda kv: kv[1])
        return _out("reject", "NO", score=p, claim=qid,
                    why=f"rejection claim {qid} at {p} >= {T['reject_claim']}")

    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")

    same_org = _p(values, "employer_is_same_org")
    relation, rel_conf = _option(values, "employer_relationship")
    employer_ok = (_at_least(same_org, T["accept"])
                   or facts.get("company_url_match") is True
                   or (relation == "same_entity_or_dba"
                       and _at_least(rel_conf, T["accept"])))
    role_ok = _at_least(_p(values, "role_is_employment"), T["accept"])

    # --- the clock
    if status == _CURRENT or _at_least(ongoing, T["accept"]):
        time_ok, time_why = True, f"temporal_status={status or 'current'}"
    elif status == _WEAK_CURRENT:
        # allowed ONLY when the employer test AND the role test pass and no
        # review reason fired — which, by this point, is already true of
        # `reasons`. A weak clock never carries a row on its own.
        time_ok = bool(employer_ok and role_ok)
        time_why = ("temporal_status=weak_current, carried by a passing "
                    "employer test and role test")
    elif status == "ended":
        return _out("insufficient", None, reasons=reasons,
                    why="the selected experience shows an end; an ended role "
                        "is not a current one, and a wrong NO needs its own "
                        "positive proof")
    else:
        reasons = reasons + ["temporal_status_unknown"]
        return _reviewed(reasons,
                         "the clock cannot be read from this evidence: no "
                         "provider currency flag, no ongoing wording and no "
                         "end date — a human decides, not a default")

    if employer_ok and role_ok and time_ok:
        score = min(x for x in (same_org, rel_conf,
                                _p(values, "role_is_employment"), ongoing)
                    if x is not None)
        return _out("accept", "YES", score=score,
                    why=f"one attributable, qualifying role at an allowed "
                        f"employer; {time_why}")
    return _fallback(T, reasons)


def _compose_identity_v3(values, facts, T, reasons):
    """Same ORGANIZATION or person — which is not the same question as same
    LOCATION.

    v2 accepted only on names + matching locality + compatible occupation.
    Localities matched on none of the bench rows, including none whose gold
    answer is `yes`, because those pairs are one chain in two cities. v3 keeps
    the name gate, then takes any ONE of three routes to an accept, and rejects
    only on a specific rejection claim.
    """
    if _at_least(_p(values, "name_only_overlap"), T["review_at"]):
        reasons.append("name_only_overlap")

    ids = facts.get("identifiers") or {}
    conflict = [k for k in ids.get("conflict") or () if k in _EXCLUSIVE_IDS]
    if conflict:
        return _out("reject", "no", claim=f"identifier_conflict:{conflict[0]}",
                    why=f"code: conflicting exclusive identifier(s) "
                        f"{', '.join(conflict)}")

    distinct = _p(values, "distinct_operator_evidence")
    if _at_least(distinct, T["reject_claim"]):
        return _out("reject", "no", score=distinct,
                    claim="distinct_operator_evidence",
                    why=f"specific rejection claim distinct_operator_evidence "
                        f"at {distinct} >= {T['reject_claim']}")

    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")

    agree = [k for k in ids.get("agree") or () if k in _EXCLUSIVE_IDS]
    if agree:
        return _out("accept", "yes", claim=f"identifier_agrees:{agree[0]}",
                    why=f"code: matching exclusive identifier {agree[0]}")

    if facts.get("names_equal") is not True:
        # name-only overlap routes to review above; names that do not match at
        # all are not this decision's accept path.
        return _fallback(T, reasons)

    occupation = _p(values, "occupation_compatible")
    same_org = _p(values, "same_org_different_locations")
    contradiction = facts.get("locality_contradiction")

    if _at_least(same_org, T["accept"]):
        return _out("accept", "yes", score=same_org,
                    why=f"same_org_different_locations at {same_org} >= "
                        f"{T['accept']}: one organization, two sites")
    if _at_least(occupation, T["accept"]) and contradiction is not True:
        return _out("accept", "yes", score=occupation,
                    why=f"matching names, compatible occupation at "
                        f"{occupation}, and no locality contradiction at this "
                        f"entity scope (locality_contradiction="
                        f"{contradiction!r})")
    return _fallback(T, reasons)


_DISPATCH_V3 = dict(_DISPATCH)
_DISPATCH_V3.update({
    "employment_now": _compose_employment_v3,
    "same_identity": _compose_identity_v3,
})


# ==========================================================================
# COMPOSER v4 — the relaxations the v3 blocker analysis MEASURED, at the rule
#
# Every number is a named constant in `batteries.py` with its measured
# gold-negative cost written beside it. Three of the four relaxations are
# threshold moves that need no code here at all, because the rules already read
# `T["accept"]` and `compose()` now resolves it per decision:
#
#   real_website    accept 0.90 -> 0.80   (RULE_REAL_WEBSITE_ACCEPT_V4)
#   employment_now  accept 0.90 -> 0.85   (RULE_EMPLOYMENT_ACCEPT_V4)
#
# The three rules below are the ones that are rules, not numbers.
# ==========================================================================
def _compose_real_website_v4(values, facts, T, reasons):
    """v3's rule, plus the redirects polarity fix and the publisher route."""
    # --- the polarity fix: the terminus question keeps its power to STOP an
    # accept and loses its power to produce a rejection on its own. LC-01,
    # LC-05 and AD-04 are evidence-identical at the terminus.
    if not B.RULE_REDIRECT_TERMINUS_MAY_REJECT_V4 and _at_least(
            _p(values, "redirect_terminus_distinct_operator"), T["review_at"]):
        reasons.append("redirect_terminus_distinct_operator")

    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")
    if not facts.get("has_candidate"):
        return _out("insufficient", None, reasons=reasons,
                    why="no candidate to judge; absence of a website is a "
                        "separate claim this row carries no evidence for")
    if facts.get("is_social") is True:
        return _out("reject", None, why="code: the effective URL is a social "
                    "profile host, which this decision does not accept as a "
                    "website", claim="social_host_code")

    accept_p = _level(values, "same_organization", "accept_candidate")
    reject_p = _level(values, "same_organization", "reject_candidate")

    claims = {}
    for qid in ("parked_or_for_sale", "directory_listing",
                "different_org_same_name", "page_is_unrelated_business"):
        p = _p(values, qid)
        if _at_least(p, T["reject_claim"]):
            claims[qid] = p
    if _at_least(reject_p, T["reject_claim"]):
        claims["same_organization:reject_candidate"] = reject_p

    accepts = _at_least(accept_p, T["accept"])

    # --- the set-A refreeze fix. The refreeze added a real field naming the
    # page's declared publisher, and the model correctly discounted four
    # municipal fire departments whose publisher is the TOWN — against a key
    # that accepts a municipality's own domain for its own department. This is
    # a SECOND route to an accept, never a bypass: it needs a usable score
    # answer that is not on the reject side, and the rejection claims below
    # still outrank it.
    publisher = _p(values, "publisher_operates_the_anchor")
    publisher_route = (
        accept_p is not None
        and accept_p >= B.RULE_PUBLISHER_ROUTE_MIN_SCORE_V4
        and _at_least(publisher, B.RULE_PUBLISHER_OPERATES_ACCEPT_V4))

    if (accepts or publisher_route) and claims:
        return _reviewed(reasons + ["unresolved_contradiction"],
                         "an accept claim and a rejection claim both cleared "
                         "their thresholds")
    if claims:
        qid, p = max(claims.items(), key=lambda kv: kv[1])
        return _out("reject", None, score=p, claim=qid,
                    why=f"rejection claim {qid} at {p} >= {T['reject_claim']}; "
                        f"this rejects the CANDIDATE, not the existence of a "
                        f"website")
    if accepts:
        return _out("accept", facts.get("candidate_domain") or None,
                    score=accept_p,
                    why=f"same_organization accept_candidate at {accept_p} "
                        f">= {T['accept']}")
    if publisher_route:
        return _out("accept", facts.get("candidate_domain") or None,
                    score=publisher,
                    why=f"publisher_operates_the_anchor at {publisher} >= "
                        f"{B.RULE_PUBLISHER_OPERATES_ACCEPT_V4}: the page's "
                        f"declared publisher operates the anchor as one of its "
                        f"own units, which this decision accepts; "
                        f"same_organization accept_candidate {accept_p} is "
                        f"consistent with it")
    return _fallback(T, reasons)


def _compose_site_status_v4(values, facts, T, reasons):
    """v2's rule with ONE question's accept line moved, and nothing else.

    `official_publisher` alone drops to 0.80. Every rejection line — the
    0.95 on `error_page` / `parked_page` / `distinct_operator`, and the CODE
    settlements — is untouched, because nothing in v4 makes it cheaper to
    commit an absence.
    """
    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")

    transport = facts.get("transport_class")
    if facts.get("is_social") is True:
        return _out("reject", "social_only", claim="social_host_code",
                    why="code: the effective host is a social network")
    if transport == "no_dns":
        return _out("reject", "dead", claim="no_dns",
                    why="code: the name does not resolve — documented "
                        "unavailability of this domain")
    if transport in ("timeout", "5xx", "unobserved"):
        return _out("insufficient", None, reasons=reasons,
                    why=f"transport {transport}: the observation failed, which "
                        f"is not evidence that the site is dead")

    publisher_accept = B.accept_for("site_status", T, "official_publisher")
    candidates = {}
    p = _p(values, "parked_page")
    if _at_least(p, T["reject_claim"]):
        candidates["parked_or_placeholder"] = ("parked_page", p)
    p = _p(values, "distinct_operator")
    if _at_least(p, T["reject_claim"]):
        candidates["different_org"] = ("distinct_operator", p)
    p = _p(values, "error_page")
    if _at_least(p, T["reject_claim"]) and transport == "4xx":
        candidates["dead"] = ("error_page", p)
    p = _p(values, "official_publisher")
    if _at_least(p, publisher_accept) and transport in ("2xx", "3xx"):
        candidates["live_official"] = ("official_publisher", p)

    if len(candidates) > 1:
        return _reviewed(reasons + ["unresolved_contradiction"],
                         f"conflicting applicable classifications: "
                         f"{', '.join(sorted(candidates))}")
    if not candidates:
        return _fallback(T, reasons)
    label, (claim, score) = next(iter(candidates.items()))
    outcome = "accept" if label == "live_official" else "reject"
    return _out(outcome, label, score=score, claim=claim,
                why=f"{claim} at {score} selects {label}")


def _compose_identity_v4(values, facts, T, reasons):
    """v3's rule minus one gate that is unreachable by construction.

    `names_equal` is True on few gold-positives and on many
    gold-negatives — anti-correlated with the key — because `state._pair_of()`
    rebuilds record_b from the row's free text as a URL blurb that carries no
    name at all. The gate cannot fire on the rows it is meant to protect. What
    DOES carry the precision stays exactly as it is: `locality_contradiction`
    (18 gold-negatives held, 0 gold-positives lost), `name_only_overlap` (9 and
    0), and the 0.90 bar on `occupation_compatible` (62 and 0).
    """
    if _at_least(_p(values, "name_only_overlap"), T["review_at"]):
        reasons.append("name_only_overlap")

    ids = facts.get("identifiers") or {}
    conflict = [k for k in ids.get("conflict") or () if k in _EXCLUSIVE_IDS]
    if conflict:
        return _out("reject", "no", claim=f"identifier_conflict:{conflict[0]}",
                    why=f"code: conflicting exclusive identifier(s) "
                        f"{', '.join(conflict)}")

    distinct = _p(values, "distinct_operator_evidence")
    if _at_least(distinct, T["reject_claim"]):
        return _out("reject", "no", score=distinct,
                    claim="distinct_operator_evidence",
                    why=f"specific rejection claim distinct_operator_evidence "
                        f"at {distinct} >= {T['reject_claim']}")

    if reasons:
        return _reviewed(reasons, f"review required: {', '.join(reasons)}")

    agree = [k for k in ids.get("agree") or () if k in _EXCLUSIVE_IDS]
    if agree:
        return _out("accept", "yes", claim=f"identifier_agrees:{agree[0]}",
                    why=f"code: matching exclusive identifier {agree[0]}")

    occupation = _p(values, "occupation_compatible")
    same_org = _p(values, "same_org_different_locations")
    contradiction = facts.get("locality_contradiction")

    if _at_least(same_org, T["accept"]):
        return _out("accept", "yes", score=same_org,
                    why=f"same_org_different_locations at {same_org} >= "
                        f"{T['accept']}: one organization, two sites")
    if _at_least(occupation, T["accept"]) and contradiction is not True:
        return _out("accept", "yes", score=occupation,
                    why=f"compatible occupation at {occupation} and no "
                        f"locality contradiction at this entity scope "
                        f"(locality_contradiction={contradiction!r}); the "
                        f"names_equal gate is dropped in v4 because it is "
                        f"unreachable on the rows it is meant to protect")
    return _fallback(T, reasons)


_DISPATCH_V4 = dict(_DISPATCH_V3)
_DISPATCH_V4.update({
    "real_website": _compose_real_website_v4,
    "site_status": _compose_site_status_v4,
    "same_identity": _compose_identity_v4,
})

_DISPATCH_BY_VERSION = {"v2": _DISPATCH, "v3": _DISPATCH_V3,
                        "v4": _DISPATCH_V4}


# ==========================================================================
# v1 — kept verbatim so a replay is a replay
# ==========================================================================
def compose_v1(decision_name, answers):
    """The v1 composition, defects included.

    Preserved on purpose: v1 -> v2 is only a clean A/B if the v1 arm still
    behaves exactly as it did when phase 1 measured it.
    """
    bat = B.BATTERIES_V1[decision_name]
    T = B.THRESHOLDS
    answers = answers or {}
    out = {"composer_version": "v1", "battery_version": "v1",
           "final_probability": None, "raw_probabilities": {},
           "invalid_answers": {}, "review_reasons": [], "rejection_claim": None,
           "decision_score": None}

    def finish(verdict, p, why, outcome):
        out.update({"verdict": verdict, "p_yes": p, "why": why,
                    "outcome": outcome, "decision_score": p})
        return out

    if answers.get("evidence_insufficient") is True:
        return finish(None, None, "evidence_insufficient answered TRUE",
                      "insufficient")
    guards = [q for q in bat["questions"] if q["polarity"] == "bad"]
    support = [q for q in bat["questions"] if q["polarity"] == "good"]
    answered = [q for q in bat["questions"] if answers.get(q["id"]) is not None]
    if not bat["questions"] or (len(answered) / len(bat["questions"])
                                < T["min_answered_fraction"]):
        return finish(None, None,
                      f"only {len(answered)}/{len(bat['questions'])} questions "
                      f"answered above the bands", "insufficient")
    fired = [q["id"] for q in guards if answers.get(q["id"]) is True]
    if len(fired) >= T["guard_veto_at"]:
        return finish(bat["reject_verdict"], 0.1,
                      f"guard(s) fired: {', '.join(fired)}", "reject")
    answered_support = [q for q in support if answers.get(q["id"]) is not None]
    if not answered_support:
        return finish(None, None, "no support question could be answered",
                      "insufficient")
    yes = [q["id"] for q in answered_support if answers.get(q["id"]) is True]
    share = len(yes) / len(answered_support)
    if share >= T["accept_support_fraction"]:
        return finish(bat["accept_verdict"], round(0.5 + share / 2, 3),
                      f"{len(yes)}/{len(answered_support)} support questions "
                      f"true", "accept")
    return finish(bat["reject_verdict"], round(share / 2, 3),
                  f"only {len(yes)}/{len(answered_support)} support questions "
                  f"true", "reject")
