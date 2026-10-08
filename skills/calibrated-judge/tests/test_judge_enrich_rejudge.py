"""Enrich, then re-judge — once, and only where it can help.

The house rule: when the packet is thin and the judge says "not sure", the
answer is more evidence, not a better prompt. So `judge()` takes an optional
enricher and, exactly once, goes back for a fuller packet and asks again.

The four boundaries this file holds:

  1. It fires only on a THIN or PARTIAL packet whose outcome was `review` or
     `insufficient`. A rich packet that came back unsure is not an evidence
     problem, and an accept is not a problem at all.
  2. It fires ONCE. One enrichment, one re-judge — never a loop.
  3. Both rounds are on the record: `round=1` and `round=2` rows in the
     decision log, and `enrichment_added` naming the fields the second packet
     carried that the first did not.
  4. A row that is still unsure NAMES what could not be found, because the
     human reading the review queue needs to know what to go get.

No network anywhere: the enricher is a stub, and the default enricher runs
against an offline page cache.
"""
from __future__ import annotations

import json

import pytest

from judge import config, core, packets, shadow
from judge import judge as run_judge

from test_judge_core import FakeClient, noul


THIN_EV = {"record": {"name": "Rivermark Family Dental"},
           "site": {"domain_registrable": "rivermarkdental.example"}}

RICH_EV = {
    "record": {"name": "Rivermark Family Dental"},
    "site": {"domain_registrable": "rivermarkdental.example",
             "homepage_text": "We are a general dental practice in Exampleton.",
             "about_text": "Dr Example opened the practice in 2009.",
             "self_description_sentences": "We are a general dental practice.",
             "matched_sentences": "general dentistry, cleanings, crowns"},
    "maps": {"primary_category": "Dentist",
             "secondary_categories": ["Dental clinic"]},
    "registry": {"specialty_label": "General Practice"},
    "entity": {"type_evidence_line": "an operating dental practice"},
}

ICP = {"inclusions": ["general dentistry"], "exclusions": ["orthodontics"]}


@pytest.fixture
def client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(core, "_client", lambda: c)
    return c


@pytest.fixture(autouse=True)
def isolated_outputs(monkeypatch, tmp_path):
    monkeypatch.setenv("CALIBRATED_JUDGE_OUT", str(tmp_path))
    monkeypatch.delenv("CALIBRATED_JUDGE_MODE", raising=False)


class Enricher:
    """Hands back a fuller packet the second time anyone asks."""

    def __init__(self, fuller=None):
        self.calls = []
        self.fuller = RICH_EV if fuller is None else fuller

    def __call__(self, evidence, decision=None):
        self.calls.append({"evidence": evidence, "decision": decision})
        return self.fuller


# ------------------------------------------------------------- when it fires

def test_a_thin_unsure_row_is_enriched_once_and_rejudged_once(client):
    client._answers = {}                      # nothing answered -> insufficient
    enrich = Enricher()
    out = run_judge("icp_fit", THIN_EV, icp=ICP, mode="shadow", enrich=enrich)
    assert len(enrich.calls) == 1
    assert len(client.calls) == 2
    assert out.round == 2
    assert out.packet_richness["tier"] == "rich"


def test_the_first_round_is_kept_whole_on_the_result(client):
    enrich = Enricher()
    out = run_judge("icp_fit", THIN_EV, icp=ICP, mode="shadow", enrich=enrich)
    rounds = out.raw["rounds"]
    assert len(rounds) == 1
    assert rounds[0]["round"] == 1
    assert rounds[0]["packet_richness"]["tier"] == "thin"


def test_the_added_fields_are_named(client):
    enrich = Enricher()
    out = run_judge("icp_fit", THIN_EV, icp=ICP, mode="shadow", enrich=enrich)
    added = set(out.enrichment_added)
    assert "site.homepage_text" in added
    assert "maps.primary_category" in added
    assert "site.domain_registrable" not in added      # it was already there


def test_a_rich_packet_is_never_enriched(client):
    client._answers = {}
    enrich = Enricher()
    out = run_judge("icp_fit", RICH_EV, icp=ICP, mode="shadow", enrich=enrich)
    assert enrich.calls == []
    assert len(client.calls) == 1
    assert out.round == 1


