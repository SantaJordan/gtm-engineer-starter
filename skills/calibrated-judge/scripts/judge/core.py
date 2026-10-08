#!/usr/bin/env python3
"""The shared judge: one call, one calibrated answer, no way to hurt a caller.

    from judge import judge
    result = judge("real_website", evidence, mode="shadow")

WHAT IT DOES, IN ORDER
  1. Mode gate. `off` (the default) returns immediately: no call, no spend.
  2. Human answers first. A settled actual for this (entity, decision) ends the
     question — the model is never shown it and never asked again.
  3. Battery. `bakeoff.batteries` v2 when it exists, v1 otherwise; the two fit
     decisions come from `judge.fit`. The version is recorded on every row.
  4. Ask. `jev_client.ask` with the configured backend order, one call per row.
  5. Validate. A probability that is not finite and inside [0, 1], or an answer
     to a question nobody asked, is DISCARDED. Never clamped, never guessed.
  6. Compose. `bakeoff.composer` when it exists, `judge.compose` otherwise —
     both give three outcomes, route review reasons to a human, and manufacture
     no probability.

EVERY failure — no key, 429, timeout, garbage payload, a battery that will not
load — comes back as `error="judge_unavailable"` with `action="insufficient"`.
The only exception is an unknown decision name, which is a programming error in
the caller and raises before anything is spent.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, fit, packets
from . import compose as fallback_composer

#: Harness bookkeeping. Ours, not the judge's — and including it would change
#: the client's cache key for identical evidence.
BOOKKEEPING = frozenset(("sha256", "set", "row_id", "decision", "bench_type",
                         "stratum", "redactions", "evidence_source",
                         "entity_key", "label", "label_source", "key_evidence"))


# ---------------------------------------------------------------- the result
@dataclass
class JudgeResult:
    verdict: Any
    action: str                     # accept | reject | review | insufficient
    decision_score: float | None
    per_question: dict
    review_reasons: list
    model: str | None
    backend: str | None
    battery_version: str
    composer_version: str
    latency_ms: int
    cost_usd: float
    evidence_sha256: str
    cached: bool
    final_probability: float | None = None
    why: str = ""
    error: str | None = None
    raw: dict = field(default_factory=dict)
    #: how much evidence the packet carried — see `judge.packets.richness`
    packet_richness: dict = field(default_factory=dict)
    #: 1 unless this row was enriched and re-judged, in which case the second
    #: answer is round 2 and round 1 is kept whole in `raw["rounds"]`
    round: int = 1
    #: the contract fields the enriched packet carried that the first did not
    enrichment_added: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "action": self.action,
                "decision_score": self.decision_score,
                "per_question": self.per_question,
                "review_reasons": list(self.review_reasons),
                "model": self.model, "backend": self.backend,
                "battery_version": self.battery_version,
                "composer_version": self.composer_version,
                "latency_ms": self.latency_ms, "cost_usd": self.cost_usd,
                "evidence_sha256": self.evidence_sha256, "cached": self.cached,
                "final_probability": self.final_probability, "why": self.why,
                "error": self.error, "packet_richness": dict(self.packet_richness),
                "round": self.round,
                "enrichment_added": list(self.enrichment_added)}


# ------------------------------------------------------------------- imports
def _scripts_dir() -> Path:
    return config.skill_dir() / "scripts"


def _import(name):
    """Import a sibling module out of this skill's scripts/ directory."""
    d = str(_scripts_dir())
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        return __import__(name, fromlist=["*"])
    except Exception:
        return None


def _client():
    """`scripts/jev_client.py`, or None when it cannot be imported.

    Owned by another agent and imported lazily on purpose: this module must
    keep working — degrading, but working — when that file is mid-edit.
    """
    mod = _import("jev_client")
    return mod if mod is not None and hasattr(mod, "ask") else None


def _batteries():
    return _import("bakeoff.batteries")


def _composer():
    mod = _import("bakeoff.composer")
    if mod is not None and hasattr(mod, "compose"):
        return mod
    return None


def _state_builder():
    return _import("bakeoff.state")


