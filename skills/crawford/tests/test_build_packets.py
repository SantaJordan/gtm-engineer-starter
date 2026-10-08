"""Job 8 — evidence packets for a list. What is guarded here:

  1. **Intake is honest.** Both CSV kinds load with only the required column;
     a row that cannot become a packet is COUNTED and reported, never dropped
     in silence and never packeted as a blank.
  2. **Every lane is refusable and every lane is injectable.** Maps is not
     called without a budget, the profile lane is not called without the flag,
     and each one runs against a fake here — no test in this file may touch
     the network, and `test_the_builder_never_reaches_the_network` proves it
     by making a fetch raise.
  3. **Richness is the shared function's answer**, not a local rule: the tier
     on disk is exactly `lib.packets.richness(packet)["tier"]`.
  4. **A packet that says nothing says WHY.** A row with no LinkedIn URL gets
     `no_profile_url` in the packet's own absence reasons, not an empty
     profile block that reads as a person with no headline.
  5. **Enrich runs once.** A thin or partial row gets one more pass with the
     caps raised, both rounds are on the record, and there is never a third.
  6. **Resume, limit, and the ledger.** A rerun skips what is already indexed,
     `--limit` is a ceiling, and a paid Maps call writes a ledger line the
     moment the provider answers.

The synthetic rows below are invented — a dental practice, a landscaper, a
person at a made-up employer. No real entity, no real person, no client name.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import build_packets as bp                                  # noqa: E402
import cost_ledger as cl                                    # noqa: E402
from lib import packets as pk                               # noqa: E402
from lib.fetch_cache import page_key                        # noqa: E402


# --------------------------------------------------------------- the fixtures

HOME = (
    "<html><head>"
    "<meta property='og:site_name' content='Northgate Dental Care'>"
    "<script type='application/ld+json'>"
    '{"@type":"Dentist","name":"Northgate Dental Care"}</script></head><body>"'
    "<nav><a href='/about'>About Us</a><a href='/services'>Our Services</a>"
    "<a href='/contact'>Contact</a></nav>"
    + "<p>Northgate Dental Care is an independently owned family practice. "
      "We have cleaned teeth on this corner since 1998. We do not place "
      "implants.</p>" * 6
    + "<footer><p>&copy; 2026 Northgate Dental Care PLLC. "
      "All rights reserved.</p></footer></body></html>")
ABOUT = "<html><body><p>" + "Our team has six hygienists. " * 20 + "</p></body></html>"
SERVICES = ("<html><body><p>" + "Cleanings, crowns and whitening. " * 20
            + "</p></body></html>")
CONTACT = "<html><body><p>Call us at 555-0100 on Northgate Road.</p></body></html>"

#: A shape-only NPPES payload. The numbers are invented.
NPPES = {"result_count": 1, "results": [{
    "number": "0000000000", "enumeration_type": "NPI-2",
    "basic": {"status": "A", "last_updated": "2025-02-01"},
    "taxonomies": [{"primary": True, "code": "1223G0001X",
                    "desc": "Dentist - General Practice"}]}]}

MAPS = {"name": "Northgate Dental Care", "category": "Dentist",
        "subtypes": ["Dental clinic"], "business_status": "OPEN",
        "street_address": "10 Northgate Rd", "city": "Springfield",
        "state": "IL", "zip": "62704", "phone": "+15550100",
        "review_count": 41, "_lane": "dataforseo"}

PERSON = {"headline": "Owner at Northgate Dental Care",
          "about_me": "I have run a two-chair practice for twenty years.",
          "location": {"city": "Springfield", "state_code": "IL"},
          "experiences": [{"job_title": "Owner",
                           "company_name": "Northgate Dental Care",
                           "job_start_date": "2004-06", "job_end_date": None,
                           "job_is_current": True},
                          {"job_title": "Associate", "company_name": "Elm Dental",
                           "job_start_date": "1999-01",
                           "job_end_date": "2004-05", "job_is_current": False}]}


def seed_cache(run_dir: Path, *, pages=True, subpages=True) -> Path:
    """A page cache the run replays — the offline half of every test here."""
    pc = run_dir / "page_cache"
    pc.mkdir(parents=True, exist_ok=True)
    meta, bodies = [], {}
    if pages:
        meta.append({"domain": "northgate-dental.example",
                     "fetched_at": "2026-09-19T00:00:00+00:00", "status": 200,
                     "final_url": "https://northgate-dental.example/",
                     "redirect_chain": [], "title": "Northgate Dental Care",
                     "tls_names": [], "favicon_mmh3": None,
                     "content_len": len(HOME), "from_cache": False})
        bodies["northgate-dental.example"] = HOME
    if subpages:
        for path, body in (("/about", ABOUT), ("/services", SERVICES),
                           ("/contact", CONTACT)):
            url = "https://northgate-dental.example" + path
            key = page_key(url)
            meta.append({"domain": key, "fetched_at": "2026-09-19T00:00:00+00:00",
                         "status": 200, "final_url": url, "redirect_chain": [],
                         "title": "", "tls_names": [], "favicon_mmh3": None,
                         "content_len": len(body), "from_cache": False})
            bodies[key] = body
    (pc / "meta.jsonl").write_text("".join(json.dumps(m) + "\n" for m in meta))
    for key, body in bodies.items():
        (pc / f"{key}.html").write_text(body)
    return pc


def companies_csv(tmp_path: Path, rows=None) -> Path:
    rows = rows if rows is not None else [
        "Northgate Dental Care,northgate-dental.example,Springfield,IL",
        "Cedar Ridge Landscaping,,Springfield,IL",
        ",,,",
    ]
    path = tmp_path / "companies.csv"
    path.write_text("name,domain,city,state\n" + "\n".join(rows) + "\n")
    return path


def people_csv(tmp_path: Path, rows=None) -> Path:
    rows = rows if rows is not None else [
        "Dana Reyes,https://linkedin.example/in/dana-reyes,"
        "Northgate Dental Care,northgate-dental.example,Owner",
        "Sam Okafor,,Cedar Ridge Landscaping,,Operations Manager",
    ]
    path = tmp_path / "people.csv"
    path.write_text("name,linkedin_url,company,company_domain,title\n"
                    + "\n".join(rows) + "\n")
    return path


class FakeBlitz:
    """Duck-types lib.person_profiles.BlitzPersonClient — one URL per call.

    `error=` makes every call come back the way the real client reports a
    TRANSPORT failure (a status the vendor never sent, plus an `error`), which
    is the one profile state an enrichment pass is allowed to re-ask.
    """

    def __init__(self, data, error=None):
        self.data = data
        self.error = error
        self.seen = []

    def fetch(self, url):
        self.seen.append(url)
        if self.error:
            return {"url": url, "found": False, "person": {}, "status": -1,
                    "error": self.error}
        person = self.data.get(url)
        return {"url": url, "found": bool(person), "person": person or {},
                "status": 200}


def lanes(tmp_path, **kw):
    """A Lanes with every network seam filled by a fake and a real ledger."""
    ledger = kw.pop("ledger", None) or cl.Ledger(tmp_path / "cost_ledger.jsonl")
    kw.setdefault("registry_http", lambda url: NPPES)
    return bp.Lanes(ledger=ledger, **kw)


# ------------------------------------------------------------------- intake

def test_company_intake_takes_the_columns_it_is_given(tmp_path):
    path = companies_csv(tmp_path)
    anchors, bad = bp.read_companies(path)
    assert [a["name"] for a in anchors] == ["Northgate Dental Care",
                                            "Cedar Ridge Landscaping"]
    assert anchors[0]["domain"] == "northgate-dental.example"
    # the optional columns are simply missing, not blank-filled
    assert anchors[1]["domain"] == ""
    assert anchors[1]["maps_place_id"] == "" and anchors[1]["npi"] == ""
    # the unusable row is counted, with the line number, and never packeted
    assert len(bad) == 1 and bad[0]["line"] == 4


def test_people_intake_keys_on_a_hash_not_on_the_name(tmp_path):
    anchors, bad = bp.read_people(people_csv(tmp_path))
    assert not bad
    assert len(anchors) == 2
    for anchor in anchors:
        assert "dana" not in anchor["entity_key"].lower()
        assert "okafor" not in anchor["entity_key"].lower()
    assert anchors[0]["linkedin_url"].endswith("/in/dana-reyes")
    assert anchors[1]["linkedin_url"] == ""       # no URL is not a fake URL


def test_a_row_id_column_names_the_packet_under_its_kind(tmp_path):
    """The operator's row_id survives into the key, prefixed by the kind.

    The prefix is what keeps `1` in a companies CSV and `1` in a people CSV
    from being the same packet.
    """
    path = tmp_path / "with_ids.csv"
    path.write_text("row_id,name,domain\nR1,Northgate Dental Care,"
                    "northgate-dental.example\n")
    anchors, _ = bp.read_companies(path)
    assert anchors[0]["entity_key"] == "c-R1"

    path = tmp_path / "people_ids.csv"
    path.write_text("row_id,name,company\nR1,Dana Reyes,Northgate Dental Care\n")
    anchors, _ = bp.read_people(path)
    assert anchors[0]["entity_key"] == "p-R1"


def test_intake_refuses_a_run_with_no_usable_row(tmp_path):
    path = companies_csv(tmp_path, rows=[",,,"])
    with pytest.raises(SystemExit):
        bp.run(tmp_path / "run", companies=path, offline=True)


# -------------------------------------------------------------------- the run

def test_an_offline_run_writes_packets_an_index_and_a_report(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    summary = bp.run(run_dir, companies=companies_csv(tmp_path), offline=True,
                     lanes=lanes(tmp_path, offline=True))
    out = run_dir / "packets"
    assert summary["built_this_run"] == 2
    assert summary["bad_row_count"] == 1
    assert (out / "companies" / "northgate-dental.example.json").is_file()
    index = [json.loads(l) for l in (out / "index.jsonl").read_text().splitlines()]
    assert {r["key"] for r in index} == {a["key"] for a in index}
    assert (out / "richness_report.md").is_file()
    assert (out / "richness_report.json").is_file()
    report = (out / "richness_report.md").read_text()
    assert "richness report" in report.lower()
    assert "Spend, by lane" in report


def test_the_packet_block_is_the_contract_shape_untouched(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    bp.run(run_dir, companies=companies_csv(tmp_path), offline=True,
           lanes=lanes(tmp_path, offline=True))
    doc = json.loads((run_dir / "packets" / "companies"
                      / "northgate-dental.example.json").read_text())
    packet = doc["packet"]
    assert packet["packet_version"] == pk.PACKET_VERSION
    assert packet["kind"] == "company"
    assert set(("record", "site", "maps", "registry", "absent")) <= set(packet)
    assert packet["site"]["homepage_text"]["source"]
    assert packet["site"]["homepage_text"]["observed_at"]
    # a packet carries evidence and identity, never an answer
    assert "verdict" not in json.dumps(packet).lower()


def test_the_tier_on_disk_is_the_shared_functions_answer(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    bp.run(run_dir, companies=companies_csv(tmp_path), offline=True,
           lanes=lanes(tmp_path, offline=True))
    for path in (run_dir / "packets" / "companies").glob("*.json"):
        doc = json.loads(path.read_text())
        assert doc["richness"]["tier"] == pk.richness(doc["packet"])["tier"]


def test_a_row_with_no_domain_is_thin_and_says_why(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    bp.run(run_dir, companies=companies_csv(tmp_path), offline=True,
           lanes=lanes(tmp_path, offline=True))
    doc = next(json.loads(p.read_text())
               for p in (run_dir / "packets" / "companies").glob("*.json")
               if json.loads(p.read_text())["packet"]["record"]["name"]["value"]
               == "Cedar Ridge Landscaping")
    assert doc["richness"]["tier"] == "thin"
    assert doc["packet"]["absent"]["site.homepage_text"]["status"] == "absent"
    assert "no domain" in doc["packet"]["absent"]["site.homepage_text"]["reason"]


def test_limit_is_a_ceiling(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    summary = bp.run(run_dir, companies=companies_csv(tmp_path), offline=True,
                     limit=1, lanes=lanes(tmp_path, offline=True))
    assert summary["built_this_run"] == 1


def test_resume_skips_what_is_already_indexed(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    first = bp.run(run_dir, companies=companies_csv(tmp_path), offline=True,
                   limit=1, lanes=lanes(tmp_path, offline=True))
    assert first["built_this_run"] == 1
    second = bp.run(run_dir, companies=companies_csv(tmp_path), offline=True,
                    resume=True, lanes=lanes(tmp_path, offline=True))
    assert second["built_this_run"] == 1          # the other row, not both
    assert second["packets"] == 2                 # and the index kept the first
    third = bp.run(run_dir, companies=companies_csv(tmp_path), offline=True,
                   resume=True, lanes=lanes(tmp_path, offline=True))
    assert third["built_this_run"] == 0


def test_the_same_row_id_in_both_files_is_two_packets_not_one(tmp_path):
    """A companies CSV and a people CSV both number their rows 1, 2, 3...

    Per-file row ids are the obvious thing an operator exports, so the key a
    packet is filed under has to carry its kind. Before it did, `--resume`
    read one flat set of keys, decided the people rows were already built,
    and reported a complete run over an empty people/ directory.
    """
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    comp = tmp_path / "c.csv"
    comp.write_text("row_id,name,domain\n"
                    "1,Northgate Dental Care,northgate-dental.example\n"
                    "2,Cedar Ridge Landscaping,\n")
    ppl = tmp_path / "p.csv"
    ppl.write_text("row_id,name,linkedin_url,company,company_domain\n"
                   "1,Dana Reyes,,Northgate Dental Care,northgate-dental.example\n"
                   "2,Sam Okafor,,Cedar Ridge Landscaping,\n")

    first = bp.run(run_dir, companies=comp, people=ppl, offline=True, limit=2,
                   lanes=lanes(tmp_path, offline=True))
    assert first["built_this_run"] == 2
    second = bp.run(run_dir, companies=comp, people=ppl, offline=True,
                    resume=True, lanes=lanes(tmp_path, offline=True))
    assert second["built_this_run"] == 2, "the people rows are not the company rows"

    out = run_dir / "packets"
    assert len(list((out / "companies").glob("*.json"))) == 2
    assert len(list((out / "people").glob("*.json"))) == 2
    index = [json.loads(l) for l in (out / "index.jsonl").read_text().splitlines()]
    assert len(index) == 4
    assert len({r["key"] for r in index}) == 4, "four rows, four distinct keys"
    third = bp.run(run_dir, companies=comp, people=ppl, offline=True,
                   resume=True, lanes=lanes(tmp_path, offline=True))
    assert third["built_this_run"] == 0


# ---------------------------------------------------------------- the lanes

def test_maps_is_not_called_at_all_without_a_budget(tmp_path):
    calls = []

    def paid(name, city, state):
        calls.append(name)
        return MAPS

    lane = lanes(tmp_path, maps_budget_usd=0.0, maps_paid=paid,
                 maps_credentials="test-only-not-a-real-key")
    assert lane.maps({"entity_key": "k", "name": "Northgate Dental Care"}) is None
    assert calls == []


def test_a_budgeted_maps_call_is_logged_the_moment_it_answers(tmp_path):
    ledger_path = tmp_path / "cost_ledger.jsonl"
    lane = lanes(tmp_path, maps_budget_usd=0.01,
                 maps_paid=lambda n, c, s: MAPS,
                 maps_credentials="test-only-not-a-real-key",
                 ledger=cl.Ledger(ledger_path))
    rec = lane.maps({"entity_key": "k1", "name": "Northgate Dental Care"})
    assert rec["category"] == "Dentist"
    assert rec["_observed_at"], "a Maps record needs OUR observation date"
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines()]
    paid_rows = [r for r in rows if r["provider"] == "dataforseo"]
    assert len(paid_rows) == 1
    assert paid_rows[0]["usd"] == bp.MAPS_COST_USD
    assert paid_rows[0]["basis"] == "imputed"
    assert paid_rows[0]["rung"] == bp.MAPS_RUNG


def test_the_maps_budget_is_a_refusal_not_a_warning(tmp_path):
    calls = []
    lane = lanes(tmp_path, maps_budget_usd=bp.MAPS_COST_USD,
                 maps_paid=lambda n, c, s: (calls.append(n), MAPS)[1],
                 maps_credentials="test-only-not-a-real-key")
    assert lane.maps({"entity_key": "a", "name": "One"}) is not None
    assert lane.maps({"entity_key": "b", "name": "Two"}) is None
    assert calls == ["One"]
    assert lane.counts["maps_budget_exhausted"] == 1


def test_a_negative_budget_is_a_bug_not_a_setting(tmp_path):
    with pytest.raises(ValueError):
        lanes(tmp_path, maps_budget_usd=-1.0)


def test_no_credentials_means_no_maps_call_even_with_a_budget(tmp_path):
    calls = []
    lane = lanes(tmp_path, maps_budget_usd=1.0, maps_credentials=(),
                 maps_paid=lambda n, c, s: calls.append(n) or MAPS)
    assert lane.maps({"entity_key": "k", "name": "Northgate Dental Care"}) is None
    assert calls == []
    assert lane.counts["maps_no_key"] == 1


def test_a_metered_cost_from_the_provider_wins_over_the_list_price(tmp_path):
    ledger_path = tmp_path / "cost_ledger.jsonl"
    lane = lanes(tmp_path, maps_budget_usd=1.0, maps_credentials=("u", "p"),
                 maps_paid=lambda n, c, s: dict(MAPS, _cost_usd=0.0021, _cost_basis="measured"),
                 ledger=cl.Ledger(ledger_path))
    rec = lane.maps({"entity_key": "k1", "name": "Northgate Dental Care"})
    assert "_cost_usd" not in rec
    row = [json.loads(l) for l in ledger_path.read_text().splitlines()][-1]
    assert row["usd"] == 0.0021 and row["basis"] == "measured"
    assert lane.spend["maps_usd"] == 0.0021


def test_the_registry_lane_asks_nobody_when_the_run_is_offline(tmp_path):
    asked = []
    lane = lanes(tmp_path, offline=True, registry_mode="always",
                 registry_http=lambda url: asked.append(url) or NPPES)
    assert lane.registry({"entity_key": "k", "name": "Northgate Dental Care"}) is None
    assert asked == []


def test_the_registry_lane_is_gated_unless_always(tmp_path):
    asked = []

    def http(url):
        asked.append(url)
        return NPPES

    landscaper = {"entity_key": "k", "name": "Cedar Ridge Landscaping"}
    assert lanes(tmp_path, registry_http=http).registry(landscaper) is None
    assert asked == []
    rec = lanes(tmp_path, registry_mode="always",
                registry_http=http).registry(landscaper)
    assert rec["source"] == "nppes" and len(asked) == 1
    # a dental name is healthcare-shaped, so `auto` asks for it
    assert lanes(tmp_path, registry_http=http).registry(
        {"entity_key": "k", "name": "Northgate Dental Care"})


def test_an_npi_on_the_row_is_queried_by_number(tmp_path):
    asked = []

    def http(url):
        asked.append(url)
        return NPPES

    rec = lanes(tmp_path, registry_http=http).registry(
        {"entity_key": "k", "name": "Cedar Ridge Landscaping",
         "npi": "0000000000"})
    assert rec["npi"] == "0000000000"
    assert "number=0000000000" in asked[0]
    assert "organization_name" not in asked[0]


def test_the_profile_lane_is_off_unless_the_caller_asks(tmp_path):
    client = FakeBlitz({})
    lane = lanes(tmp_path, profiles=False, profile_client=client)
    profile, reason = lane.profile({"entity_key": "k",
                                    "linkedin_url": "https://www.linkedin.com/in/x"})
    assert profile is None and reason == "profile_lane_off"
    assert client.seen == []


def test_a_person_with_a_url_gets_a_profile_and_a_company_identity(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    url = "https://linkedin.example/in/dana-reyes"
    client = FakeBlitz({url: PERSON})
    summary = bp.run(run_dir, people=people_csv(tmp_path), offline=True,
                     lanes=lanes(tmp_path, offline=True, profiles=True,
                                 profile_client=client))
    assert summary["built_this_run"] == 2
    docs = [json.loads(p.read_text())
            for p in sorted((run_dir / "packets" / "people").glob("*.json"))]
    with_profile = [d for d in docs if d["lanes"]["profile"]]
    assert len(with_profile) == 1
    packet = with_profile[0]["packet"]
    assert packet["kind"] == "person"
    assert packet["profile"]["headline"]["value"].startswith("Owner at")
    exps = packet["profile"]["current_experiences"]["value"]
    assert exps[0]["title"] == "Owner"
    assert exps[0]["provider_current"] is True
    assert exps[0]["dates_text"], "a rich person packet needs dated roles"
    # the company identity travels; the company's PAGE TEXT does not
    assert packet["company_identity"]["og_site_name"]["value"] == "Northgate Dental Care"
    assert "homepage_text" not in packet["company_identity"]
    assert with_profile[0]["richness"]["tier"] == "rich"


def test_a_person_without_a_url_says_no_profile_url(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    client = FakeBlitz({})
    bp.run(run_dir, people=people_csv(tmp_path), offline=True,
           lanes=lanes(tmp_path, offline=True, profiles=True,
                       profile_client=client))
    docs = [json.loads(p.read_text())
            for p in (run_dir / "packets" / "people").glob("*.json")]
    bare = [d for d in docs if d["lanes"]["profile_reason"] == "no_profile_url"]
    assert len(bare) == 1
    reason = bare[0]["packet"]["absent"]["profile.headline"]
    assert reason["status"] == "absent"
    assert reason["reason"].startswith("no_profile_url")
    assert bare[0]["richness"]["tier"] == "thin"
    report = (run_dir / "packets" / "richness_report.md").read_text()
    assert "no_profile_url" in report
    # a name is never printed in the report — the key is a hash
    assert "Sam Okafor" not in report


# ---------------------------------------------------------------- enrichment

def test_enrich_adds_fields_and_records_both_rounds(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    path = companies_csv(tmp_path, rows=[
        "Northgate Dental Care,northgate-dental.example,Springfield,IL"])
    summary = bp.run(run_dir, companies=path, offline=True, enrich=True,
                     lanes=lanes(tmp_path, offline=True, registry_mode="never"),
                     max_subpages=5)
    doc = json.loads((run_dir / "packets" / "companies"
                      / "northgate-dental.example.json").read_text())
    rounds = doc["rounds"]
    assert [r["round"] for r in rounds] == [1, 2], "one extra pass, never two"
    assert "site.contact_text" in rounds[1]["enrichment_added"], (
        "round 1 spends 2 sub-pages, round 2 spends the full budget — the "
        "contact page is what the raised cap buys")
    assert summary["enriched_rows"] == 1
    assert summary["enrichment_added_fields"]["site.contact_text"] == 1
    report = (run_dir / "packets" / "richness_report.md").read_text()
    assert "What --enrich added" in report


def test_enrich_reports_what_it_could_not_find(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir, subpages=False)
    path = companies_csv(tmp_path, rows=["Cedar Ridge Landscaping,,Springfield,IL"])
    summary = bp.run(run_dir, companies=path, offline=True, enrich=True,
                     lanes=lanes(tmp_path, offline=True))
    doc = json.loads(next((run_dir / "packets" / "companies").glob("*.json")).read_text())
    assert doc["rounds"][1]["enrichment_added"] == []
    assert doc["rounds"][1]["enrichment_missing"]
    assert summary["enrichment_added_fields"] == {}
    assert "found nothing more" in (run_dir / "packets"
                                    / "richness_report.md").read_text()


def test_enrich_never_re_asks_a_profile_the_vendor_already_answered(tmp_path):
    """A 404 is an ANSWER, and it cost a record to get.

    A thin person packet is thin precisely because the profile did not
    resolve, so these are exactly the rows `--enrich` walks. Re-asking the
    vendor for a URL it has already said it has no record for doubles a
    metered spend and cannot change the packet.
    """
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    client = FakeBlitz({})                     # found: False, status 200
    lane = lanes(tmp_path, offline=True, profiles=True, profile_client=client)
    bp.run(run_dir, people=people_csv(tmp_path), offline=True, enrich=True,
           lanes=lane)
    assert lane.counts["profile_calls"] == 1, "one row carries a URL, one call"
    assert len(client.seen) == 1
    doc = next(json.loads(p.read_text())
               for p in (run_dir / "packets" / "people").glob("*.json")
               if json.loads(p.read_text())["lanes"]["profile_reason"]
               == "profile_not_found")
    assert len(doc["rounds"]) == 2             # the pass still ran; it just
    assert doc["richness"]["tier"] == "thin"   # did not re-buy the same answer


def test_a_transport_failure_is_the_one_profile_state_enrich_re_asks(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    client = FakeBlitz({}, error="timeout")
    lane = lanes(tmp_path, offline=True, profiles=True, profile_client=client)
    bp.run(run_dir, people=people_csv(tmp_path), offline=True, enrich=True,
           lanes=lane)
    assert lane.counts["profile_calls"] == 2, "a failed call answered nothing"
    doc = next(json.loads(p.read_text())
               for p in (run_dir / "packets" / "people").glob("*.json")
               if json.loads(p.read_text())["lanes"]["profile_reason"]
               == "profile_error")
    assert doc["packet"]["absent"]["profile.headline"]["reason"].startswith(
        "profile_error")


def test_max_profiles_is_a_ceiling_the_lane_refuses_at(tmp_path):
    url = "https://linkedin.example/in/dana-reyes"
    client = FakeBlitz({url: PERSON})
    lane = lanes(tmp_path, profiles=True, profile_client=client, max_profiles=1)
    assert lane.profile({"entity_key": "a", "linkedin_url": url})[0]
    profile, reason = lane.profile({"entity_key": "b",
                                    "linkedin_url": url + "-two"})
    assert profile is None and reason == "profile_budget_exhausted"
    assert len(client.seen) == 1
    assert lane.counts["profile_budget_exhausted"] == 1


def test_a_rich_packet_is_not_enriched_at_all(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    path = companies_csv(tmp_path, rows=[
        "Northgate Dental Care,northgate-dental.example,Springfield,IL"])
    # The pages replay offline; the free registry lane answers from the
    # injected transport, which is what makes this packet rich.
    bp.run(run_dir, companies=path, offline=True, enrich=True,
           lanes=lanes(tmp_path, registry_mode="always"))
    doc = json.loads((run_dir / "packets" / "companies"
                      / "northgate-dental.example.json").read_text())
    assert doc["richness"]["tier"] == "rich"
    assert len(doc["rounds"]) == 1


# ------------------------------------------------------------ the network gate

def test_the_builder_never_reaches_the_network(tmp_path, monkeypatch):
    """An offline run must not make one request. Prove it, don't assume it."""
    from lib import fetch_cache

    def explode(*a, **kw):                # noqa: ANN002, ANN003
        raise AssertionError("an offline packet build tried to fetch")

    monkeypatch.setattr(fetch_cache, "fetch_url", explode)
    monkeypatch.setattr(fetch_cache, "_one_hop", explode)
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    summary = bp.run(run_dir, companies=companies_csv(tmp_path),
                     people=people_csv(tmp_path), offline=True, enrich=True,
                     lanes=lanes(tmp_path, offline=True))
    assert summary["built_this_run"] == 4
    assert summary["fetches"] == 0


