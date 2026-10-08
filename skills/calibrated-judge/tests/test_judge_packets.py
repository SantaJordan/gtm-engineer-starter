"""Contract tests for the rich evidence packet builder, read through the shim.

The packet is the state the evidence-packet contract specifies (it and its
builder live in crawford since 2026-09-19; `judge.packets` is a shim): named
fields, hard character caps, a provenance and an observation date on every
value, and — the rule the whole file exists for — **a missing field is ABSENT,
never an empty string**. A blank that reads as a value is how a quick fit check
says `fits: true` about a page it never fetched.

No network: the page cache is a stub, Maps and the registry arrive as data.
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

ANCHOR = {
    "entity_key": "row9",
    "name": "Rivermark Family Dental",
    "city": "Exampleton",
    "state": "NC",
    "phone": "5555550134",
    "domain": "rivermarkdental.example",
}


class StubCache:
    """Duck-types crawford's PageCache: get_or_fetch_page(url) -> (rec, html)."""

    def __init__(self, pages, *, offline=False):
        self.pages = dict(pages)
        self.offline = offline
        self.calls = []

    def get_or_fetch_page(self, url):
        self.calls.append(url)
        hit = self.pages.get(url)
        if hit is None:
            if self.offline:
                return ({"domain": url, "status": None, "error": "offline_cache_miss",
                         "fetched_at": "", "final_url": url}, "")
            return ({"domain": url, "status": 404, "final_url": url,
                     "fetched_at": "2026-09-18T00:00:00+00:00", "redirect_chain": []}, "")
        rec, html = hit
        return dict(rec), html


def _home_rec(**over):
    rec = {"domain": "rivermarkdental.example", "status": 200,
           "final_url": "https://rivermarkdental.example/",
           "fetched_at": "2026-09-18T12:00:00+00:00", "redirect_chain": []}
    rec.update(over)
    return rec


def _cache(extra=None, **kw):
    pages = {"https://rivermarkdental.example/": (_home_rec(), HOMEPAGE)}
    pages.update(extra or {})
    return StubCache(pages, **kw)


# ------------------------------------------------------------------ shape

def test_company_packet_has_the_named_blocks_and_version():
    p = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=None, registry=None)
    assert p["kind"] == "company"
    assert p["packet_version"] == packets.PACKET_VERSION
    assert p["entity_key"] == "row9"
    for block in ("record", "site", "maps", "registry", "absent"):
        assert block in p, block
    assert p["record"]["name"]["value"] == "Rivermark Family Dental"


def test_every_present_field_carries_source_and_observed_at():
    p = packets.build_company_packet(
        ANCHOR, page_cache=_cache(),
        maps={"category": "Dentist", "subtypes": ["Dental clinic"],
              "business_status": "OPERATIONAL", "street_address": "123 Example St",
              "phone": "+1 (555)555-0134", "review_count": 214,
              "_observed_at": "2026-09-18T12:00:00+00:00"},
        registry=NPPES)
    for block in ("record", "site", "maps", "registry"):
        for name, fld in p[block].items():
            assert isinstance(fld, dict), f"{block}.{name} is not a field object"
            assert fld.get("source"), f"{block}.{name} has no source"
            assert fld.get("observed_at"), f"{block}.{name} has no observed_at"
            assert fld.get("status") in packets.FIELD_STATUSES
            assert fld["value"] not in ("", [], None), f"{block}.{name} is a silent blank"


def test_missing_field_is_absent_never_empty_string():
    p = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=None, registry=None)
    assert p["maps"] == {}
    assert p["registry"] == {}
    assert "maps.primary_category" in p["absent"]
    assert "registry.taxonomy_code" in p["absent"]
    for key, rec in p["absent"].items():
        assert rec["status"] in packets.ABSENT_STATUSES, (key, rec)
        assert rec.get("reason")


def test_fetch_failure_is_fetch_failed_not_absent():
    """A request that WAS made and came back wrong."""
    cache = StubCache({})                      # a real 404, not a replay miss
    p = packets.build_company_packet(ANCHOR, page_cache=cache, maps=None, registry=None)
    assert p["absent"]["site.homepage_text"]["status"] == "fetch_failed"
    assert p["site"].get("homepage_text") is None