# ------------------------------------------------------------------ batteries
def resolve_battery(decision: str, version: str | None = None):
    """-> (battery dict, version). v2 when the bake-off ships one, else v1."""
    if decision in fit.FIT_BATTERIES:
        return fit.FIT_BATTERIES[decision], fit.VERSION
    if decision not in config.IDENTITY_DECISIONS:
        raise ValueError(
            f"unknown decision {decision!r}; known: {', '.join(config.DECISIONS)}")
    B = _batteries()
    if B is None:
        raise ValueError("bakeoff.batteries is not importable")
    wanted = version or ("v2" if hasattr(B, "BATTERIES_V2") else "v1")
    getter = getattr(B, "battery", None)
    if callable(getter):
        try:
            return getter(decision, wanted), wanted
        except Exception:
            pass
    table = getattr(B, f"BATTERIES_{wanted.upper()}", None) or getattr(B, "BATTERIES")
    return table[decision], wanted


def applicable_questions(decision, battery, facts, version):
    """The battery's questions, narrowed by the bake-off when it can.

    A missing prerequisite makes a question INAPPLICABLE, never FALSE — the
    rule the identity batteries have had since v2. Now the two
    fit batteries obey it too (`judge.fit.applicable_questions`): a text guard
    whose field is absent or empty is not sent, because a guard answered
    against a blank comes back as a number and a number reads as a finding.
    """
    B = _batteries()
    fn = getattr(B, "applicable_questions", None) if B else None
    if callable(fn) and version == "v2":
        try:
            qs = list(fn(decision, facts))
            if qs:
                return qs
        except Exception:
            pass
    if decision in fit.FIT_BATTERIES:
        qs = list(fit.applicable_questions(decision, battery, facts))
        ins = battery.get("evidence_insufficient")
        return qs + [ins] if ins else qs
    qs = list(battery.get("questions") or [])
    ins = battery.get("evidence_insufficient")
    if ins:
        qs = qs + [ins]
    return qs


def wire_questions(questions) -> dict:
    """Battery questions -> jev_client's `{qid: spec}` wire shape.

    Two schemas are in play and both are handled: v1 carries `true_when` /
    `false_when` on the question, v2 carries a `criteria` block and knows how
    to render itself through `batteries.wire_spec`. The bake-off's renderer
    wins whenever it is importable, so a question's wording has exactly one
    owner.
    """
    B = _batteries()
    spec_fn = getattr(B, "wire_spec", None) if B else None
    out = {}
    for q in questions:
        if callable(spec_fn):
            try:
                out[q["id"]] = spec_fn(q)
                continue
            except Exception:
                pass
        criteria = q.get("criteria")
        if not isinstance(criteria, dict):
            criteria = {"true": q.get("true_when", ""),
                        "false": q.get("false_when", "")}
        out[q["id"]] = {"type": q.get("kind") or "noul",
                        "instructions": q.get("text") or q.get("instructions", ""),
                        "criteria": criteria}
    return out


# ------------------------------------------------------------------ evidence
def clean_evidence(evidence: dict) -> dict:
    ev = evidence or {}
    return {k: v for k, v in ev.items()
            if k not in BOOKKEEPING and v not in (None, "", [], {})}


def evidence_sha256(evidence) -> str:
    """Canonical hash: dict ordering and whitespace must not split one row."""
    blob = json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_state(decision, evidence, criteria):
    """The text the judge reads. Bookkeeping never reaches it."""
    ev = clean_evidence(evidence)
    if decision in fit.FIT_BATTERIES:
        facts = fit.facts(decision, ev, criteria)
        return fit.packet(decision, ev, criteria, facts=facts), facts
    S = _state_builder()
    facts = {}
    if S is not None and hasattr(S, "facts_of"):
        try:
            facts = S.facts_of(dict(ev, decision=decision)) or {}
        except Exception:
            facts = {}
    if S is not None and hasattr(S, "build_state"):
        try:
            state = S.build_state(ev, facts=facts or None)
            if state:
                return state, facts
        except Exception:
            pass
    return ev, facts