def test_an_accept_is_never_enriched(client):
    """Thin, but it answered. More evidence is the fix for "not sure", and an
    accept is not that."""
    client._answers = {"self_description_matches_icp": noul(0.99)}
    thin_but_answerable = {"record": {"name": "Rivermark Family Dental"},
                           "site": {"self_description_sentences":
                                    "We are a general dental practice."}}
    enrich = Enricher()
    out = run_judge("icp_fit", thin_but_answerable, icp=ICP, mode="shadow",
                    enrich=enrich)
    assert out.action == "accept"
    assert packets.richness(thin_but_answerable)["tier"] == "thin"
    assert enrich.calls == []


def test_no_enricher_means_exactly_what_it_did_before(client):
    out = run_judge("icp_fit", THIN_EV, icp=ICP, mode="shadow")
    assert len(client.calls) == 1
    assert out.round == 1
    assert out.enrichment_added == []


def test_it_never_loops(client):
    """The second packet is still thin — and that is the end of it."""
    enrich = Enricher(fuller={"record": {"name": "Rivermark Family Dental"},
                              "site": {"domain_registrable": "rivermarkdental.example"}})
    out = run_judge("icp_fit", THIN_EV, icp=ICP, mode="shadow", enrich=enrich)
    assert len(enrich.calls) == 1
    assert len(client.calls) == 2
    assert out.round == 2


# ------------------------------------------------------- what the human reads

def test_a_still_unsure_row_names_what_could_not_be_found(client):
    enrich = Enricher(fuller=THIN_EV)
    out = run_judge("icp_fit", THIN_EV, icp=ICP, mode="shadow", enrich=enrich)
    assert out.action in ("review", "insufficient")
    named = " ".join(out.review_reasons)
    assert "still_missing" in named
    assert "site.homepage_text" in named


def test_an_enricher_that_explodes_leaves_the_first_answer_standing(client):
    def boom(evidence, decision=None):
        raise RuntimeError("the page cache is on fire")
    out = run_judge("icp_fit", THIN_EV, icp=ICP, mode="shadow", enrich=boom)
    assert out.error is None
    assert out.round == 1
    assert len(client.calls) == 1


def test_an_enricher_that_finds_nothing_returns_none_safely(client):
    out = run_judge("icp_fit", THIN_EV, icp=ICP, mode="shadow",
                    enrich=lambda ev, decision=None: None)
    assert out.round == 1
    assert len(client.calls) == 1


# ------------------------------------------------------------------ the log

def test_the_seam_logs_both_rounds(client, monkeypatch, tmp_path):
    enrich = Enricher()
    shadow.observe("icp_fit", THIN_EV, incumbent_verdict="IN_ICP",
                   skill="tam-qa", hook_point="post_verdict",
                   entity_key="row9", icp=ICP, mode="shadow", enrich=enrich)
    lines = [json.loads(line)
             for p in (tmp_path / "decisions").glob("*.jsonl")
             for line in p.read_text().splitlines() if line.strip()]
    assert [r["round"] for r in lines] == [1, 2]
    assert lines[0]["packet_richness"]["tier"] == "thin"
    assert lines[1]["packet_richness"]["tier"] == "rich"
    assert lines[1]["enrichment_added"]


def test_every_logged_row_carries_a_richness_even_without_enrichment(client, tmp_path):
    shadow.observe("icp_fit", RICH_EV, incumbent_verdict="IN_ICP",
                   skill="tam-qa", hook_point="post_verdict",
                   entity_key="row9", icp=ICP, mode="shadow")
    rows = [json.loads(line) for p in (tmp_path / "decisions").glob("*.jsonl")
            for line in p.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["packet_richness"]["tier"] == "rich"
    assert rows[0]["round"] == 1


# ------------------------------------------------------- the default enricher

def test_the_default_enricher_builds_a_company_packet_offline():
    from test_judge_packets import ANCHOR, StubCache, _cache

    enrich = packets.default_enricher(page_cache=_cache(), maps={"category": "Dentist"})
    fuller = enrich({"record": {"name": ANCHOR["name"]},
                     "anchor": ANCHOR}, "icp_fit")
    assert fuller is not None
    assert packets.richness(fuller)["tier"] in ("partial", "rich")


def test_the_default_enricher_touches_no_network_when_it_has_no_cache():
    enrich = packets.default_enricher()
    out = enrich({"record": {"name": "Nobody"}}, "icp_fit")
    assert out is None or packets.richness(out)["tier"] == "thin"
