"""The shadow contract: the hook seam every incumbent calls.

`observe()` is the ONLY thing a pipeline is allowed to call. It runs the
incumbent's own decision first, then — and only in shadow/active — asks the
judge, logs both, and hands the incumbent's verdict back unchanged. `active`
exists in the code and is enabled nowhere.
"""
import json

import pytest

from judge import config, core, shadow
from judge.core import JudgeResult


def result(action="reject", verdict="NO_WEBSITE", score=0.95):
    return JudgeResult(verdict=verdict, action=action, decision_score=score,
                       per_question={}, review_reasons=[], model="m",
                       backend="openrouter", battery_version="v1",
                       composer_version="min", latency_ms=12, cost_usd=1e-5,
                       evidence_sha256="a" * 64, cached=False)


EV = {"decision": "real_website", "candidate": {"domain": "advancefd.example"}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("CALIBRATED_JUDGE_OUT", str(tmp_path))
    monkeypatch.delenv("CALIBRATED_JUDGE_MODE", raising=False)
    return tmp_path


def incumbent():
    return {"verdict": "advancefd.example", "confidence": 0.99}


def test_off_never_calls_the_judge_at_all(monkeypatch):
    called = []
    monkeypatch.setattr(shadow, "judge", lambda *a, **k: called.append(1))
    out = shadow.observe("real_website", EV, incumbent_verdict="advancefd.example",
                         incumbent_confidence=0.99, skill="linkedin-validator",
                         hook_point="post_verdict", entity_key="set-a:12")
    assert out["verdict"] == "advancefd.example"
    assert called == []


def test_shadow_runs_the_judge_but_never_changes_the_verdict(monkeypatch):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "shadow")
    monkeypatch.setattr(shadow, "judge", lambda *a, **k: result())
    out = shadow.observe("real_website", EV, incumbent_verdict="advancefd.example",
                         incumbent_confidence=0.99, skill="lv",
                         hook_point="post_verdict", entity_key="set-a:12")
    assert out["verdict"] == "advancefd.example"
    assert out["overridden"] is False
    assert out["judge"]["action"] == "reject"


def test_shadow_logs_both_verdicts_and_their_agreement(monkeypatch, isolated):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "shadow")
    monkeypatch.setattr(shadow, "judge", lambda *a, **k: result())
    shadow.observe("real_website", EV, incumbent_verdict="advancefd.example",
                   incumbent_confidence=0.99, skill="lv",
                   hook_point="post_verdict", entity_key="set-a:12")
    lines = []
    for p in (isolated / "decisions").glob("*.jsonl"):
        lines += [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    assert len(lines) == 1
    row = lines[0]
    assert row["incumbent_verdict"] == "advancefd.example"
    assert row["incumbent_confidence"] == 0.99
    assert row["agreement"] is False
    assert row["mode"] == "shadow"
    assert row["entity_key"] == "set-a:12"


def test_agreement_is_true_when_the_judge_endorses_the_incumbent(monkeypatch, isolated):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "shadow")
    monkeypatch.setattr(shadow, "judge",
                        lambda *a, **k: result(action="accept",
                                               verdict="advancefd.example"))
    shadow.observe("real_website", EV, incumbent_verdict="advancefd.example",
                   incumbent_confidence=0.99, skill="lv",
                   hook_point="post_verdict", entity_key="set-a:12")
    row = json.loads(next((isolated / "decisions").glob("*.jsonl"))
                     .read_text().splitlines()[0])
    assert row["agreement"] is True


def test_a_judge_that_raises_cannot_break_the_incumbent(monkeypatch):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "shadow")

    def boom(*a, **k):
        raise RuntimeError("the judge exploded")
    monkeypatch.setattr(shadow, "judge", boom)
    out = shadow.observe("real_website", EV, incumbent_verdict="advancefd.example",
                         incumbent_confidence=0.99, skill="lv",
                         hook_point="post_verdict", entity_key="set-a:12")
    assert out["verdict"] == "advancefd.example"
    assert out["judge"]["error"] == "judge_unavailable"


def test_a_logging_failure_cannot_break_the_incumbent(monkeypatch):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "shadow")
    monkeypatch.setattr(shadow, "judge", lambda *a, **k: result())
    monkeypatch.setattr(shadow.log, "append",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    out = shadow.observe("real_website", EV, incumbent_verdict="advancefd.example",
                         incumbent_confidence=0.99, skill="lv",
                         hook_point="post_verdict", entity_key="set-a:12")
    assert out["verdict"] == "advancefd.example"


def test_active_is_implemented_behind_the_flag(monkeypatch):
    # TWO switches since 2026-09-18: the global mode, and the per-decision list
    # that says which decisions the global mode applies to. Both are required.
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "active")
    monkeypatch.setenv("CALIBRATED_JUDGE_ACTIVE_DECISIONS", "real_website")
    monkeypatch.setattr(shadow, "judge", lambda *a, **k: result())
    out = shadow.observe("real_website", EV, incumbent_verdict="advancefd.example",
                         incumbent_confidence=0.99, skill="lv",
                         hook_point="post_verdict", entity_key="set-a:12")
    assert out["overridden"] is True
    assert out["verdict"] == "NO_WEBSITE"


def test_active_still_leaves_the_incumbent_alone_when_the_judge_is_unavailable(monkeypatch):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "active")
    monkeypatch.setenv("CALIBRATED_JUDGE_ACTIVE_DECISIONS", "real_website")
    monkeypatch.setattr(shadow, "judge",
                        lambda *a, **k: JudgeResult(
                            verdict=None, action="insufficient",
                            decision_score=None, per_question={},
                            review_reasons=["judge_unavailable"], model=None,
                            backend=None, battery_version="v1",
                            composer_version="min", latency_ms=1,
                            cost_usd=0.0, evidence_sha256="b" * 64,
                            cached=False, error="judge_unavailable"))
    out = shadow.observe("real_website", EV, incumbent_verdict="advancefd.example",
                         incumbent_confidence=0.99, skill="lv",
                         hook_point="post_verdict", entity_key="set-a:12")
    assert out["verdict"] == "advancefd.example"
    assert out["overridden"] is False


def test_the_mode_is_off_unless_the_environment_says_otherwise(monkeypatch):
    """Renamed 2026-09-18: the repo `.env` now DOES set active, so the old
    claim ("nothing in this repo enables active") is retired. What survives
    it is the rule that matters — nothing is on unless something says so,
    and per-decision activation is a second switch on top (see
    test_active_decisions.py)."""
    assert config.mode() == "off"
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "shadow")
    assert config.mode() == "shadow"