# ------------------------------------------------------------------ hygiene
def _finite_unit(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < 0.0 or v > 1.0:
        return None
    return v


def validate_answers(raw_answers, asked: dict):
    """-> (clean jev-shaped answers, per_question view, problems).

    Three ways an answer dies here, all silent to the caller and all recorded:
      * it answers a question nobody asked,
      * its probability is not finite, or falls outside [0, 1],
      * its type is not one this battery asked for.
    A discarded answer is UNAVAILABLE. It is never rounded into a False.
    """
    clean, view, problems = {}, {}, []
    if not isinstance(raw_answers, dict):
        return clean, view, ["the response carried no answers object"]

    for qid, ans in raw_answers.items():
        if qid not in asked:
            problems.append(f"{qid}: answered but never asked")
            continue
        if not isinstance(ans, dict):
            problems.append(f"{qid}: answer is not an object")
            continue
        kind = str(ans.get("type") or asked[qid].get("type") or "noul").lower()
        if kind in ("noul", "boolean", "bool"):
            p = _finite_unit(ans.get("probability", ans.get("p")))
            if p is None:
                problems.append(f"{qid}: probability is not a finite [0,1]")
                continue
            clean[qid] = {"type": "noul", "probability": p}
            view[qid] = {"type": "noul", "p": p, "confidence": None}
            continue
        if kind not in ("choice", "score"):
            problems.append(f"{qid}: unknown answer type {kind!r}")
            continue
        probs = ans.get("probabilities")
        if not isinstance(probs, dict) or not probs:
            problems.append(f"{qid}: a {kind} with no probabilities")
            continue
        cleaned = {}
        for option, value in probs.items():
            v = _finite_unit(value)
            if v is None:
                cleaned = None
                break
            cleaned[str(option)] = v
        if not cleaned:
            problems.append(f"{qid}: a {kind} probability is not a finite [0,1]")
            continue
        chosen = ans.get("choice") or max(cleaned, key=cleaned.get)
        conf = _finite_unit(ans.get("confidence"))
        if conf is None:
            conf = cleaned.get(str(chosen), max(cleaned.values()))
        keep = {"type": kind, "probabilities": cleaned, "confidence": conf}
        if kind == "choice":
            keep["choice"] = chosen
        else:
            keep["score"] = ans.get("score")
        clean[qid] = keep
        view[qid] = {"type": kind, "probs": cleaned, "confidence": conf,
                     "choice": chosen if kind == "choice" else None}
    return clean, view, problems


# --------------------------------------------------------------------- price
#: The same three rungs `bakeoff/arms/jev_arm.py` uses, so one row's dollar
#: means the same thing in the bake-off and in production:
#:   usage.cost -> measured; input_tokens x rate -> derived; else 0.0, flagged.
PRICE_PER_INPUT_MTOK = 0.042


def price_of(usage) -> tuple[float, str]:
    u = usage if isinstance(usage, dict) else {}
    raw = u.get("cost", u.get("total_cost"))
    if raw is not None:
        try:
            v = float(raw)
            if v >= 0.0:
                return v, "usage.cost"
        except (TypeError, ValueError):
            pass
    tokens = u.get("input_tokens", u.get("prompt_tokens"))
    if tokens is not None:
        try:
            n = float(tokens)
            if n >= 0.0:
                return n * PRICE_PER_INPUT_MTOK / 1_000_000.0, "fallback_input_rate"
        except (TypeError, ValueError):
            pass
    return 0.0, "unpriced"


# -------------------------------------------------------------------- action
def action_of(outcome, review_reasons) -> str:
    if outcome == "accept":
        return "accept"
    if outcome == "reject":
        return "reject"
    return "review" if review_reasons else "insufficient"


def _unavailable(reason, sha, battery_version="unknown", detail=None,
                 richness=None):
    return JudgeResult(verdict=None, action="insufficient", decision_score=None,
                       per_question={}, review_reasons=["judge_unavailable"],
                       model=None, backend=None,
                       battery_version=battery_version, composer_version="none",
                       latency_ms=0, cost_usd=0.0, evidence_sha256=sha,
                       cached=False, error="judge_unavailable",
                       packet_richness=dict(richness or {}),
                       why=reason, raw={"detail": str(detail)[:300] if detail else ""})


def _ask_kwargs(client, scope):
    """Pass `cache_scope` only to a client that accepts it — the v2 client
    keys its cache on battery and composer version, the v1 one does not."""
    kw = {"backends": config.backend_order()}
    try:
        sig = inspect.signature(client.ask)
    except (TypeError, ValueError):
        return kw
    params = sig.parameters
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD
                         for p in params.values())
    if "cache_scope" in params or accepts_kwargs:
        kw["cache_scope"] = scope
    if not (accepts_kwargs or "backends" in params):
        kw.pop("backends", None)
    return kw


