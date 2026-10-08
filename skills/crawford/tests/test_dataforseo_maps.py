"""DataForSEO Google Maps lane: parsing a synthetic response. No network."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))
import gmaps_facts as gf  # noqa: E402


def _item(title, **over):
    d = {"type": "maps_search", "rank_group": 1, "title": title,
         "domain": "northgatedental.example", "url": "https://northgatedental.example/",
         "rating": {"rating_type": "Max5", "value": 4.7, "votes_count": 212, "rating_max": None},
         "category": "Dentist", "additional_categories": ["Cosmetic dentist"],
         "address": "100 Example Ave, Springfield, OH 99999",
         "address_info": {"address": "100 Example Ave", "city": "Springfield",
                          "zip": "99999", "region": "Ohio", "country_code": "US"},
         "phone": "+1 555-555-0123", "place_id": "ChIJ_SYNTHETIC_0001",
         "cid": "123456789", "feature_id": "0x1a2b:0x75bcd15",
         "latitude": 40.0, "longitude": -83.0, "is_claimed": True, "total_photos": 58}
    d.update(over)
    return d


def _payload(items, cost=0.002, status=20000, task_status=20000):
    return {"status_code": status, "status_message": "Ok.", "cost": cost,
            "tasks": [{"status_code": task_status, "status_message": "Ok.", "cost": cost,
                       "result": [{"keyword": "x", "items_count": len(items), "items": items}]}]}


def test_maps_the_matching_listing_to_canonical_fields():
    calls = []

    def fake(body, creds):
        calls.append((body, creds))
        return _payload([_item("Bright Smile Orthodontics"), _item("Northgate Family Dental")])

    rec = gf.dataforseo_facts("Northgate Family Dental", "Springfield", "OH",
                              credentials=("login", "pw"), http_json=fake)
    assert rec["name"] == "Northgate Family Dental"
    assert rec["rating"] == 4.7 and rec["review_count"] == 212
    assert rec["category"] == "Dentist" and rec["subtypes"] == ["Cosmetic dentist"]
    assert rec["city"] == "Springfield" and rec["state"] == "Ohio" and rec["zip"] == "99999"
    assert rec["place_id"] == "ChIJ_SYNTHETIC_0001" and rec["cid"] == "123456789"
    assert rec["business_status"] is None          # open-right-now is not "still exists"
    assert rec["_cost_usd"] == 0.002 and rec["_cost_basis"] == "measured"
    body = calls[0][0][0]
    assert body["keyword"] == "Northgate Family Dental Springfield OH"
    assert body["language_code"] == "en" and body["location_name"] == "United States"


def test_no_shared_word_means_no_match():
    rec = gf.dataforseo_facts("Pinegate Veterinary", credentials=("l", "p"),
                              http_json=lambda b, c: _payload([_item("Harbor Dental")]))
    assert rec is None


def test_missing_cost_falls_back_to_list_price():
    rec = gf.dataforseo_facts("Northgate Family Dental", credentials=("l", "p"),
                              http_json=lambda b, c: _payload([_item("Northgate Family Dental")], cost=None))
    assert rec["_cost_usd"] == gf.DATAFORSEO_MAPS_COST_USD and rec["_cost_basis"] == "imputed"


@pytest.mark.parametrize("payload", [
    {"status_code": 40100, "status_message": "auth"},
    {"status_code": 20000, "tasks": []},
    _payload([], task_status=40501),
    "not json",
])
def test_bad_shapes_are_loud(payload):
    with pytest.raises(gf.GmapsFactsShapeError):
        gf.dataforseo_facts("Northgate", credentials=("l", "p"), http_json=lambda b, c: payload)


def test_credentials_need_both_names():
    assert gf.credentials_from_env({"DATAFORSEO_LOGIN": "a"}) is None
    assert gf.credentials_from_env({"DATAFORSEO_LOGIN": "a", "DATAFORSEO_PASSWORD": "b"}) == ("a", "b")


def test_enrich_business_without_credentials_calls_nothing():
    def boom(*a):
        raise AssertionError("must not be called")
    assert gf.enrich_business({"name": "x"}, credentials=None, http_json=boom) == (None, 0.0, "dataforseo")


def test_enrich_business_logs_the_metered_cost():
    rows = []

    class Led:
        def log(self, row_id, **kw):
            rows.append(kw)

    rec, cost, lane = gf.enrich_business(
        {"name": "Northgate Family Dental"}, credentials=("l", "p"), ledger=Led(),
        http_json=lambda b, c: _payload([_item("Northgate Family Dental")], cost=0.0021))
    assert lane == "dataforseo" and cost == 0.0021 and "_cost_usd" not in rec
    assert rows[0]["provider"] == "dataforseo" and rows[0]["basis"] == "measured"