def test_an_offline_miss_is_not_fetched_not_fetch_failed():
    """A request nobody made. Corrected 2026-09-18: this case used to be
    recorded as `fetch_failed`, which reads as evidence about the SITE when it
    is only evidence about the run — and a site that reads as dead is dropped
    by every arm."""
    cache = StubCache({}, offline=True)
    p = packets.build_company_packet(ANCHOR, page_cache=cache, maps=None, registry=None)
    assert p["absent"]["site.homepage_text"]["status"] == "not_fetched"
    assert p["site"].get("homepage_text") is None


def test_no_page_cache_means_no_fetch_and_a_stated_absence():
    p = packets.build_company_packet(ANCHOR, page_cache=None, maps=None, registry=None)
    assert p["absent"]["site.homepage_text"]["status"] == "absent"
    assert p["record"]["name"]["value"] == "Rivermark Family Dental"


# ------------------------------------------------------------------ caps

def test_caps_are_enforced_in_code_and_truncation_is_declared():
    big = HOMEPAGE.replace("</main>", "<p>" + ("filler sentence. " * 2000) + "</p></main>")
    p = packets.build_company_packet(
        ANCHOR, page_cache=_cache({"https://rivermarkdental.example/": (_home_rec(), big)}),
        maps=None, registry=None)
    home = p["site"]["homepage_text"]
    assert len(home["value"]) <= packets.CAPS["site.homepage_text"] == 6000
    assert home["status"] == "truncated"
    assert home["full_chars"] > len(home["value"])


def test_no_packet_field_exceeds_its_declared_cap():
    p = packets.build_company_packet(
        ANCHOR, page_cache=_cache(), maps=None, registry=None)
    for block in ("record", "site", "maps", "registry"):
        for name, fld in p[block].items():
            cap = packets.CAPS.get(f"{block}.{name}")
            if cap and isinstance(fld["value"], str):
                assert len(fld["value"]) <= cap, f"{block}.{name} over cap"


# ------------------------------------------------- sub-page discovery

def test_subpage_discovery_matches_the_named_labels_only():
    found = packets.discover_subpages(HOMEPAGE, "https://rivermarkdental.example/")
    kinds = {f["kind"] for f in found}
    assert kinds <= {"about", "services", "contact"}
    urls = [f["url"] for f in found]
    assert "https://rivermarkdental.example/about-us/" in urls
    assert "https://rivermarkdental.example/services/" in urls
    assert "https://rivermarkdental.example/contact/" in urls
    assert not any("/blog" in u for u in urls)


def test_subpage_discovery_stays_on_the_registrable_domain():
    found = packets.discover_subpages(HOMEPAGE, "https://rivermarkdental.example/")
    assert not any("facebook.com" in f["url"] or "directory.example.org" in f["url"]
                   for f in found)


def test_subpage_discovery_is_capped_at_five_fetches():
    found = packets.discover_subpages(HOMEPAGE, "https://rivermarkdental.example/")
    assert len(found) <= packets.MAX_SUBPAGE_FETCHES == 5


def test_subpages_are_fetched_through_the_cache_and_fill_named_fields():
    about = ({"domain": "about", "status": 200, "final_url": "https://rivermarkdental.example/about-us/",
              "fetched_at": "2026-09-18T12:01:00+00:00"},
             "<html><body><p>Founded in 2004 by Dr. Nunez, we remain independently owned.</p></body></html>")
    svc = ({"domain": "svc", "status": 200, "final_url": "https://rivermarkdental.example/services/",
            "fetched_at": "2026-09-18T12:02:00+00:00"},
           "<html><body><p>Cleanings, crowns, whitening.</p></body></html>")
    cache = _cache({"https://rivermarkdental.example/about-us/": about,
                    "https://rivermarkdental.example/services/": svc})
    p = packets.build_company_packet(ANCHOR, page_cache=cache, maps=None, registry=None)
    assert "independently owned" in p["site"]["about_text"]["value"]
    assert p["site"]["about_text"]["source"].startswith("https://rivermarkdental.example/about-us/")
    assert "Cleanings" in p["site"]["services_text"]["value"]
    assert p["fetches"], "sub-page fetches are recorded"
    # homepage + at most five sub-pages
    assert len(cache.calls) <= 1 + packets.MAX_SUBPAGE_FETCHES