def test_an_offline_miss_is_not_fetched_never_a_failure(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir, pages=False, subpages=False)
    path = companies_csv(tmp_path, rows=[
        "Northgate Dental Care,northgate-dental.example,Springfield,IL"])
    bp.run(run_dir, companies=path, offline=True,
           lanes=lanes(tmp_path, offline=True))
    doc = json.loads((run_dir / "packets" / "companies"
                      / "northgate-dental.example.json").read_text())
    assert doc["packet"]["absent"]["site.homepage_text"]["status"] == "not_fetched"


# -------------------------------------------------- the measured distributions
# Pinned so a change to the richness rule shows up as a diff in these counts
# rather than as a quietly different report. The packets are SYNTHETIC and are
# built field-by-field, not scraped: the point is the tier boundary, not a row.

def _company(**site):
    packet = {"packet_version": pk.PACKET_VERSION, "kind": "company",
              "record": {"name": pk.field("Northgate Dental Care",
                                          source="row", observed_at="2026-09-19")},
              "site": {}, "maps": {}, "registry": {}, "absent": {}}
    for name, value in site.items():
        block = "maps" if name.startswith("maps_") else (
            "registry" if name.startswith("registry_") else "site")
        key = name.split("_", 1)[1] if block != "site" else name
        packet[block][key] = pk.field(value, source="fixture",
                                      observed_at="2026-09-19")
    return packet


