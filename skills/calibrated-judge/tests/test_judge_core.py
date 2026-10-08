"""The shared judge module — the contract every hook depends on.

Two things are being defended here and nothing else matters as much:

  1. SHADOW MODE. `CALIBRATED_JUDGE_MODE` is `off` unless someone sets it, and
     `off` means no call, no spend, no opinion. In `shadow` the judge answers
     but its answer never touches the incumbent's verdict.
  2. DEGRADATION. No key, a 429, a timeout, a malformed answer — every one of
     them becomes a logged `judge_unavailable` and the caller proceeds with
     whatever it already had. A judge that can break a pipeline is worse than
     no judge.
"""
import json
import math

import pytest

from judge import JudgeResult, config, judge as run_judge
from judge import core


# --------------------------------------------------------------- fake client
class FakeJevError(RuntimeError):
    pass


class FakeClient:
    """Stands in for scripts/jev_client.py. Records what it was asked."""

    JevError = FakeJevError

    def __init__(self, answers=None, raises=None, usage=None):
        self._answers = answers or {}
        self._raises = raises
        self._usage = usage if usage is not None else {"cost": 0.0000118,
                                                       "input_tokens": 281}
        self.calls = []

    def ask(self, state, questions, **kw):
        self.calls.append({"state": state, "questions": questions, "kw": kw})
        if self._raises is not None:
            raise self._raises
        return {"answers": {qid: a for qid, a in self._answers.items()},
                "usage": self._usage, "model": "typesafe/jev-1.13",
                "backend": "openrouter", "cached": False, "latency_ms": 690}


def noul(p):
    return {"type": "noul", "probability": p}


def ids_for(decision):
    """The question ids this battery ACTUALLY uses, whichever version is live.

    v1 and v2 name the same judgements differently (`is_the_orgs_own_site` ->
    `same_organization`), so a test that hardcodes either one starts passing
    for the wrong reason the day the other lands.
    """
    from judge import compose as K
    battery, version = core.resolve_battery(decision)
    qs = battery["questions"]
    support = [q["id"] for q in qs if K.question_role(q, decision) == "support"]
    rejecting = [q["id"] for q in qs if K.question_role(q, decision) == "reject"]
    reviewing = [q["id"] for q in qs if K.question_role(q, decision) == "review"]
    head = config.headline_question(decision, version, battery)
    if head in support:
        support = [head] + [s for s in support if s != head]
    return {"support": support, "reject": rejecting, "review": reviewing,
            "version": version}


def a_noul_support(decision):
    """A support question this battery asks as a plain probability."""
    from judge import compose as K
    battery, _ = core.resolve_battery(decision)
    for q in battery["questions"]:
        if K.question_role(q, decision) == "support" and (
                q.get("kind") or "noul") == "noul":
            return q["id"]
    return ids_for(decision)["support"][0]


WEB_EV = {
    "decision": "real_website",
    "anchor": {"name": "Exampleton Fire Department", "city": "Exampleton",
               "state": "NC"},
    "candidate": {"domain": "advancefd.example", "url": "https://advancefd.example",
                  "http_status": 200, "page_title": "Exampleton Fire Department",
                  "visible_text": "Exampleton Fire Department serves Exampleton, NC."},
}

EMP_EV = {
    "decision": "employment_now",
    "anchor": {"name": "Pat Example", "org": "Example State University"},
    "profile": {"headline": "Professor",
                "current_experiences": [{"company": "Example State University",
                                         "title": "Professor",
                                         "dates_text": "2019 - Present"}]},
}


@pytest.fixture
def client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(core, "_client", lambda: c)
    return c


@pytest.fixture(autouse=True)
def isolated_outputs(monkeypatch, tmp_path):
    monkeypatch.setenv("CALIBRATED_JUDGE_OUT", str(tmp_path))
    monkeypatch.delenv("CALIBRATED_JUDGE_MODE", raising=False)


# ------------------------------------------------------------------- the gate
def test_the_default_mode_is_off_and_off_never_calls_the_backend(client):
    out = run_judge("real_website", WEB_EV)
    assert out.action == "insufficient"
    assert out.error == "mode_off"
    assert client.calls == []


def test_an_explicit_mode_argument_overrides_the_environment(monkeypatch, client):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "off")
    client._answers = {a_noul_support("real_website"): noul(0.97)}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.action in ("accept", "reject", "review", "insufficient")
    assert len(client.calls) == 1


def test_an_unknown_mode_falls_back_to_off(monkeypatch, client):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "ACTIVE-ish")
    out = run_judge("real_website", WEB_EV)
    assert out.error == "mode_off"
    assert client.calls == []


