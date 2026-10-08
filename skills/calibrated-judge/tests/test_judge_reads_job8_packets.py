"""The judge reads a Crawford Job-8 packet file unchanged.

Crawford builds packets (`skills/crawford/scripts/build_packets.py`)
and this skill judges them. That hand-off is a contract with exactly one shape
to get wrong: the file carries the contract packet under `packet`, with
Crawford's own bookkeeping (richness, rounds, lanes) beside it, and
`judge(decision, doc["packet"])` must work on that block with nothing renamed,
unwrapped or flattened in between.

So this test does the hand-off for real, offline: it loads a file a Job-8 run
actually wrote and asserts the two things the judge does to a packet before any
model is involved — `packets.richness()` tags it, and the battery narrows to
the questions the packet's fields support. No backend is called; `judge()`
itself is not invoked, because a test that needs a key is a test that does not
run.

The fixtures are real Job-8 output over a SYNTHETIC page cache. Every entity
and person in them is invented.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from judge import core, packets

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures" / "packets"
COMPANY = json.loads((FIXTURES / "job8-company.json").read_text())
PERSON = json.loads((FIXTURES / "job8-person.json").read_text())

ICP = {"what": "independently owned dental practices with their own website",
       "not_for": ["dental service organizations", "practice brokers"],
       "examples": ["a two-chair family practice"]}
PERSONA = {"what": "the owner-dentist who signs for software",
           "not_for": ["retired owners", "practice brokers"],
           "examples": ["Owner", "Practice Owner", "Managing Dentist"]}


def test_the_file_carries_the_packet_under_a_packet_key():
    for doc in (COMPANY, PERSON):
        assert "packet" in doc, "Job 8 writes the contract packet under `packet`"
        assert doc["packet"]["packet_version"] == packets.PACKET_VERSION
        # the run's own bookkeeping sits BESIDE the packet, never inside it
        assert "richness" in doc and "rounds" in doc
        assert "richness" not in doc["packet"] and "rounds" not in doc["packet"]


@pytest.mark.parametrize("doc,kind", [(COMPANY, "company"), (PERSON, "person")])
def test_richness_reads_a_job8_packet_block_unchanged(doc, kind):
    tier = packets.richness(doc["packet"])
    assert tier["kind"] == kind
    # the tier Crawford recorded and the tier this skill computes are the same
    # number from the same function — that is the whole point of one module
    assert tier["tier"] == doc["richness"]["tier"]
    assert set(tier["present_fields"]) == set(doc["richness"]["present_fields"])
    assert tier["tier"] in packets.RICHNESS_TIERS
    assert not tier["missing_required"], "these fixtures are the rich case"


@pytest.mark.parametrize("doc,decision,criteria", [
    (COMPANY, "icp_fit", ICP),
    (PERSON, "persona_fit", PERSONA),
])
def test_the_battery_narrows_on_a_job8_packet(doc, decision, criteria):
    battery, version = core.resolve_battery(decision)
    state, facts = core.build_state(decision, doc["packet"], criteria)
    questions = core.applicable_questions(decision, battery, facts, version)
    assert questions, "a rich packet must leave the judge something to ask"
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids)), "a question asked twice is counted twice"
    assert state, "the packet has to render to a state the reader can read"


def test_an_absent_field_is_never_read_as_a_value():
    """The packet's absences travel; a reader must not see them as blanks."""
    absent = PERSON["packet"].get("absent") or {}
    assert absent, "a real packet records what it could not find"
    for key, entry in absent.items():
        assert entry["status"] in packets.ABSENT_STATUSES
        assert entry["reason"]
        block, _, name = key.partition(".")
        assert name not in (PERSON["packet"].get(block) or {}), (
            f"{key} is recorded absent AND present — one of them is a lie")
