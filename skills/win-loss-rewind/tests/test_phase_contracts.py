"""The contracts between phases: outcomes, archetype membership, holdout isolation, stats.

Each test pins a bug that used to pass silently:
  * phase 3 wrote `cluster_id` + separate assignment rows, phases 5-7 read `id`
    + embedded `members` -> every archetype was empty and nothing was tested;
  * the customer CSV's `company_name`, `label` and `health` were ignored ->
    churned accounts were studied as wins and 10X as ordinary wins;
  * hypothesis generation could see holdout customers' themes and panels;
  * the small-sample branch called the odds ratio "lift" and never checked
    its p-value -> a (2,1,1,2) table "passed" with p=1.0.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "adapters"))

import backcast_validate as bv  # noqa: E402
import generate_hypotheses as gh  # noqa: E402
import segment  # noqa: E402
import wlr_schema as ws  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CALLS = ROOT / "examples" / "northwind-demo" / "corpus" / "calls"


# ------------------------------------------------------------- outcomes

@pytest.mark.parametrize("row,expected", [
    ({"company_name": "A", "outcome": "won", "health": "churned"}, "churned"),
    ({"company_name": "A", "outcome": "won", "health": "healthy", "label": "10X"}, "10x"),
    ({"company_name": "A", "outcome": "won", "health": "healthy"}, "won"),
    ({"company_name": "A", "outcome": "lost"}, "lost"),
    ({"company_name": "A", "outcome": "won", "label": "lost"}, "lost"),
    ({"company_name": "A"}, "unknown"),
])
def test_verified_outcome(row, expected):
    assert ws.normalize_customer(row)["outcome"] == expected


def test_normalize_keeps_name_crm_stage_and_id():
    c = ws.normalize_customer({"customer_id": "X1", "company_name": "Acme Dental",
                               "domain": "https://www.acme.example/", "outcome": "won",
                               "health": "churned"})
    assert c["company"] == "Acme Dental" and c["customer_id"] == "X1"
    assert c["domain"] == "acme.example"
    assert c["crm_outcome"] == "won" and c["outcome"] == "churned"


def test_10x_is_a_good_fit_and_churn_is_not():
    assert "10x" in ws.GOOD_FIT_OUTCOMES and "churned" in ws.NOT_GOOD_FIT_OUTCOMES
    assert bv.GOOD_FIT_OUTCOMES == ws.GOOD_FIT_OUTCOMES


# ------------------------------------------------------------- archetypes

def _panels(n=20):
    rows = []
    for i in range(n):
        big = i % 2 == 0
        rows.append({"customer_id": f"C{i}", "company": f"Co {i}",
                     "outcome": "won" if i % 3 else "lost",
                     "signal_panel": {"firecrawl_map": {"success": True, "data": {
                         "page_count": 60 + i if big else 5 + (i % 4), "has_careers_page": big}}}})
    return rows


def test_segment_output_loads_with_members_and_holdout(tmp_path):
    rows = _panels()
    arcs, labels, hold, sil, k = segment.segment_rows(rows, seed=42, min_cluster_size=5)
    segment.write_archetypes(tmp_path, arcs, rows, labels, hold, sil, k)
    loaded = ws.load_archetypes(tmp_path / "archetypes.jsonl")
    assert loaded and all(a["members"] for a in loaded)
    assert all("id" in a and "name" in a for a in loaded)
    for a in loaded:
        n_hold = sum(1 for m in a["members"] if m["holdout"])
        assert n_hold == max(1, round(len(a["members"]) * 0.2))
    member_ids = {m["customer_id"] for a in loaded for m in a["members"]}
    assert member_ids == {r["customer_id"] for r in rows}


def test_old_shape_is_joined(tmp_path):
    p = tmp_path / "archetypes.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"_kind": "archetype", "cluster_id": 3, "archetype_name": "Old"},
        {"_kind": "customer_assignment", "customer_id": "a", "cluster_id": 3, "holdout": False},
        {"_kind": "customer_assignment", "customer_id": "b", "cluster_id": 3, "holdout": True},
    ]) + "\n")
    [a] = ws.load_archetypes(p)
    assert a["id"] == 3 and a["name"] == "Old"
    assert ws.split_ids(a) == (["a"], ["b"])


def test_an_archetype_without_members_is_refused(tmp_path):
    p = tmp_path / "archetypes.jsonl"
    p.write_text(json.dumps({"_kind": "archetype", "cluster_id": 1}) + "\n")
    with pytest.raises(ValueError):
        ws.load_archetypes(p)


def test_outliers_are_left_out(tmp_path):
    p = tmp_path / "archetypes.jsonl"
    p.write_text(json.dumps({"_kind": "archetype", "cluster_id": 9, "archetype_quality": "outlier",
                             "_excluded_from_archetypes": True, "members": []}) + "\n")
    assert ws.load_archetypes(p) == []


# ------------------------------------------------------------- holdout isolation

def test_hypothesis_generation_sees_training_members_only():
    arc = {"id": 1, "name": "A", "members": [
        {"customer_id": "t1", "holdout": False, "outcome": "won"},
        {"customer_id": "t2", "holdout": False, "outcome": "lost"},
        {"customer_id": "h1", "holdout": True, "outcome": "won"}],
        "outcome_breakdown": {"won": 2, "lost": 1},
        "top_3_member_companies": [{"customer_id": "h1"}, {"customer_id": "t1"}]}
    themes = {"t1": [{"theme": "train pain"}], "h1": [{"theme": "HOLDOUT pain"}]}
    panels = {"t1": {"customer_id": "t1"}, "t2": {"customer_id": "t2"}, "h1": {"customer_id": "h1"}}
    view, th, sample = gh.training_inputs(arc, themes, panels)
    blob = json.dumps([view, th, sample])
    assert "h1" not in blob and "HOLDOUT" not in blob
    assert view["outcome_breakdown"]["won"] == 1 and view["member_count"] == 2


# ------------------------------------------------------------- statistics

def test_small_table_reports_probability_ratio_not_odds_ratio():
    out = bv.compute_lift_with_ci(2, 1, 1, 2)
    assert out["method"] == "fisher_exact"
    assert out["lift"] == pytest.approx(2.0)            # (2/3) / (1/3)
    assert out["fisher_odds_ratio"] == pytest.approx(4.0)
    assert out["fisher_p_value"] == pytest.approx(1.0)


def test_small_table_with_p_1_does_not_survive():
    t = bv.compute_lift_with_ci(2, 1, 1, 2)
    ok, reasons = bv.apply_survival_rules(t, t, 1.5, 1.2, min_train_n=1, min_holdout_n=1)
    assert not ok and "Fisher p" in reasons[0]


def test_tiny_splits_are_insufficient_evidence():
    t = bv.compute_lift_with_ci(4, 0, 1, 3)
    ok, reasons = bv.apply_survival_rules(t, t, 1.5, 1.2)
    assert not ok and reasons[0].startswith("insufficient evidence")


def test_a_strong_replicated_signal_survives():
    train = bv.compute_lift_with_ci(30, 2, 6, 30)
    hold = bv.compute_lift_with_ci(8, 0, 2, 8)
    ok, reasons = bv.apply_survival_rules(train, hold, 1.5, 1.2)
    assert ok, reasons


def test_no_discriminator_is_an_explicit_result():
    flag = bv.no_discriminator_flag(2, [
        {"provisional_reasons": ["insufficient evidence: holdout has 3"]},
        {"provisional_reasons": ["train lift 1.1 < 1.5"]}])
    assert flag["result"] == "no reliable discriminator found"
    assert flag["causes"] == {"insufficient_evidence": 1, "no_detectable_relationship": 1}


# ------------------------------------------------------------- transcript import

def test_markdown_transcripts_import_with_attribution(tmp_path):
    import transcripts_md as tm
    matched, attributed = tm.import_dir(CALLS, {"northwind.example"})
    assert len(matched) == len(list(CALLS.glob("*.md"))) and matched[0]["customer_id"]
    river = next(a for a in attributed if a["call_id"] == "CALL-0001")
    roles = {r["name"]: r["role"] for r in river["roster"]}
    assert roles["Jane Rep"] == "company" and roles["Dana Whitfield"] == "customer"
    grades = {s["text"][:20]: s["elicitation"] for s in river["customer_statements"]}
    assert grades["Honestly, the phones"] == "unprompted"
    assert grades["Yes. One calendar ac"] == "led"
    assert tm.main([str(CALLS), "--run-id", "t", "--seller-domain", "northwind.example",
                    "--out-dir", str(tmp_path)]) == 0
    assert (tmp_path / "calls_with_speaker_attribution.jsonl").read_text().count("\n") == len(matched)


# ------------------------------------------------------------- orchestration gate

def _run_dir(tmp_path, monkeypatch):
    import orchestrate
    monkeypatch.setattr(orchestrate, "OUTPUTS_ROOT", tmp_path)
    rd = tmp_path / "r1"
    rd.mkdir()
    (rd / "data_confidence.md").write_text("ok")
    return orchestrate, rd


def test_stop_after_3_runs_no_later_phase(tmp_path, monkeypatch):
    orch, rd = _run_dir(tmp_path, monkeypatch)
    ran = []
    rc = orch.main(["--run-id", "r1", "--resume-from", "2", "--stop-after", "3"],
                   runner=lambda ph, rid: (ran.append(ph), (True, 0.0))[1])
    assert rc == 0 and ran == [2, 3]


def test_phase_4_needs_approval_and_reapproval_after_edit(tmp_path, monkeypatch):
    orch, rd = _run_dir(tmp_path, monkeypatch)
    (rd / "archetypes.jsonl").write_text('{"_kind": "archetype", "id": 1}\n')
    ran = []
    runner = lambda ph, rid: (ran.append(ph), (True, 0.0))[1]  # noqa: E731
    assert orch.main(["--run-id", "r1", "--resume-from", "4"], runner=runner) == 3 and ran == []
    assert orch.main(["--run-id", "r1", "--resume-from", "4", "--approve-archetypes"], runner=runner) == 0
    assert ran == [4, 5, 6, 7]
    (rd / "archetypes.jsonl").write_text('{"_kind": "archetype", "id": 2}\n')   # edited after approval
    ran.clear()
    assert orch.main(["--run-id", "r1", "--resume-from", "4"], runner=runner) == 3 and ran == []


# ------------------------------------------------------------- namer

class _Resp:
    def __init__(self, text):
        self.content = [type("B", (), {"text": text})()]


class _Client:
    def __init__(self, text=None, boom=False):
        self.text, self.boom = text, boom
        self.messages = self

    def create(self, **kw):
        if self.boom:
            raise RuntimeError("api down")
        return _Resp(self.text)


def _named(tmp_path, client):
    rows = _panels()
    arcs, labels, hold, sil, k = segment.segment_rows(
        rows, seed=42, namer=lambda s: segment.name_cluster_via_llm(s, "sys", client=client))
    segment.write_archetypes(tmp_path, arcs, rows, labels, hold, sil, k)
    return ws.load_archetypes(tmp_path / "archetypes.jsonl"), (tmp_path / "archetypes.md").read_text()


def test_namer_success_is_parsed_and_keeps_numeric_features(tmp_path):
    yaml_text = ("```yaml\narchetype_id: 0\narchetype_name: \"Multi-site group practice\"\n"
                 "one_liner: \"Several offices, one front desk team\"\ncanonical_features:\n"
                 "  - fc_page_count\nspot_check_quote: \"we run six offices\"\n```")
    arcs, md = _named(tmp_path, _Client(yaml_text))
    assert all(a["archetype_name"] == "Multi-site group practice" for a in arcs)
    assert all(isinstance(f, dict) and "gap" in f for a in arcs for f in a["canonical_features"])
    assert arcs[0]["named_features"] == ["fc_page_count"]
    assert "Multi-site group practice" in md


def test_namer_failure_and_bad_yaml_fall_back_without_crashing(tmp_path):
    arcs, md = _named(tmp_path / "a", _Client(boom=True)) if (tmp_path / "a").mkdir() is None else None
    assert all("unnamed" in a["archetype_name"] for a in arcs) and "Top distinguishing" in md
    arcs, _ = _named(tmp_path / "b", _Client("not: [valid")) if (tmp_path / "b").mkdir() is None else None
    assert all("unnamed" in a["archetype_name"] for a in arcs)


def test_leading_question_echo_is_led_and_grades_are_provisional():
    import transcripts_md as tm
    prev = {"role": "company", "text": "Would a single calendar solve that?"}
    assert tm.elicitation("A single calendar would solve that.", prev) == "led"
    assert tm.elicitation("Honestly the phones are the problem.", prev) == "unprompted"
    assert tm.elicitation("Our phones ring all day.", {"role": "company", "text": "Thanks."}) == "unprompted"
    _, attributed = tm.import_dir(CALLS, {"northwind.example"})
    assert all(s["provisional"] and s["grade_basis"] == "heuristic"
               for a in attributed for s in a["customer_statements"])


# ------------------------------------------------------------- live parsers + renderer

def test_live_scrape_parser_extracts_office_count_and_booking():
    import panel_parsers as pp
    blob = pp.parse_firecrawl_scrape({"data": {"markdown": "We run two offices. Book an appointment online.",
                                               "metadata": {"title": "X"}}})
    assert blob["data"]["location_count"] == 2 and blob["data"]["mentions_online_booking"] is True
    solo = pp.parse_firecrawl_scrape({"data": {"markdown": "Visit our office on Main Street."}})
    assert solo["data"]["location_count"] == 1 and solo["data"]["mentions_online_booking"] is False
    none = pp.parse_firecrawl_scrape({"data": {"markdown": "Welcome."}})
    assert none["data"]["location_count"] is None


def test_live_fetcher_uses_the_shared_parser(monkeypatch):
    import enrich_panel

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"markdown": "Care at our five offices.", "metadata": {}}}

    monkeypatch.setattr(enrich_panel, "FIRECRAWL_API_KEY", "test-only")
    monkeypatch.setattr(enrich_panel.requests, "post", lambda *a, **k: R())
    out = enrich_panel.firecrawl_scrape_homepage("x.example")
    assert out["data"]["location_count"] == 5


def test_renderer_labels_lift_not_odds_ratio():
    line = bv._fmt_lift_line("Train", bv.compute_lift_with_ci(2, 1, 1, 2))
    assert "lift: 2.00" in line and "odds ratio 4.00" in line


def test_reference_date_anchors_10x_on_close_date():
    import reference_dates as refd
    d, note = refd.resolve_reference_date({"outcome": "10x", "close_date": "2024-03-01"})
    assert str(d) == "2024-03-01" and note == "close_date"


def test_customers_from_dossiers_writes_the_canonical_contract(tmp_path):
    import csv as _csv
    import subprocess
    demo = ROOT / "examples" / "northwind-demo"
    subprocess.run([sys.executable, str(demo / "build_demo_dossiers.py"), str(tmp_path / "d")],
                   check=True, capture_output=True)
    import customers_from_dossiers as cfd
    assert cfd.main(["--dossiers", str(tmp_path / "d"), "--out", str(tmp_path / "c.csv")]) == 0
    rows = list(_csv.DictReader(open(tmp_path / "c.csv")))
    assert list(rows[0].keys()) == ws.CUSTOMER_COLUMNS and len(rows) == 8
    labels = {r["company_name"]: r["label"] for r in rows}
    assert labels["Rivermark Dental Group"] == "10X" and labels["Elmstead Oral Surgery"] == "lost"
    assert all(r["close_date"] for r in rows)