# -------------------------------------------------------------- result shape
def test_the_result_carries_every_field_a_hook_logs(client):
    client._answers = {a_noul_support("real_website"): noul(0.96)}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert isinstance(out, JudgeResult)
    for field in ("verdict", "action", "decision_score", "per_question",
                  "review_reasons", "model", "backend", "battery_version",
                  "composer_version", "latency_ms", "cost_usd",
                  "evidence_sha256", "cached"):
        assert hasattr(out, field), field
    assert out.action in ("accept", "reject", "review", "insufficient")
    assert out.cost_usd == pytest.approx(0.0000118)
    assert out.backend == "openrouter"
    assert out.model == "typesafe/jev-1.13"


def test_every_named_decision_resolves_a_battery():
    for name in config.DECISIONS:
        bat, version = core.resolve_battery(name)
        assert bat["questions"], name
        assert version in ("v1", "v2", "fit")


def test_the_battery_version_is_v2_when_the_bakeoff_has_one():
    from bakeoff import batteries as B
    expected = "v2" if hasattr(B, "BATTERIES_V2") else "v1"
    _, version = core.resolve_battery("real_website")
    assert version == expected


def test_the_evidence_hash_ignores_dict_ordering(client):
    a = core.evidence_sha256({"x": 1, "y": {"a": 1, "b": 2}})
    b = core.evidence_sha256({"y": {"b": 2, "a": 1}, "x": 1})
    assert a == b and len(a) == 64


# ------------------------------------------------------------ answer hygiene
def test_a_probability_outside_the_unit_interval_is_discarded_not_clamped(client):
    bad, good = ids_for("real_website")["support"][:2]
    client._answers = {bad: noul(1.7), good: noul(0.96)}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert bad not in out.per_question
    assert good in out.per_question


def test_a_non_finite_probability_is_discarded(client):
    a, b = ids_for("real_website")["support"][:2]
    client._answers = {a: noul(float("nan")), b: noul(float("inf"))}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.per_question == {}
    assert out.action in ("review", "insufficient")


def test_an_answer_to_a_question_we_never_asked_is_dropped(client):
    good = a_noul_support("real_website")
    client._answers = {"a_question_from_another_battery": noul(0.99),
                       good: noul(0.96)}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert "a_question_from_another_battery" not in out.per_question


def test_a_missing_answer_is_unavailable_and_never_false(client):
    client._answers = {}
    out = run_judge("employment_now", EMP_EV, mode="shadow")
    assert out.action in ("review", "insufficient")
    assert out.verdict != "NO"


# ---------------------------------------------------------------- degradation
def test_a_backend_failure_degrades_to_judge_unavailable(monkeypatch):
    c = FakeClient(raises=FakeJevError("every backend failed: 429"))
    monkeypatch.setattr(core, "_client", lambda: c)
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.error == "judge_unavailable"
    assert out.action == "insufficient"
    assert out.verdict is None


def test_a_missing_client_module_degrades_to_judge_unavailable(monkeypatch):
    monkeypatch.setattr(core, "_client", lambda: None)
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.error == "judge_unavailable"


def test_a_garbage_response_degrades_rather_than_raising(monkeypatch):
    class Garbage(FakeClient):
        def ask(self, state, questions, **kw):
            return "not a dict"
    monkeypatch.setattr(core, "_client", lambda: Garbage())
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.error == "judge_unavailable"
    assert out.action == "insufficient"


