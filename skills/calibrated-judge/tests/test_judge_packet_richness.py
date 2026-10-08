"""Packet richness, the public-suffix rule, and the offline absence status.

Three claims, all of them about the same thing — a packet has to be able to
say how much evidence it actually carries, because every downstream rule
(enrich-then-rejudge, the review row a human reads) is a function of that:

  1. `richness(packet)` -> `{tier, present_fields, missing_required}` computed
     from the CONTRACT fields that are present, never from a character count.
  2. `registrable_domain` treats a multi-level public suffix conservatively:
     when the tail is a known one, or a pair of two-letter labels, it keeps
     three labels rather than gluing two different sites together.
  3. An offline cache MISS is `not_fetched`, never `fetch_failed`. A page we
     never asked for did not fail; calling it a failure is how "no evidence"
     becomes "the site is dead".
"""
from __future__ import annotations

import json
import pathlib

import pytest

from judge import packets

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures" / "packets"
HOMEPAGE = (FIXTURES / "homepage.html").read_text()
NPPES = json.loads((FIXTURES / "nppes-organization.json").read_text())

from test_judge_packets import ANCHOR, StubCache, _cache, _home_rec  # noqa: E402


# ----------------------------------------------------------- registrable_domain

@pytest.mark.parametrize("host,expected", [
    ("https://www.example.com/about", "example.com"),
    ("deep.sub.example.com", "example.com"),
    ("example.com", "example.com"),
    ("https://shop.example.co.uk/x", "example.co.uk"),
    ("www.example.com.au", "example.com.au"),
    # a two-letter pair nobody hardcoded: co.id is a public suffix too
    ("www.example.co.id", "example.co.id"),
    # us locality style: exampletown.k12.ca.us is the registrable name, and two
    # districts under k12.ca.us must not read as one site
    ("www.exampletown.k12.ca.us", "exampletown.k12.ca.us"),
    ("example.ny.us", "example.ny.us"),
])
def test_registrable_domain_is_conservative_about_public_suffixes(host, expected):
    assert packets.registrable_domain(host) == expected


def test_two_districts_under_one_locality_suffix_are_different_sites():
    a = packets.registrable_domain("https://exampletown.k12.ca.us/")
    b = packets.registrable_domain("https://sampleville.k12.ca.us/")
    assert a != b


# ------------------------------------------------------------- offline absence

def test_an_offline_cache_miss_is_not_fetched_not_a_failure():
    cache = StubCache({}, offline=True)
    packet = packets.build_company_packet(ANCHOR, page_cache=cache)
    absent = packet["absent"]
    assert absent["site.homepage_text"]["status"] == "not_fetched"
    assert absent["site.http_status"]["status"] == "not_fetched"
    statuses = {v["status"] for v in absent.values()}
    assert "fetch_failed" not in statuses
    assert "not_fetched" in packets.ABSENT_STATUSES


def test_a_real_http_failure_is_still_fetch_failed():
    cache = StubCache({})            # not offline: a 404 is a real answer
    packet = packets.build_company_packet(ANCHOR, page_cache=cache)
    assert packet["absent"]["site.homepage_text"]["status"] == "fetch_failed"


# -------------------------------------------------------------------- richness

def _company(**kw):
    return packets.build_company_packet(ANCHOR, **kw)


def test_a_company_packet_with_page_about_and_registry_is_rich():
    about = ("https://rivermarkdental.example/about-us/",
             (_home_rec(final_url="https://rivermarkdental.example/about-us/"),
              "<html><body><p>" + "We are a family dental practice. " * 20
              + "</p></body></html>"))
    packet = _company(page_cache=_cache(dict([about])), registry=NPPES)
    r = packets.richness(packet)
    assert r["tier"] == "rich"
    assert "site.homepage_text" in r["present_fields"]
    assert r["missing_required"] == []


def test_a_company_packet_with_only_a_homepage_is_partial():
    packet = _company(page_cache=_cache(), fetch_subpages=False)
    r = packets.richness(packet)
    assert r["tier"] == "partial"
    assert any("about" in m for m in r["missing_required"])


def test_a_company_packet_with_no_page_text_at_all_is_thin():
    packet = _company(page_cache=StubCache({}, offline=True))
    r = packets.richness(packet)
    assert r["tier"] == "thin"
    assert "site.homepage_text" in " ".join(r["missing_required"])


def test_a_person_packet_needs_headline_about_dates_and_location_to_be_rich():
    profile = {
        "headline": "Owner at Rivermark Family Dental",
        "about": "I have run a family practice in Exampleton since 2009.",
        "location": "Exampleton, North Carolina",
        "experiences": [{"title": "Owner", "company": "Rivermark Family Dental",
                         "is_current": True, "start_date": "2009"}],
    }
    packet = packets.build_person_packet({"entity_key": "p1", "name": "A B"},
                                         profile=profile)
    assert packets.richness(packet)["tier"] == "rich"

    thin_profile = dict(profile, about="", location="")
    thin = packets.build_person_packet({"entity_key": "p1", "name": "A B"},
                                       profile=thin_profile)
    r = packets.richness(thin)
    assert r["tier"] == "partial"
    assert any("about" in m for m in r["missing_required"])


def test_richness_reads_a_flat_evidence_dict_too():
    """Not every caller hands in a built packet; a dotted evidence dict is the
    other shape in production and must not crash or read as rich."""
    r = packets.richness({"site.homepage_text": "we fix roofs",
                          "record.name": "Acme Roofing"})
    assert r["tier"] == "thin"
    assert "site.homepage_text" in r["present_fields"]


def test_richness_of_nothing_is_thin_not_an_exception():
    for value in (None, {}, [], "nonsense"):
        assert packets.richness(value)["tier"] == "thin"