def test_subpages_are_not_fetched_when_the_caller_says_no():
    cache = _cache()
    packets.build_company_packet(ANCHOR, page_cache=cache, maps=None, registry=None,
                                 fetch_subpages=False)
    assert cache.calls == ["https://rivermarkdental.example/"]


# ------------------------------------------------------ page-derived fields

def test_identity_fields_are_lifted_from_the_homepage():
    p = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=None, registry=None)
    site = p["site"]
    assert site["og_site_name"]["value"] == "Rivermark Family Dental"
    assert site["schema_org_name"]["value"] == "Rivermark Family Dental PLLC"
    assert "Rivermark Family Dental PLLC" in site["legal_entity_footer"]["value"]
    assert "not affiliated" in site["ownership_disclosure_text"]["value"].lower()
    assert "About Us" in site["nav_labels"]["value"]
    assert site["http_status"]["value"] == 200
    assert site["final_url"]["value"] == "https://rivermarkdental.example/"


def test_redirect_terminus_is_the_last_hop_when_there_is_one():
    rec = _home_rec(redirect_chain=["https://rivermarkdental.example/",
                                    "https://www.rivermarkdental.example/"],
                    final_url="https://www.rivermarkdental.example/")
    p = packets.build_company_packet(
        ANCHOR, page_cache=_cache({"https://rivermarkdental.example/": (rec, HOMEPAGE)}),
        maps=None, registry=None)
    assert p["site"]["redirect_terminus"]["value"] == "https://www.rivermarkdental.example/"


# ------------------------------------------------------------------ maps

def test_maps_block_is_named_not_raw():
    maps = {"category": "Dentist", "subtypes": ["Dental clinic", "Orthodontist"],
            "business_status": "OPERATIONAL", "street_address": "123 Example St",
            "city": "Exampleton", "state": "NC", "phone": "+1 (555)555-0134",
            "review_count": 214, "_observed_at": "2026-09-18T12:00:00+00:00"}
    p = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=maps, registry=None)
    m = p["maps"]
    assert m["primary_category"]["value"] == "Dentist"
    assert m["secondary_categories"]["value"] == ["Dental clinic", "Orthodontist"]
    assert m["business_status"]["value"] == "OPERATIONAL"
    assert m["business_status_as_of"]["value"] == "2026-09-18T12:00:00+00:00"
    assert m["rating_count"]["value"] == 214
    assert "123 Example St" in m["address"]["value"]


def test_zero_reviews_is_a_value_not_an_absence():
    maps = {"category": "Dentist", "review_count": 0,
            "_observed_at": "2026-09-18T12:00:00+00:00"}
    p = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=maps, registry=None)
    assert p["maps"]["rating_count"]["value"] == 0
    assert "maps.rating_count" not in p["absent"]


# --------------------------------------------------------------- registry

def test_nppes_parsing_names_the_taxonomy_and_the_parent():
    reg = packets.parse_nppes(NPPES, observed_at="2026-09-18T12:00:00+00:00")
    assert reg["source"] == "nppes"
    assert reg["entity_type"] == "organization"
    assert reg["status"] == "A"
    assert reg["taxonomy_code"] == "1223G0001X"
    assert reg["taxonomy_desc"] == "Dentist - General Practice"
    assert reg["parent_org_lbn"] == "HARBORLINE DENTAL PARTNERS LLC"
    assert reg["as_of"] == "2026-03-09"


def test_nppes_empty_result_is_none_not_a_blank_record():
    assert packets.parse_nppes({"result_count": 0, "results": []}) is None
    assert packets.parse_nppes({}) is None
    assert packets.parse_nppes(None) is None


def test_registry_block_rides_the_packet():
    p = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=None, registry=NPPES)
    r = p["registry"]
    assert r["source"]["value"] == "nppes"
    assert r["taxonomy_code"]["value"] == "1223G0001X"
    assert r["parent_org_lbn"]["value"] == "HARBORLINE DENTAL PARTNERS LLC"
    assert r["taxonomy_code"]["observed_at"]


def test_a_non_healthcare_registry_seam_is_recorded_not_invented():
    p = packets.build_company_packet(
        ANCHOR, page_cache=_cache(), maps=None,
        registry={"source": "nc_sos", "status": "Current-Active",
                  "entity_type": "organization", "as_of": "2026-08-01"})
    assert p["registry"]["source"]["value"] == "nc_sos"
    assert "registry.taxonomy_code" in p["absent"]


