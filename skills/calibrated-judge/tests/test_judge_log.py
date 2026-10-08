"""The append-only decision log."""
import json

import pytest

from judge import config, log
from judge.core import JudgeResult


def res(**kw):
    base = dict(verdict="advancefd.example", action="accept", decision_score=0.97,
                per_question={"is_the_orgs_own_site": {"type": "noul", "p": 0.97}},
                review_reasons=[], model="typesafe/jev-1.13",
                backend="openrouter", battery_version="v1",
                composer_version="min", latency_ms=690, cost_usd=1.18e-5,
                evidence_sha256="c" * 64, cached=False)
    base.update(kw)
    return JudgeResult(**base)


@pytest.fixture(autouse=True)
def out_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CALIBRATED_JUDGE_OUT", str(tmp_path))
    return tmp_path


def test_the_log_path_is_month_partitioned_under_outputs(out_dir):
    p = log.path_for()
    assert p.parent == out_dir / "decisions"
    assert p.name.endswith(".jsonl") and len(p.stem) == 7


def test_the_repo_gitignores_the_log_directory():
    root = config.blueprint_root()
    if not (root / "skills" / "blueprint").is_dir():
        pytest.skip("standalone install: no repo above the skill, nothing to gitignore")
    text = (root / ".gitignore").read_text()
    assert any(line.strip() in ("outputs/", "/outputs/", "outputs")
               for line in text.splitlines())


def test_one_append_writes_one_line_with_every_required_field(out_dir):
    log.append(res(), skill="linkedin-validator", hook_point="post_verdict",
               decision="real_website", entity_key="set-a:12", run_id="r1",
               incumbent_verdict="advancefd.example", incumbent_confidence=0.99,
               mode="shadow")
    rows = [json.loads(x) for x in log.path_for().read_text().splitlines()]
    assert len(rows) == 1
    for k in ("ts", "run_id", "skill", "hook_point", "decision", "entity_key",
              "evidence_sha256", "battery_version", "composer_version",
              "model", "backend", "per_question", "action",
              "incumbent_verdict", "incumbent_confidence", "agreement",
              "latency_ms", "cost_usd", "mode"):
        assert k in rows[0], k
    assert rows[0]["per_question"]["is_the_orgs_own_site"]["p"] == 0.97


def test_appends_accumulate_and_never_rewrite(out_dir):
    for i in range(3):
        log.append(res(), skill="lv", hook_point="h", decision="real_website",
                   entity_key=f"row:{i}", run_id="r1", mode="shadow")
    assert len(log.path_for().read_text().strip().splitlines()) == 3


def test_agreement_is_none_when_there_is_no_incumbent_to_compare(out_dir):
    log.append(res(), skill="lv", hook_point="h", decision="real_website",
               entity_key="x", run_id="r1", mode="shadow")
    row = json.loads(log.path_for().read_text().splitlines()[0])
    assert row["agreement"] is None


def test_a_judge_unavailable_row_is_still_logged(out_dir):
    log.append(res(verdict=None, action="insufficient", model=None,
                   backend=None, error="judge_unavailable"),
               skill="lv", hook_point="h", decision="real_website",
               entity_key="x", run_id="r1", mode="shadow")
    row = json.loads(log.path_for().read_text().splitlines()[0])
    assert row["error"] == "judge_unavailable"
    assert row["action"] == "insufficient"


def test_reading_the_log_skips_a_corrupt_line_rather_than_dying(out_dir):
    log.append(res(), skill="lv", hook_point="h", decision="real_website",
               entity_key="x", run_id="r1", mode="shadow")
    with log.path_for().open("a") as fh:
        fh.write("{not json\n")
    rows = list(log.read_all())
    assert len(rows) == 1
