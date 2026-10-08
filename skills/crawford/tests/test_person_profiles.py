"""Crawford's own profile client — the three places it could lie quietly.

  1. **A refused request is not an answer.** An injected transport that comes
     back 503 must be treated the way the real `urlopen` treats it — a failure
     to retry — and never parsed as a body that says "no such person". The two
     states cost different things and mean different things.
  2. **The last attempt does not sleep.** A back-off after the final try buys
     nothing and holds the run for seconds per dead URL.
  3. **A database read is not a scrape.** `/v2/enrichment/person` returns a
     stored record; stamping the build time on it as `scrape_as_of` makes an
     unknown-age profile read as observed today. The retrieval time is ours to
     assert; the record's age is the vendor's, and when the vendor does not
     say, the packet says it does not know.

Every person and employer below is invented.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from lib import person_profiles as pp                       # noqa: E402

URL = "https://linkedin.example/in/dana-reyes"
PERSON = {"headline": "Owner at Northgate Dental Care",
          "about_me": "I have run a two-chair practice for twenty years.",
          "location": {"city": "Springfield", "state_code": "IL"},
          "experiences": [{"job_title": "Owner",
                           "company_name": "Northgate Dental Care",
                           "job_start_date": "2004-06", "job_end_date": None,
                           "job_is_current": True}]}


def _client(http_post, **kw):
    """A client with no key — the injected transport is the whole network."""
    return pp.BlitzPersonClient(http_post=http_post, rate=0, **kw)


# ------------------------------------------------------------- the transport

def test_an_error_status_is_a_transport_failure_not_an_answer():
    """A 503 body is a gateway page, not a verdict about this person."""
    seen = []

    def http_post(url, body, headers, timeout):
        seen.append(url)
        return 503, b'{"found": false, "person": {}}'

    out = _client(http_post, tries=2).fetch(URL)
    assert out["found"] is False
    assert out["error"], "a refused request must travel as an error"
    assert out["status"] != 503 or out["error"], "503 is not a 'no such person'"
    assert len(seen) == 2, "a 503 is retryable; it was treated as an answer"


def test_a_404_from_an_injected_transport_is_still_an_answer():
    """The vendor saying 'no record' is an answer, and is not retried."""
    seen = []

    def http_post(url, body, headers, timeout):
        seen.append(url)
        return 404, b'{"detail": "not found"}'

    out = _client(http_post, tries=3).fetch(URL)
    assert out["found"] is False
    assert out["status"] == 404
    assert "error" not in out
    assert len(seen) == 1


def test_the_final_attempt_does_not_sleep_before_giving_up(monkeypatch):
    slept = []
    monkeypatch.setattr(pp.time, "sleep", lambda s: slept.append(s))

    def http_post(url, body, headers, timeout):
        raise TimeoutError("connection timed out")

    out = _client(http_post, tries=3).fetch(URL)
    assert out["status"] == -1 and out["error"] == "TimeoutError"
    assert len(slept) == 2, ("a back-off after the LAST attempt holds the run "
                             "for nothing")


# ------------------------------------------------------------ the record age

def test_to_profile_says_it_does_not_know_the_records_age():
    record = {"url": URL, "found": True, "person": dict(PERSON), "status": 200}
    profile = pp.to_profile(record)
    assert profile["retrieved_at"], "when WE read it is ours to assert"
    assert profile["scrape_as_of"] is None, (
        "the build time is not the date this record was collected")
    assert profile["as_of_status"] == "vendor_record_age_unknown"


def test_to_profile_carries_the_vendors_own_last_updated_when_it_has_one():
    person = dict(PERSON, last_updated="2026-04-01")
    profile = pp.to_profile({"url": URL, "found": True, "person": person,
                             "status": 200})
    assert profile["scrape_as_of"] == "2026-04-01"
    assert profile.get("as_of_status") in (None, "vendor_record_last_updated")
    assert profile["retrieved_at"] != "2026-04-01"


def test_the_packet_does_not_stamp_an_unknown_age_as_an_observation():
    from lib import packets as pk

    profile = pp.to_profile({"url": URL, "found": True,
                             "person": dict(PERSON), "status": 200})
    packet = pk.build_person_packet({"entity_key": "p-1", "name": "Dana Reyes"},
                                    profile=profile)
    assert "scrape_as_of" not in packet["profile"]
    absent = packet["absent"]["profile.scrape_as_of"]
    assert absent["status"] == "absent"
    assert "vendor_record_age_unknown" in absent["reason"]
    # the fields themselves still carry OUR retrieval time
    assert packet["profile"]["headline"]["observed_at"] == profile["retrieved_at"]


def test_a_profile_that_states_its_age_is_stamped_with_it():
    from lib import packets as pk

    profile = pp.to_profile({"url": URL, "found": True,
                             "person": dict(PERSON, last_updated="2026-04-01"),
                             "status": 200})
    packet = pk.build_person_packet({"entity_key": "p-1", "name": "Dana Reyes"},
                                    profile=profile)
    assert packet["profile"]["scrape_as_of"]["value"] == "2026-04-01"
    assert "profile.scrape_as_of" not in packet["absent"]


def test_a_client_with_no_key_and_no_transport_refuses(monkeypatch):
    monkeypatch.delenv(pp.ENV_KEY_NAME, raising=False)
    with pytest.raises(RuntimeError):
        pp.BlitzPersonClient()