def _person(**profile):
    packet = {"packet_version": pk.PACKET_VERSION, "kind": "person",
              "record": {"name": pk.field("Dana Reyes", source="row",
                                          observed_at="2026-09-19")},
              "profile": {}, "company_identity": {}, "absent": {}}
    for name, value in profile.items():
        packet["profile"][name] = pk.field(value, source="fixture",
                                           observed_at="2026-09-19")
    return packet


RICH_COMPANY = _company(homepage_text="We are an independently owned practice.",
                        about_text="Our team has six hygienists.",
                        services_text="Cleanings, crowns and whitening.",
                        maps_primary_category="Dentist")
PARTIAL_COMPANY = _company(homepage_text="We are an independently owned practice.",
                           maps_primary_category="Dentist")
THIN_COMPANY = _company(maps_primary_category="Dentist",
                        maps_business_status="OPEN")
RICH_PERSON = _person(
    headline="Owner at Northgate Dental Care",
    about="I have run a two-chair practice for twenty years.",
    location="Springfield IL",
    current_experiences=[{"title": "Owner", "company": "Northgate Dental Care",
                          "dates_text": "2004-06 – present (no end date on the "
                                        "profile)", "provider_current": True}])
THIN_PERSON = _person(location="Springfield IL")


@pytest.mark.parametrize("packet,tier", [
    (RICH_COMPANY, "rich"),
    (PARTIAL_COMPANY, "partial"),
    (THIN_COMPANY, "thin"),
    (RICH_PERSON, "rich"),
    (THIN_PERSON, "thin"),
])
def test_the_pinned_richness_distribution(packet, tier):
    assert pk.richness(packet)["tier"] == tier


