"""The review queue — where a judge's "I am not sure" becomes a human decision.

`active` is only honest if the maybes actually reach somebody. This module
collects every `review` row a run logged into one CSV (and, on request, one
Google Sheet) with the judge's own reasons, its top question probabilities, and
the incumbent's verdict beside them — so the human is deciding, not re-deriving.

THE CONTRACT UNDER TEST
  1. Only `review` rows, only this run.
  2. The columns are exactly the nine a reviewer was promised, in order, with
     the last two blank for them to fill in.
  3. The probabilities are the judge's own numbers, unrounded into fiction.
  4. An empty queue still writes a file — "nothing to review" must be visible,
     not indistinguishable from a writer that never ran.
  5. Nothing here can write outside the review directory, whatever a run id says.
  6. The Sheet is opt-in. Without `--sheet` nothing touches Google.
"""
import csv
import json

import pytest

from judge import config, review_queue as rq


def _row(**over):
    row = {"ts": "2026-09-18T10:00:00+00:00", "run_id": "R1",
           "skill": "domain-resolver", "hook_point": "tier6_model_judge",
           "decision": "real_website", "entity_key": "set-a:12",
           "evidence_sha256": "a" * 64, "battery_version": "v2",
           "composer_version": "v2", "model": "jev-1.13.0",
           "backend": "typesafe",
           "per_question": {"same_organization": {"type": "noul", "p": 0.91},
                            "parked_or_for_sale": {"type": "noul", "p": 0.12},
                            "directory_listing": {"type": "noul", "p": 0.55},
                            "error_page": {"type": "noul", "p": 0.02}},
           "decision_score": 0.55, "action": "review",
           "verdict": None, "review_reasons": ["directory_listing"],
           "incumbent_verdict": "advancefd.example", "incumbent_confidence": 0.9,
           "agreement": None, "latency_ms": 500, "cost_usd": 6e-05,
           "cached": False, "mode": "active", "error": None}
    row.update(over)
    return row


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_OUT, str(tmp_path))
    return tmp_path


# --- 1. what lands in the queue --------------------------------------------

def test_only_review_rows_from_this_run(out):
    rows = [_row(), _row(action="accept"), _row(action="reject"),
            _row(run_id="R2"), _row(action="insufficient")]
    assert [r["action"] for r in rq.review_rows("R1", rows)] == ["review"]


def test_no_run_id_means_every_run(out):
    rows = [_row(), _row(run_id="R2")]
    assert len(list(rq.review_rows(None, rows))) == 2


def test_an_unavailable_judge_is_not_a_review(out):
    rows = [_row(action="review", error="judge_unavailable")]
    assert list(rq.review_rows("R1", rows)) == []


# --- 2. the columns a human reads -------------------------------------------

def test_the_header_is_the_ten_promised_columns(out):
    """`what the judge read` joined the row on 2026-09-21 (the packet card).

    Three probabilities with no field names beside them tell a reviewer which
    question wobbled and never what it was looking at. The column sits with the
    evidence, and the last two stay the human's.
    """
    path = rq.write_csv("R1", [_row()])
    with path.open() as fh:
        header = next(csv.reader(fh))
    assert header == ["entity", "decision", "judge action", "review reasons",
                      "top question probabilities", "what the judge read",
                      "incumbent verdict", "evidence link", "YOUR DECISION",
                      "note"]


def test_the_row_carries_the_judges_own_words_and_numbers(out):
    path = rq.write_csv("R1", [_row()])
    row = list(csv.DictReader(path.open()))[0]
    assert row["entity"] == "set-a:12"
    assert row["decision"] == "real_website"
    assert row["judge action"] == "review"
    assert row["review reasons"] == "directory_listing"
    assert row["incumbent verdict"] == "advancefd.example"
    assert row["top question probabilities"].startswith("same_organization=0.91")
    assert "directory_listing=0.55" in row["top question probabilities"]
    # the weakest of four is dropped: a reviewer reads three
    assert "error_page" not in row["top question probabilities"]
    assert row["YOUR DECISION"] == ""
    assert row["note"] == ""


def test_the_evidence_link_points_at_the_logged_row(out):
    path = rq.write_csv("R1", [_row()])
    link = list(csv.DictReader(path.open()))[0]["evidence link"]
    assert "2026-09.jsonl" in link
    assert "a" * 16 in link


def test_a_choice_question_reports_its_strongest_label():
    text = rq.top_probabilities({"same_organization": {
        "type": "choice", "probs": {"accept_candidate": 0.71,
                                    "review_candidate": 0.2,
                                    "reject_candidate": 0.09}}})
    assert text == "same_organization:accept_candidate=0.71"


def test_a_question_with_no_number_is_not_invented():
    assert rq.top_probabilities({"q": {"type": "noul", "p": None}}) == ""
    assert rq.top_probabilities({}) == ""
    assert rq.top_probabilities(None) == ""


def test_several_reasons_are_joined_readably(out):
    path = rq.write_csv("R1", [_row(review_reasons=["a", "b"])])
    assert list(csv.DictReader(path.open()))[0]["review reasons"] == "a; b"


# --- 3. the file itself -----------------------------------------------------

def test_the_default_path_is_the_review_directory(out):
    path = rq.write_csv("R1", [_row()])
    assert path == out / "review" / "R1.csv"
    assert path.exists()


def test_an_empty_queue_still_writes_a_file(out):
    path = rq.write_csv("R9", [])
    assert path.exists()
    assert len(list(csv.reader(path.open()))) == 1          # the header alone


def test_a_run_id_cannot_escape_the_review_directory(out):
    path = rq.write_csv("../../etc/passwd", [_row(run_id="../../etc/passwd")])
    assert path.parent == out / "review"
    assert ".." not in path.name


def test_rows_are_read_from_the_decision_log_when_none_are_passed(out):
    from judge import log
    p = log.path_for()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_row()) + "\n" + json.dumps(_row(action="accept")) + "\n")
    path = rq.write_csv("R1")
    assert len(list(csv.DictReader(path.open()))) == 1


# --- 4. the Sheet is opt-in --------------------------------------------------

def test_the_cli_writes_the_csv_and_touches_nothing_else(out, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(rq, "create_sheet", lambda *a, **k: called.append(a))
    rq.main(["--run-id", "R1"])
    assert called == []
    assert (out / "review" / "R1.csv").exists()


def test_the_sheet_title_names_the_run_and_the_day():
    import datetime as dt
    title = rq.sheet_title("R1", when=dt.date(2026, 9, 18))
    assert title == "Judge review — R1 — 2026-09-18"


def test_a_missing_google_auth_is_a_message_not_a_crash(out, monkeypatch, capsys):
    monkeypatch.setattr(rq, "_sheets_service", lambda: None)
    rq.main(["--run-id", "R1", "--sheet"])
    printed = capsys.readouterr().out
    assert "Google" in printed
    assert (out / "review" / "R1.csv").exists()
