"""`judge.card` — the packet card, judge-side, and the review queue's new half.

A reviewer used to get three probabilities and a list of review reasons. Three
probabilities with no evidence names beside them say which question wobbled and
never what it was looking at, and "the model was unsure" is not a question a
human can answer. What is guarded here:

  1. **The card resolves the BATTERY**, so questions are printed in the words
     they were asked in and beside the fields each one reads — at the version
     the ROW recorded, not the version this harness happens to ship today. A
     row judged under a battery that is gone says the historical wording is
     unavailable; it never substitutes today's (the 2026-09-21 blind review).
  2. **A decision-log row alone is enough.** The log keeps the evidence HASH,
     not the evidence, so the card renders from the row's own richness block
     and says plainly that the values are not on the row.
  3. **The review CSV carries `what the judge read`**, and the cards file is
     written beside it — including when the queue is empty, because "nothing to
     review" has to be visible.
  4. **Nothing here can raise.** A row with no decision, no battery and no
     packet still produces a card, because a review row that arrives without
     one is a row nobody answers.

Every person and company below is invented.
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from judge import card as card_mod                          # noqa: E402
from judge import review_queue                              # noqa: E402


def flat(text: str) -> str:
    """Wrapping removed — the card wraps on purpose; an assertion about a
    sentence must not also be an assertion about where it broke."""
    return " ".join(text.split())


PERSONA_ROW = {
    "ts": "2026-09-21T00:00:00+00:00", "run_id": "r1", "entity_key": "p-1",
    "decision": "persona_fit", "action": "review",
    "review_reasons": ["still_missing: profile.about"],
    "verdict": None, "decision_score": 0.42, "round": 1,
    "enrichment_added": [], "incumbent_verdict": None,
    "per_question": {"title_matches_persona": {"type": "noul", "p": 0.91},
                     "role_is_operating": {"type": "noul", "p": 0.55}},
    "packet_richness": {"tier": "partial", "kind": "person",
                        "present_fields": ["profile.headline",
                                           "profile.current_experiences"],
                        "missing_required": ["profile.about or profile.about_text",
                                             "profile.location"]},
}

PERSON_PACKET = {
    "kind": "person", "entity_key": "p-1",
    "profile": {"headline": "VP Sales at Northwind Systems",
                "current_experiences": [{"title": "VP Sales",
                                         "company": "Northwind Systems",
                                         "dates_text": "2021-03 – present"}]},
    "absent": {"profile.about": {"status": "fetch_failed",
                                 "reason": "the About section did not load"},
               "profile.location": {"status": "absent",
                                    "reason": "the profile carries no location"}},
}

PERSONA = {"what": "owns the number for outbound",
           "not_for": "sales ops analysts",
           "examples": ["VP Sales", "CRO"], "weights": ["VP first"]}


# ------------------------------------------------------------ the decision

def test_the_decision_is_taken_from_the_row_when_it_carries_one():
    assert card_mod.resolve_decision(PERSONA_ROW) == "persona_fit"


def test_a_verdict_names_its_decision_when_nothing_else_does():
    assert card_mod.resolve_decision({"verdict": "IN_ICP"}) == "icp_fit"
    assert card_mod.resolve_decision({"verdict": "NOT_PERSONA"}) == "persona_fit"


def test_the_packets_kind_is_the_last_rung_not_a_crash():
    assert card_mod.resolve_decision({}, {"kind": "company"}) == "icp_fit"
    assert card_mod.resolve_decision({}, {"kind": "person"}) == "persona_fit"
    assert card_mod.resolve_decision({}, {}) is None


def test_an_explicit_decision_beats_every_guess():
    assert card_mod.resolve_decision(PERSONA_ROW, decision="icp_fit") == "icp_fit"


# ------------------------------------------------------------- the battery

def test_the_battery_is_resolved_so_questions_print_in_their_own_words():
    questions = card_mod.battery_questions("persona_fit")
    ids = {q["id"] for q in questions}
    assert "title_matches_persona" in ids
    assert "evidence_insufficient" in ids          # the meta question travels too
    for q in questions:
        assert q.get("text")


def test_an_unknown_decision_returns_no_questions_rather_than_raising():
    assert card_mod.battery_questions(None) == []
    assert card_mod.battery_questions("not_a_decision") == []


# -------------------------------------------------- the RECORDED battery

def test_the_recorded_battery_version_is_read_off_the_row():
    assert card_mod.resolve_version(PERSONA_ROW) is None
    assert card_mod.resolve_version(dict(PERSONA_ROW,
                                         battery_version="v9")) == "v9"
    assert card_mod.resolve_version({"raw": {"battery_version": "v9"}}) == "v9"
    assert card_mod.resolve_version({}, "v2") == "v2"


def test_a_battery_version_this_harness_does_not_ship_gets_no_questions():
    """Substituting today's wording prints an operator a question the row was
    never asked."""
    assert card_mod.battery_questions("persona_fit", "v0-prehistoric") == []
    # and the version this harness DOES resolve still answers normally
    assert card_mod.battery_questions("persona_fit")
    questions, note = card_mod.resolve_questions("persona_fit", "v0-prehistoric")
    assert questions == []
    assert "historical question wording unavailable for battery v0-prehistoric" \
        in note.lower()


def test_a_historical_row_says_the_wording_is_gone_instead_of_substituting():
    row = dict(PERSONA_ROW, battery_version="v0-prehistoric")
    text = card_mod.card(row, packet=PERSON_PACKET, criteria=PERSONA)
    assert "battery v0-prehistoric" in text
    assert "historical question wording unavailable for battery v0-prehistoric" \
        in flat(text).lower()
    assert "NOT substituted" in text
    # today's battery text is nowhere on this card
    assert "Does a CURRENT role's title match" not in flat(text)
    # and it is still a card: the evidence, the absences and the answer remain
    assert "— PARTIAL" in text
    assert "the About section did not load" in flat(text)
    assert "0.91" in text


def test_a_row_with_no_recorded_version_still_gets_todays_questions():
    text = card_mod.card(PERSONA_ROW, packet=PERSON_PACKET, criteria=PERSONA)
    assert "Does a CURRENT role's title match" in flat(text)
    assert "unavailable for battery" not in text
    assert "· battery" not in text


# ---------------------------------------------------------------- the card

def test_a_card_from_a_packet_and_a_result_names_the_questions_and_the_evidence():
    text = card_mod.card(PERSONA_ROW, packet=PERSON_PACKET, criteria=PERSONA)
    assert text.startswith("Decision: persona_fit")
    assert "— PARTIAL" in text
    assert "Profile headline (their own words) [profile.headline]" in flat(text)
    assert "VP Sales at Northwind Systems" in flat(text)
    # the packet's own absence status and reason, never one the card invented
    assert "fetch_failed" in text
    assert "the About section did not load" in flat(text)
    # the persona block, as data
    assert "owns the number for outbound" in flat(text)
    # a question, in the battery's own words, with the fields it reads
    assert "Does a CURRENT role's title match" in flat(text)
    assert "reads: Current roles, with dates" in flat(text)
    # and the answer
    assert "0.91" in text and "Next: " in text


def test_a_log_row_alone_still_makes_a_card():
    text = card_mod.card(PERSONA_ROW)
    assert "the packet itself is not on this row" in flat(text)
    assert "Profile headline (their own words)" in flat(text)
    assert "profile.about or profile.about_text" in flat(text)


def test_a_card_never_raises():
    for junk in (None, {}, {"action": "review"}, "garbage", 7):
        assert "PACKET CARD" in card_mod.card(junk)


# ------------------------------------------------- what the judge read

def test_what_the_judge_read_names_the_fields_behind_the_top_answers():
    text = card_mod.what_the_judge_read(PERSONA_ROW)
    assert "title_matches_persona=0.91" in text
    assert "<- Current roles, with dates" in text
    assert len(text) <= card_mod.READ_CAP


def test_what_the_judge_read_is_capped_and_says_it_was_cut():
    row = dict(PERSONA_ROW, per_question={
        f"q{i}": {"type": "noul", "p": 0.9} for i in range(20)})
    text = card_mod.what_the_judge_read(row, limit=20)
    assert len(text) <= card_mod.READ_CAP
    assert text.endswith("…")


def test_a_row_with_no_answers_gets_an_empty_cell_not_a_zero():
    assert card_mod.what_the_judge_read({"per_question": {}}) == ""
    assert card_mod.what_the_judge_read({}) == ""


# ------------------------------------------------------------ the queue

def test_the_review_csv_carries_the_new_column_in_its_header(tmp_path):
    assert "what the judge read" in review_queue.HEADERS
    # and it sits with the evidence, before the two columns that are the human's
    assert review_queue.HEADERS[-2:] == ("YOUR DECISION", "note")
    out = review_queue.write_csv("r1", [PERSONA_ROW], path=tmp_path / "r1.csv")
    lines = out.read_text().splitlines()
    assert lines[0].split(",").index("what the judge read") == 5
    assert "title_matches_persona=0.91" in lines[1]


def test_every_row_carries_the_same_number_of_cells_as_the_header(tmp_path):
    import csv
    out = review_queue.write_csv("r1", [PERSONA_ROW], path=tmp_path / "r1.csv")
    rows = list(csv.reader(out.open()))
    assert all(len(r) == len(review_queue.HEADERS) for r in rows)


def test_the_cards_file_lands_beside_the_csv(tmp_path):
    csv_at = tmp_path / "r1.csv"
    path = review_queue.cards_path("r1", csv_at)
    assert path == tmp_path / "r1-cards.md"
    written = review_queue.write_cards("r1", [PERSONA_ROW], path=path)
    text = written.read_text()
    assert "PACKET CARD" in text
    assert "`p-1` — persona_fit" in text


def test_an_empty_queue_still_writes_a_cards_file(tmp_path):
    """A missing file is indistinguishable from a writer that never ran."""
    written = review_queue.write_cards("r1", [], path=tmp_path / "none-cards.md")
    assert written.exists()
    assert "Nothing to review." in written.read_text()