# ----------------------------------------------------------------- the judge
def judge(decision: str, evidence: dict, *, icp=None, persona=None,
          mode: str | None = None, entity_key: str | None = None,
          questions_limit: int | None = None,
          battery_version: str | None = None, enrich=None) -> JudgeResult:
    """One calibrated answer about one row. See the module docstring.

    `enrich` is the house rule in code: when the packet is thin and the answer
    is "not sure", the fix is more evidence, not a better prompt. Pass a
    callable — `enrich(evidence, decision) -> a fuller packet or None`, e.g.
    `packets.default_enricher(page_cache=...)` — and a thin or partial packet
    whose outcome was `review` or `insufficient` is rebuilt ONCE and re-judged
    ONCE. Both rounds are on the result and both reach the decision log. The
    default is None: no enricher, no second round, no spend.
    """
    if decision not in config.DECISIONS:
        raise ValueError(
            f"unknown decision {decision!r}; known: {', '.join(config.DECISIONS)}")

    sha = evidence_sha256(clean_evidence(evidence))
    active_mode = config.mode(mode)

    # 1 — the gate
    if active_mode == config.MODE_OFF:
        return JudgeResult(verdict=None, action="insufficient",
                           decision_score=None, per_question={},
                           review_reasons=[], model=None, backend=None,
                           battery_version="none", composer_version="none",
                           latency_ms=0, cost_usd=0.0, evidence_sha256=sha,
                           cached=False, error="mode_off",
                           why=f"{config.ENV_MODE} is off; no call was made")

    # 2 — a settled human answer ends the question, and is never shown to the
    #     model
    if entity_key:
        try:
            from . import actuals
            settled = actuals.lookup(entity_key, decision)
        except Exception:
            settled = None
        if settled:
            return actuals.as_result(settled, decision, sha)

    criteria = icp if decision == "icp_fit" else (
        persona if decision == "persona_fit" else None)

    result = _judge_round(decision, evidence, criteria, active_mode,
                          questions_limit, battery_version)

    # 3 — enrich, then re-judge. ONCE, and only where more evidence is the
    #     thing that was missing: a rich packet that came back unsure has a
    #     different problem, and an accept has none.
    if _wants_enrichment(result, enrich):
        fuller = _enriched(enrich, evidence, decision)
        if fuller:
            second = _judge_round(decision, fuller, criteria, active_mode,
                                  questions_limit, battery_version)
            second.round = 2
            second.enrichment_added = _added_fields(result.packet_richness,
                                                    second.packet_richness)
            second.raw = dict(second.raw or {},
                              rounds=[result.as_dict()],
                              enriched_from_sha256=result.evidence_sha256)
            second.cost_usd = round(result.cost_usd + second.cost_usd, 12)
            result = _name_what_is_missing(second)

    return result


#: The two outcomes more evidence can actually move. An `accept` is not a
#: problem and a `reject` was made on a specific claim.
ENRICHABLE_ACTIONS = ("review", "insufficient")
ENRICHABLE_TIERS = ("thin", "partial")


def _wants_enrichment(result, enrich) -> bool:
    return bool(
        enrich is not None
        and callable(enrich)
        and result.error is None
        and result.round == 1
        and result.action in ENRICHABLE_ACTIONS
        and (result.packet_richness or {}).get("tier") in ENRICHABLE_TIERS)


def _enriched(enrich, evidence, decision):
    """Call the enricher once. It cannot break the row: an enricher that
    raises leaves the first answer standing, which is the answer we already
    had."""
    try:
        sig = inspect.signature(enrich)
        takes_decision = len(sig.parameters) >= 2 or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except (TypeError, ValueError):
        takes_decision = False
    try:
        fuller = enrich(evidence, decision) if takes_decision else enrich(evidence)
    except Exception:                           # noqa: BLE001 — never fatal
        return None
    return fuller if isinstance(fuller, dict) and fuller else None


def _added_fields(before, after) -> list:
    had = set((before or {}).get("present_fields") or ())
    now = set((after or {}).get("present_fields") or ())
    return sorted(now - had)


def _name_what_is_missing(result) -> JudgeResult:
    """A row that is still unsure after an enrichment NAMES what is missing.

    The human reading the review queue has to know what to go and get. "Not
    sure" with no shopping list is a row that gets skipped.
    """
    if result.action not in ENRICHABLE_ACTIONS:
        return result
    missing = (result.packet_richness or {}).get("missing_required") or []
    if not missing:
        return result
    reasons = list(result.review_reasons)
    reasons.append("still_missing: " + "; ".join(missing))
    result.review_reasons = reasons
    result.action = "review"
    return result