def test_a_third_party_record_alone_is_never_a_rich_company():
    """Maps and a registry are someone ELSE's word about the company.

    Without the company's own page text there is nothing to settle against, so
    the tier is thin however many third-party fields are present.
    """
    packet = _company(maps_primary_category="Dentist",
                      maps_business_status="OPEN",
                      registry_entity_type="organization",
                      registry_taxonomy_code="1223G0001X")
    assert pk.richness(packet)["tier"] == "thin"


def test_an_undated_current_role_is_not_a_rich_person():
    packet = _person(headline="Owner", about="Twenty years in practice.",
                     location="Springfield IL",
                     current_experiences=[{"title": "Owner",
                                           "company": "Northgate Dental Care",
                                           "provider_current": True}])
    assert pk.richness(packet)["tier"] == "partial"


def test_the_report_counts_match_the_index(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    summary = bp.run(run_dir, companies=companies_csv(tmp_path),
                     people=people_csv(tmp_path), offline=True,
                     lanes=lanes(tmp_path, offline=True))
    index = [json.loads(l) for l in
             (run_dir / "packets" / "index.jsonl").read_text().splitlines()]
    from collections import Counter
    assert summary["by_tier"] == {
        t: Counter(r["tier"] for r in index).get(t, 0) for t in bp.TIERS}
    assert sum(summary["by_tier"].values()) == len(index)


# ------------------------------------------------ the row's own title (CRM)
# The CLI advertises a `title` column on the people CSV and the intake reads
# it. A value that is read and then dropped is worse than one that was never
# offered: the packet says neither "Owner" nor "we were told nothing", and the
# judge reading it cannot tell which. The contract has a home for it —
# `crm.title`, with `crm.persona_source` beside it saying on whose authority
# it is asserted (references/evidence-packet.md).

def test_a_roster_title_lands_in_the_packet_and_is_never_called_missing(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    url = "https://linkedin.example/in/dana-reyes"
    client = FakeBlitz({url: PERSON})
    bp.run(run_dir, people=people_csv(tmp_path), offline=True,
           lanes=lanes(tmp_path, offline=True, profiles=True,
                       profile_client=client))
    docs = [json.loads(p.read_text())
            for p in (run_dir / "packets" / "people").glob("*.json")]
    doc = next(d for d in docs if d["lanes"]["profile"])
    packet = doc["packet"]

    # the anchor carries it, sourced to the FILE AND LINE it was read from
    assert packet["record"]["title"]["value"] == "Owner"
    assert packet["record"]["title"]["source"].startswith("people.csv:")
    assert packet["record"]["title"]["observed_at"]

    # and the contract's CRM block carries it with its provenance
    assert packet["crm"]["title"]["value"] == "Owner"
    assert packet["crm"]["title"]["source"].startswith("people.csv:")
    assert packet["crm"]["persona_source"]["value"] == "vendor_field"
    # a blank title date reads as UNKNOWN, never as current
    assert packet["absent"]["crm.title_as_of"]["status"] == "absent"

    present = pk.present_fields(packet)
    assert "record.title" in present and "crm.title" in present
    assert "record.title" not in packet["absent"]
    assert "crm.title" not in packet["absent"]
    assert doc["richness"]["tier"] == "rich"


def test_the_rows_own_title_is_not_evidence_about_the_person(tmp_path):
    """A title the row asserts cannot lift a packet off the thin tier.

    Sam Okafor's row carries "Operations Manager" and no profile URL: nothing
    about that person was ever read. If the row's own word counted as evidence
    the packet would tag `partial` and skip the enrichment a thin row gets.
    """
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    bp.run(run_dir, people=people_csv(tmp_path), offline=True,
           lanes=lanes(tmp_path, offline=True))
    docs = [json.loads(p.read_text())
            for p in (run_dir / "packets" / "people").glob("*.json")]
    bare = next(d for d in docs
                if d["packet"]["crm"].get("title", {}).get("value")
                == "Operations Manager")
    assert bare["richness"]["tier"] == "thin"


def test_a_row_with_no_title_records_nothing_false(tmp_path):
    path = tmp_path / "people_no_title.csv"
    path.write_text("name,linkedin_url,company\n"
                    "Dana Reyes,,Northgate Dental Care\n")
    anchors, bad = bp.read_people(path)
    assert not bad
    packet = pk.build_person_packet(anchors[0])
    assert "title" not in packet["record"], "a blank is not a title"
    assert "title" not in packet["crm"]
    assert packet["absent"]["crm.title"]["status"] == "absent"
    assert "no title" in packet["absent"]["crm.title"]["reason"]
    assert packet["absent"]["crm.persona_source"]["status"] == "absent"
    present = pk.present_fields(packet)
    assert "record.title" not in present and "crm.title" not in present


# ------------------------------------------ a lane is asked once, not twice
# `--enrich` re-asks a lane that came back empty. "Empty" and "never asked"
# are different states, and for a PAID lane the difference is money: the
# DataForSEO call that returns no listing is billed exactly like the one
# that returns a record.

def _miss(url):
    """A fetch seam that answers every URL with a cache miss, never the wire."""
    return {"error": "offline_cache_miss", "status": None}, ""


def test_enrich_never_re_asks_a_paid_maps_lane_that_already_answered(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir, pages=False, subpages=False)
    calls = []
    ledger_path = tmp_path / "cost_ledger.jsonl"

    def paid(name, city, state):
        calls.append(name)
        return None                    # the provider has no listing for this row

    lane = lanes(tmp_path, maps_budget_usd=1.0, maps_paid=paid,
                 maps_credentials="test-only-not-a-real-key",
                 ledger=cl.Ledger(ledger_path))
    path = companies_csv(tmp_path, rows=["Cedar Ridge Landscaping,,Springfield,IL"])
    bp.run(run_dir, companies=path, enrich=True, lanes=lane, fetch_page=_miss)

    assert calls == ["Cedar Ridge Landscaping"], (
        "round 2 re-bought the same no-result — a paid lane that ANSWERED, "
        "even with nothing, is never asked twice")
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines()]
    assert len([r for r in rows if r["provider"] == "dataforseo"]) == 1
    assert lane.spend["maps_usd"] == bp.MAPS_COST_USD


def test_enrich_never_re_asks_a_registry_that_already_answered(tmp_path):
    run_dir = tmp_path / "run"
    seed_cache(run_dir, pages=False, subpages=False)
    asked = []

    def registry_http(url):
        asked.append(url)
        return {"result_count": 0, "results": []}

    lane = lanes(tmp_path, registry_mode="always", registry_http=registry_http)
    path = companies_csv(tmp_path, rows=["Cedar Ridge Landscaping,,Springfield,IL"])
    bp.run(run_dir, companies=path, enrich=True, lanes=lane, fetch_page=_miss)
    assert len(asked) == 1, ("NPPES answered 'no such organization' once; the "
                             "enrichment round asked it again")


def test_enrich_never_re_asks_the_profile_lane_for_a_url_it_answered(tmp_path):
    """The builder claims this one already — prove it rather than trust it."""
    run_dir = tmp_path / "run"
    seed_cache(run_dir)
    ledger_path = tmp_path / "cost_ledger.jsonl"
    client = FakeBlitz({})                       # found: False — an ANSWER
    lane = lanes(tmp_path, offline=True, profiles=True, profile_client=client,
                 ledger=cl.Ledger(ledger_path))
    bp.run(run_dir, people=people_csv(tmp_path), offline=True, enrich=True,
           lanes=lane)
    assert len(client.seen) == 1
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines()]
    assert len([r for r in rows if r["provider"] == "blitz"]) == 1