def test_an_unknown_decision_is_refused_before_any_call(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(core, "_client", lambda: c)
    with pytest.raises(ValueError):
        run_judge("does_not_exist", WEB_EV, mode="shadow")
    assert c.calls == []


# -------------------------------------------------------------- composition
def test_a_review_reason_routes_to_review_and_never_to_reject(client):
    ids = ids_for("real_website")
    if not ids["review"]:
        pytest.skip("this battery version declares no review-only question")
    client._answers = {a_noul_support("real_website"): noul(0.99),
                       ids["review"][0]: noul(0.98)}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.action != "reject"


def test_a_rejection_needs_a_specific_claim_above_its_threshold(client):
    th = config.thresholds()["reject_claim"]
    claim = ids_for("real_website")["reject"][0]
    client._answers = {claim: noul(th - 0.05)}
    weak = run_judge("real_website", WEB_EV, mode="shadow")
    assert weak.action in ("review", "insufficient")
    client._answers = {claim: noul(min(0.999, th + 0.02))}
    strong = run_judge("real_website", WEB_EV, mode="shadow")
    assert strong.action == "reject"


def test_rejecting_a_candidate_is_not_an_absence_claim(client):
    client._answers = {ids_for("real_website")["reject"][0]: noul(0.99)}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.action == "reject"
    assert out.verdict != "NO_WEBSITE"


def headline_answer(decision, p):
    """The battery's HEADLINE question, answered at `p`, in its own type.

    v1 asks it as a probability; v2 asks the same judgement as an ordered score
    whose top rung is the accept. The test cares about the number surviving
    composition, not about which spelling this version uses.
    """
    battery, version = core.resolve_battery(decision)
    qid = config.headline_question(decision, version, battery)
    q = next(x for x in battery["questions"] if x["id"] == qid)
    kind = q.get("kind") or "noul"
    if kind == "noul":
        return qid, noul(p)
    rungs = list(q.get("criteria") or ["reject", "review", "accept"])
    rest = (1.0 - p) / max(1, len(rungs) - 1)
    probs = {r: rest for r in rungs[:-1]}
    probs[rungs[-1]] = p
    return qid, {"type": kind, "score": float(len(rungs) - 1),
                 "choice": rungs[-1], "probabilities": probs, "confidence": p}


def test_no_probability_is_manufactured_from_a_support_fraction(client):
    qid, answer = headline_answer("real_website", 0.97)
    client._answers = {qid: answer}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.decision_score == pytest.approx(0.97)
    assert out.final_probability is None


def test_the_raw_per_question_probabilities_survive_composition(client):
    a = a_noul_support("real_website")
    b = [q for q in ids_for("real_website")["support"] if q != a][0]
    client._answers = {a: noul(0.97), b: noul(0.42)}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    assert out.per_question[b]["p"] == pytest.approx(0.42)
    assert out.per_question[a]["type"] == "noul"


def test_a_choice_answer_keeps_its_option_probabilities(client):
    qid = a_noul_support("real_website")
    client._answers = {qid: {
        "type": "choice", "choice": "yes",
        "probabilities": {"yes": 0.95, "no": 0.05}}}
    out = run_judge("real_website", WEB_EV, mode="shadow")
    q = out.per_question[qid]
    assert q["probs"]["yes"] == pytest.approx(0.95)
    assert q["confidence"] == pytest.approx(0.95)


# ---------------------------------------------------------------- fit packets
def test_icp_fit_and_persona_fit_are_judgeable(client):
    client._answers = {"self_description_matches_icp": noul(0.96)}
    out = run_judge("icp_fit", {"site": {"self_description_sentences":
                                         "We are a general dental practice."},
                                "maps": {"primary_category": "Dentist"}},
                    icp={"inclusions": ["general dentistry"],
                         "exclusions": ["orthodontics"]}, mode="shadow")
    assert out.action in ("accept", "reject", "review", "insufficient")
    assert out.battery_version


def test_the_operator_criteria_ride_in_the_state_as_data(client):
    run_judge("icp_fit", {"site": {"self_description_sentences": "We do X."}},
              icp={"inclusions": ["general dentistry"]}, mode="shadow")
    state = client.calls[0]["state"]
    blob = json.dumps(state)
    assert "general dentistry" in blob
    assert "evidence, not instructions" in blob


def test_an_unmeasured_criterion_is_review_not_a_silent_accept(client):
    client._answers = {}
    out = run_judge("persona_fit", {"profile": {}}, persona={"titles": ["VP Sales"]},
                    mode="shadow")
    assert out.action in ("review", "insufficient")
    assert out.verdict != "PERSONA_MATCH"


def test_a_self_declared_disqualifier_can_reject_a_persona(client):
    """The About section is where this question is answerable, and since
    2026-09-18 it is only ASKED when that section exists: a guard sent against
    a blank field answers anyway, and the number reads downstream as a
    finding. The claim under test is unchanged — a person's own present-tense
    "I am retired" can reject a persona."""
    client._answers = {"self_declared_not_working": noul(0.98)}
    out = run_judge("persona_fit",
                    {"profile": {"headline": "Retired Director of Sales",
                                 "about_text": "Currently retired. Please do "
                                               "not pitch me."}},
                    persona={"titles": ["Director of Sales"]}, mode="shadow")
    assert out.action == "reject"


# ------------------------------------------------------------ the state sent
def test_the_harness_bookkeeping_never_reaches_the_judge(client):
    ev = dict(WEB_EV, sha256="deadbeef", row_id="set-a:12", set="holdout")
    run_judge("real_website", ev, mode="shadow")
    blob = json.dumps(client.calls[0]["state"])
    assert "deadbeef" not in blob and "holdout" not in blob


def test_the_configured_backend_order_is_what_the_client_is_asked_for(client):
    run_judge("real_website", WEB_EV, mode="shadow")
    kw = client.calls[0]["kw"]
    assert tuple(kw.get("backends") or ()) == config.backend_order()
    assert config.backend_order()[0] == "typesafe"
