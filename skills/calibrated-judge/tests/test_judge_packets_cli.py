"""judge_packets.py: packets folder -> judge("icp_fit") -> scores.csv + review queue.

No network: the judge is injected, or (for the offline test) run with no key so
every backend is unavailable.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

import judge_packets
from judge.core import JudgeResult

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "packets" / "job8-company.json"
ICP = {"inclusions": ["general dentistry"], "exclusions": ["orthodontics-only practice"]}


def _packets(tmp_path: Path, n: int = 3) -> Path:
    base = tmp_path / "packets" / "companies"
    base.mkdir(parents=True)
    doc = json.loads(FIXTURE.read_text())
    for i in range(n):
        d = dict(doc, key=f"row-{i}")
        (base / f"row-{i}.json").write_text(json.dumps(d))
    (base / "not-a-packet.json").write_text(json.dumps({"hello": "world"}))
    return tmp_path / "packets"


def _result(action, verdict=None, p=None, cost=0.00001, error=None):
    return JudgeResult(verdict=verdict, action=action, decision_score=p,
                       per_question={"self_description_matches_icp": {"type": "noul", "p": p or 0.5}},
                       review_reasons=["near_threshold"] if action == "review" else [],
                       model="typesafe/jev-1.13", backend="fake", battery_version="v-test",
                       composer_version="c-test", latency_ms=1, cost_usd=cost,
                       evidence_sha256="x", cached=False, final_probability=p,
                       why="test", error=error)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CALIBRATED_JUDGE_OUT", str(tmp_path / "judge-out"))
    for k in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "TYPESAFE_AI_API_KEY",
              "AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GTM_STARTER_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("JEV_CACHE_PATH", str(tmp_path / "jev-cache.jsonl"))


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_scores_every_packet_and_queues_only_reviews(tmp_path):
    actions = iter([_result("accept", True, 0.93), _result("review", None, 0.55),
                    _result("reject", False, 0.08)])
    calls = []

    def fake_judge(decision, packet, **kw):
        calls.append((decision, kw.get("icp"), kw.get("entity_key")))
        return next(actions)

    out = tmp_path / "out"
    summary = judge_packets.run(_packets(tmp_path), out, criteria=ICP, judge_fn=fake_judge)
    assert [c[0] for c in calls] == ["icp_fit"] * 3
    assert all(c[1] == ICP for c in calls)
    rows = _read(out / "scores.csv")
    assert [r["action"] for r in rows] == ["accept", "review", "reject"]
    assert rows[0]["name"]                      # the fixture's record name travels
    queue = _read(out / "review_queue.csv")
    assert len(queue) == 1
    assert summary["actions"] == {"accept": 1, "review": 1, "reject": 1}
    assert (out / "review_cards.md").is_file()
    assert (out / "summary.json").is_file()


def test_max_usd_stops_the_run(tmp_path):
    def pricey(decision, packet, **kw):
        return _result("accept", True, 0.9, cost=0.6)

    summary = judge_packets.run(_packets(tmp_path, n=3), tmp_path / "out",
                                criteria=ICP, judge_fn=pricey, max_usd=1.0)
    assert summary["rows"] == 2                 # 0.6 + 0.6 passes 1.0, third row never asked


def test_offline_with_no_key_is_insufficient_and_spends_nothing(tmp_path):
    summary = judge_packets.run(_packets(tmp_path, n=2), tmp_path / "out", criteria=ICP)
    assert summary["rows"] == 2
    assert summary["actions"] == {"insufficient": 2}
    assert summary["judge_unavailable"] == 2
    assert summary["spent_usd"] == 0
    assert _read(tmp_path / "out" / "review_queue.csv") == []   # an outage is not a review


def test_missing_folder_is_a_clear_error(tmp_path):
    with pytest.raises(SystemExit):
        judge_packets.run(tmp_path / "nope", tmp_path / "out", criteria=ICP,
                          judge_fn=lambda *a, **k: None)


def test_cli_parses_and_runs(tmp_path, capsys):
    pk = _packets(tmp_path, n=1)
    icp = tmp_path / "icp.json"
    icp.write_text(json.dumps(ICP))
    assert judge_packets.main(["--packets", str(pk), "--icp", str(icp),
                               "--out", str(tmp_path / "o"), "--mode", "off"]) == 0
    rows = _read(tmp_path / "o" / "scores.csv")
    assert rows[0]["error"] == "mode_off"