def _judge_round(decision, evidence, criteria, active_mode, questions_limit,
                 battery_version) -> JudgeResult:
    """One packet, one ask, one composition. The judge's whole body."""
    sha = evidence_sha256(clean_evidence(evidence))
    tier = packets.richness(evidence)

    # 3 — battery
    try:
        battery, version = resolve_battery(decision, battery_version)
        state, facts = build_state(decision, evidence, criteria)
        questions = applicable_questions(decision, battery, facts, version)
        if questions_limit:
            head = config.headline_question(decision, version, battery)
            ordered = ([q for q in questions if q["id"] == head]
                       + [q for q in questions if q["id"] != head])
            questions = ordered[:int(questions_limit)]
        asked = wire_questions(questions)
    except Exception as exc:                       # a battery that will not load
        return _unavailable("the battery could not be prepared", sha,
                            detail=exc, richness=tier)

    # 3b — a CODE fact that outranks every question. An employer that is a
    #      status word ("Retired", "Career break", "Stealth") is not a persona
    #      judgement at all, and asking six questions about it spends money to
    #      arrive where the string already was.
    if facts.get("employer_is_status_word"):
        return JudgeResult(
            verdict=config.UNSURE_VERDICT.get(decision), action="review",
            decision_score=None, per_question={},
            review_reasons=["employer_is_status_word"], model=None,
            backend=None, battery_version=version, composer_version="code",
            latency_ms=0, cost_usd=0.0, evidence_sha256=sha, cached=False,
            packet_richness=tier,
            why="the current employer is a status word, not a company; a human "
                "decides what that row is")

    if not asked:
        return _unavailable("the battery is empty for this evidence", sha,
                            battery_version=version, richness=tier)

    # 4 — ask
    client = _client()
    if client is None:
        return _unavailable("jev_client is not importable", sha,
                            battery_version=version, richness=tier)
    started = time.perf_counter()
    try:
        response = client.ask(state, asked,
                              **_ask_kwargs(client,
                                            {"battery_version": version,
                                             "composer_version": _composer_version()}))
    except Exception as exc:                       # no key, 429, timeout, 400
        return _unavailable("every backend refused the call", sha,
                            battery_version=version, detail=exc, richness=tier)
    measured_ms = int(round((time.perf_counter() - started) * 1000))
    if not isinstance(response, dict):
        return _unavailable("the backend returned something that is not a "
                            "response object", sha, battery_version=version,
                            richness=tier)

    # 5 — validate
    clean, view, problems = validate_answers(response.get("answers"), asked)
    cost, cost_source = price_of(response.get("usage"))
    latency = max(measured_ms, int(response.get("latency_ms") or 0))

    # 6 — compose. The bake-off's composer owns the five identity decisions
    #     when it is present; fit is ours, and so is every case the bake-off
    #     refuses — a composer that cannot read this battery is a reason to
    #     compose it here, not a reason to lose the answers.
    out, verdict, composer_version, fallback_reason = None, None, None, None
    K = _composer() if decision in config.IDENTITY_DECISIONS else None
    if K is not None:
        try:
            out = K.compose(decision, questions, clean, facts)
            verdict = K.verdict_for(decision, out)
            composer_version = getattr(K, "VERSION", "v2")
        except Exception as exc:
            out, fallback_reason = None, f"{type(exc).__name__}: {exc}"[:200]
    if out is None:
        try:
            out = fallback_composer.compose(decision, questions, clean, facts,
                                            battery=battery)
        except Exception as exc:
            return _unavailable("the composer refused the answers", sha,
                                battery_version=version, detail=exc,
                                richness=tier)
        verdict = out.get("verdict")
        composer_version = fallback_composer.VERSION

    review_reasons = list(out.get("review_reasons") or [])
    result = JudgeResult(
        verdict=verdict, action=action_of(out.get("outcome"), review_reasons),
        decision_score=out.get("decision_score"), per_question=view,
        review_reasons=review_reasons, model=response.get("model"),
        backend=response.get("backend"), battery_version=version,
        composer_version=composer_version, latency_ms=latency, cost_usd=cost,
        evidence_sha256=sha, cached=bool(response.get("cached")),
        final_probability=out.get("final_probability"), why=out.get("why", ""),
        packet_richness=tier,
        raw={"cost_source": cost_source, "problems": problems,
             "rejection_claim": out.get("rejection_claim"),
             "composer_fallback_reason": fallback_reason,
             "raw_probabilities": out.get("raw_probabilities", {})})
    return result


def _composer_version() -> str:
    K = _composer()
    return getattr(K, "VERSION", "v2") if K is not None else fallback_composer.VERSION
