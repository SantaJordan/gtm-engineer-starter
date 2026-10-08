"""Phase 6 firm pass: only a record that satisfies the signal can date it.

Includes the cold-start review's negative fixture: an old, unrelated article
used to certify a signal the panel did not have.
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import backcast_validate as bv  # noqa: E402

HIRING = {"hypothesis_id": "H-compliance", "signal": "posted a compliance officer role",
          "observability_source": "serper_news", "queryable_field": "items.snippet",
          "predicate": {"op": "regex", "value": r"hiring an? (chief )?compliance (officer|lead)"},
          "temporal_provability": {"best_case_tag": "RECORD_DATED_BEFORE", "how": "news date"},
          "record_date_field": "items.date"}


def panel(items, close="2024-06-01"):
    return {"customer_id": "c1", "outcome": "won", "close_date": close, "domain": "c1.example",
            "signal_panel": {"serper_news": {"source": "serper_news", "success": True, "error": None,
                                             "data": {"items": items, "count": len(items)}}}}


def item(snippet, date, link="https://news.example/a"):
    return {"title": "t", "link": link, "date": date, "snippet": snippet, "source": "News"}


def test_negative_fixture_old_unrelated_article_is_not_firm():
    p = panel([item("Acme opens a second office downtown", "2019-03-01")])
    assert bv.signal_present_in_panel(HIRING, p) is False
    cell = bv.verify_cell_firm(HIRING, p, provisional_present=False)
    assert cell["firm_present"] is None
    assert cell["visibility_tag"] == "RETRO_ASSUMED"


def test_satisfying_record_before_reference_is_firm_with_url_and_excerpt():
    p = panel([item("Acme opens a second office", "2019-03-01"),
               item("Acme is hiring a compliance officer for its new region", "2024-01-15",
                    link="https://news.example/hiring")])
    cell = bv.verify_cell_firm(HIRING, p, provisional_present=True)
    assert cell["firm_present"] is True
    assert cell["visibility_tag"] == "RECORD_DATED_BEFORE"
    assert cell["evidence_detail"]["url"] == "https://news.example/hiring"
    assert "compliance officer" in cell["evidence_detail"]["excerpt"]
    assert cell["evidence_detail"]["record_date"] == "2024-01-15"


def test_post_reference_only_results_are_unknown_not_absent():
    """A later article cannot prove that no earlier one existed."""
    p = panel([item("Acme is hiring a compliance lead", "2024-09-01")])
    cell = bv.verify_cell_firm(HIRING, p, provisional_present=True)
    assert cell["firm_present"] is None
    assert "cannot prove" in cell["evidence_detail"]["note"]


def test_field_level_signal_on_a_record_source_is_unknown():
    h = dict(HIRING, queryable_field="count", predicate={"op": "gte", "value": 1})
    cell = bv.verify_cell_firm(h, panel([item("anything", "2019-01-01")]), provisional_present=True)
    assert cell["firm_present"] is None


def test_archive_route_for_a_non_reextractable_source_is_unknown():
    h = {"hypothesis_id": "H-x", "signal": "careers page", "observability_source": "firecrawl_map",
         "queryable_field": "has_careers_page",
         "temporal_provability": {"best_case_tag": "ARCHIVED_BEFORE", "how": "capture"}}
    p = panel([])
    p["signal_panel"]["firecrawl_map"] = {"source": "firecrawl_map", "success": True, "error": None,
                                          "data": {"has_careers_page": True}}
    cell = bv.verify_cell_firm(h, p, provisional_present=True)
    assert cell["firm_present"] is None and "cannot be re-extracted" in cell["evidence_detail"]


MULTI = {"hypothesis_id": "H-multi-office", "signal": "homepage says 2+ offices",
         "observability_source": "firecrawl_scrape", "queryable_field": "location_count",
         "predicate": {"op": "gte", "value": 2}, "archive_search_pattern": "offices",
         "temporal_provability": {"best_case_tag": "ARCHIVED_BEFORE", "how": "capture"}}


def _archive(monkeypatch, text, captures=(("20240101000000", "2024-01-01"),)):
    monkeypatch.setattr(bv.wtt, "cdx_moments", lambda url, yfrom="2008", yto=None, **k: {
        "captures": [{"ts": ts, "day": day} for ts, day in captures], "truncated": False})
    monkeypatch.setattr(bv.wtt, "fetch_capture", lambda url, ts: text)


def _scrape_panel():
    p = panel([])
    p["signal_panel"]["firecrawl_scrape"] = {"source": "firecrawl_scrape", "success": True, "error": None,
                                             "data": {"location_count": 3}}
    return p


def test_archive_substring_never_certifies_a_numeric_signal(monkeypatch):
    """The review's counterexample: 'offices' appears, but the page lists ONE office."""
    _archive(monkeypatch, "Visit our office opposite the council offices.")
    cell = bv.verify_cell_firm(MULTI, _scrape_panel(), provisional_present=True)
    assert cell["firm_present"] is False
    assert cell["evidence_detail"]["extracted_value"] == 1


def test_archive_typed_predicate_true_is_firm_with_capture_evidence(monkeypatch):
    _archive(monkeypatch, "Family dentistry at our four offices across town.")
    cell = bv.verify_cell_firm(MULTI, _scrape_panel(), provisional_present=True)
    assert cell["firm_present"] is True and cell["visibility_tag"] == "ARCHIVED_BEFORE"
    assert cell["evidence_detail"]["extracted_value"] == 4
    assert cell["evidence_detail"]["capture"] == "2024-01-01"


def test_archive_without_extractable_field_is_unknown(monkeypatch):
    _archive(monkeypatch, "Welcome to our practice.")
    cell = bv.verify_cell_firm(MULTI, _scrape_panel(), provisional_present=True)
    assert cell["firm_present"] is None


def test_archive_capture_after_reference_is_ignored(monkeypatch):
    _archive(monkeypatch, "our four offices", captures=(("20250101000000", "2025-01-01"),))
    cell = bv.verify_cell_firm(MULTI, _scrape_panel(), provisional_present=True)
    assert cell["firm_present"] is None


def test_firm_contingency_counts_only_firm_cells():
    panels = [dict(panel([item("hiring a compliance officer", "2024-01-01")]), customer_id="a"),
              dict(panel([item("opens new office", "2019-01-01")]), customer_id="b", outcome="lost"),
              dict(panel([item("hiring a compliance lead", "2025-01-01")]), customer_id="c", outcome="lost")]
    ev = []
    a, b, c, d, counts = bv.firm_contingency(HIRING, panels, ev)
    assert (a, b, c, d) == (1, 0, 0, 0)
    assert counts["n_firm_present"] == 1 and counts["n_retro_assumed_or_unknown"] == 2