# ----------------------------------------------------------------- person

PROFILE = {
    "full_name": "Dana Reyes",
    "headline": "Retired from practice. Enjoying the slower pace.",
    "about": "I sold my practice and now consult two days a month.",
    "location": "Exampleton, North Carolina",
    "open_to_work": False,
    "licenses": ["NC Dental License D-00001"],
    "scrape_as_of": "2026-09-18T12:00:00+00:00",
    "experiences": [
        {"title": "Owner / Operator", "company": "Rivermark Family Dental",
         "company_linkedin_url": "https://linkedin.example/company/rivermark",
         "start_date": "Jan 2018", "end_date": None, "is_current": True},
        {"title": "Associate Dentist", "company": "Harborline Family Dental",
         "company_linkedin_url": "", "start_date": "2009", "end_date": "2017",
         "is_current": False},
    ],
}


def test_person_packet_keeps_positions_unflattened_and_verbatim():
    company = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=None, registry=None)
    p = packets.build_person_packet({"entity_key": "row9|Dana Reyes", "name": "Dana Reyes"},
                                    profile=PROFILE, company_packet=company)
    assert p["kind"] == "person"
    exps = p["profile"]["current_experiences"]["value"]
    assert isinstance(exps, list) and len(exps) == 1
    first = exps[0]
    assert first["title"] == "Owner / Operator"
    assert first["company"] == "Rivermark Family Dental"
    assert first["company_url"] == "https://linkedin.example/company/rivermark"
    assert "Jan 2018" in first["dates_text"]
    # the provider's claim travels under the provider's name, never as truth
    assert first["provider_current"] is True
    assert "is_current" not in first


def test_person_packet_carries_headline_about_licences_and_prior_role():
    company = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=None, registry=None)
    p = packets.build_person_packet({"entity_key": "k", "name": "Dana Reyes"},
                                    profile=PROFILE, company_packet=company)
    prof = p["profile"]
    assert prof["headline"]["value"].startswith("Retired from practice")
    assert "sold my practice" in prof["about"]["value"]
    assert prof["location"]["value"] == "Exampleton, North Carolina"
    assert prof["licenses"]["value"] == ["NC Dental License D-00001"]
    assert prof["open_to_work"]["value"] is False
    assert prof["scrape_as_of"]["value"] == "2026-09-18T12:00:00+00:00"
    assert "Associate Dentist" in prof["prior_role"]["value"]


def test_person_packet_inherits_the_company_identity_block():
    company = packets.build_company_packet(ANCHOR, page_cache=_cache(), maps=None, registry=None)
    p = packets.build_person_packet({"entity_key": "k", "name": "Dana Reyes"},
                                    profile=PROFILE, company_packet=company)
    assert p["company_identity"]["name"]["value"] == "Rivermark Family Dental"
    assert p["company_identity"]["domain"]["value"] == "rivermarkdental.example"
    assert "homepage_text" not in p["company_identity"]


def test_person_packet_without_a_profile_states_the_absence():
    p = packets.build_person_packet({"entity_key": "k", "name": "Dana Reyes"},
                                    profile=None, company_packet=None)
    assert p["profile"] == {}
    assert p["absent"]["profile.headline"]["status"] == "absent"
    assert p["record"]["name"]["value"] == "Dana Reyes"


def test_person_about_is_capped():
    prof = dict(PROFILE, about="x" * 9000)
    p = packets.build_person_packet({"entity_key": "k", "name": "Dana Reyes"},
                                    profile=prof, company_packet=None)
    fld = p["profile"]["about"]
    assert len(fld["value"]) == packets.CAPS["profile.about"] == 3000
    assert fld["status"] == "truncated"


def test_packets_are_json_serialisable():
    company = packets.build_company_packet(
        ANCHOR, page_cache=_cache(),
        maps={"category": "Dentist", "_observed_at": "2026-09-18T12:00:00+00:00"},
        registry=NPPES)
    person = packets.build_person_packet({"entity_key": "k", "name": "Dana Reyes"},
                                         profile=PROFILE, company_packet=company)
    json.dumps(company, sort_keys=True)
    json.dumps(person, sort_keys=True)
