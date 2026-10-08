"""One presence brain — Phases 6, 7 and 8 must read a panel the same way.

Fixture is the REAL enrich_panel output shape: every source key holds
{source, success, data, error}, so a dotted queryable_field has to resolve
under `data` as well as at the blob's top level.

Run:
    python -m pytest -q skills/win-loss-rewind/tests/test_signal_presence.py
"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backcast_validate  # noqa: E402
import compose_discriminator  # noqa: E402
import enrich_panel  # noqa: E402
import generate_hypotheses  # noqa: E402
import score_prospect  # noqa: E402
import signal_presence  # noqa: E402


# ── Fixture: one customer panel in enrich_one_customer's exact shape ──

def real_panel() -> dict:
    """Mirror of enrich_panel.enrich_one_customer output (no network)."""
    return {
        "customer_id": "acmelegal.example",
        "company": "Acme Legal",
        "domain": "acmelegal.example",
        "outcome": "won",
        "signal_panel": {
            # succeeded, field present under data
            "firecrawl_map": {
                "source": "firecrawl_map",
                "success": True,
                "data": {
                    "page_count": 42,
                    "has_careers_page": True,
                    "has_pricing_page": False,
                    "sample_urls": ["https://acmelegal.example/careers"],
                },
                "error": None,
            },
            # succeeded, but the requested field is genuinely missing
            "apify_linkedin": {
                "source": "apify_linkedin",
                "success": True,
                "data": {
                    "employees_count": 120,
                    "headquarters": "Austin, TX",
                    "linkedin_url": "https://linkedin.example/company/acmelegal",
                },
                "error": None,
            },
            # the source never ran: key missing but no key set
            "exa": {
                "source": "exa",
                "success": False,
                "data": None,
                "error": "EXA_API_KEY not set",
            },
        },
        "wayback_timeline": None,
        "reconciliation_flags": [],
        "_cost_estimate_usd": 0.01,
    }


def hypothesis(source, field, weight=1.0, predicate=None):
    """A Phase 5 hypothesis carrying a real queryable_field (and typed predicate)."""
    h = {
        "hypothesis_id": "1-h1",
        "archetype_id": 1,
        "signal": "%s %s" % (source, field),
        "observability_source": source,
        "queryable_field": field,
        "weight": weight,
        "temporal_provability": {
            "best_case_tag": "ARCHIVED_BEFORE",
            "how": "archive.org capture of the careers page at-or-before the reference date",
        },
        "archive_search_pattern": "open roles",
    }
    if predicate is not None:
        h["predicate"] = predicate
    return h


class FixtureIsRealisticTests(unittest.TestCase):
    def test_panel_keys_are_enrich_panel_source_keys(self):
        panel_keys = set(real_panel()["signal_panel"])
        known = {"firecrawl_map", "firecrawl_scrape", "exa", "openweb_ninja",
                 "apify_linkedin", "serper_news"}
        self.assertTrue(panel_keys <= known)
        self.assertTrue(set(enrich_panel.ALL_SOURCES))  # module really imported

    def test_queryable_field_survives_the_phase_5_gate(self):
        """The field used below is one Phase 5 would actually ship."""
        manifest = generate_hypotheses.load_source_manifest()
        self.assertIsNone(
            generate_hypotheses.check_gate4(
                hypothesis("firecrawl_map", "has_careers_page"), manifest))


class SharedHelperTests(unittest.TestCase):
    def test_field_under_data_resolves(self):
        panel = real_panel()
        self.assertEqual(
            signal_presence.signal_state(hypothesis("firecrawl_map", "has_careers_page"), panel),
            "present")
        self.assertEqual(
            signal_presence.signal_state(hypothesis("apify_linkedin", "employees_count",
                                                    predicate={"op": "gte", "value": 100}), panel),
            "present")

    def test_dotted_field_under_data_resolves(self):
        panel = real_panel()
        self.assertEqual(
            signal_presence.signal_state(hypothesis("firecrawl_map", "data.page_count",
                                                    predicate={"op": "gt", "value": 10}), panel),
            "present")

    def test_falsy_field_under_data_is_absent(self):
        panel = real_panel()
        self.assertEqual(
            signal_presence.signal_state(hypothesis("firecrawl_map", "has_pricing_page"), panel),
            "absent")

    def test_unsupported_selector_is_unknown_and_rejected(self):
        panel = real_panel()
        h = hypothesis("apify_linkedin", "recent_leadership_hire")
        self.assertEqual(signal_presence.signal_state(h, panel), "unknown")
        self.assertIn("unsupported selector", signal_presence.validate_signal(h))
        self.assertIsNotNone(generate_hypotheses.check_gate4(
            h, generate_hypotheses.load_source_manifest()))

    def test_numeric_field_is_not_a_boolean(self):
        panel = real_panel()
        few = hypothesis("apify_linkedin", "employees_count", predicate={"op": "lt", "value": 10})
        self.assertEqual(signal_presence.signal_state(few, panel), "absent")   # 120 is not < 10
        bare = hypothesis("apify_linkedin", "employees_count")
        self.assertIn("needs a predicate", signal_presence.validate_signal(bare))
        self.assertEqual(signal_presence.signal_state(bare, panel), "unknown")

    def test_not_extracted_is_unknown_not_absent(self):
        panel = real_panel()
        panel["signal_panel"]["apify_linkedin"]["data"]["employees_count"] = None
        h = hypothesis("apify_linkedin", "employees_count", predicate={"op": "lt", "value": 10})
        res = signal_presence.evaluate(h, panel)
        self.assertEqual((res["state"], res["reason"]), ("unknown", "not_extracted"))

    def test_failed_source_is_unknown(self):
        panel = real_panel()
        self.assertEqual(
            signal_presence.signal_state(hypothesis("exa", "results.published_date"), panel),
            "unknown")

    def test_error_string_makes_it_unknown_even_without_success_flag(self):
        panel = {"signal_panel": {"serper_news": {"source": "serper_news",
                                                  "data": None,
                                                  "error": "timeout"}}}
        self.assertEqual(
            signal_presence.signal_state(hypothesis("serper_news", "items.date"), panel),
            "unknown")

    def test_source_missing_from_panel_is_unknown(self):
        self.assertEqual(
            signal_presence.signal_state(hypothesis("openweb_ninja", "rating"), real_panel()),
            "unknown")

    def test_tristate_maps_unknown_to_none(self):
        panel = real_panel()
        self.assertIs(
            signal_presence.signal_present_tristate(hypothesis("exa", "results"), panel), None)
        self.assertIs(
            signal_presence.signal_present_tristate(
                hypothesis("firecrawl_map", "has_careers_page"), panel), True)
        self.assertIs(
            signal_presence.signal_present_tristate(
                hypothesis("firecrawl_map", "has_pricing_page"), panel), False)


class ThreeCallersAgreeTests(unittest.TestCase):
    """The split brain: Phase 6/7 used to score a missing field as present."""

    CASES = [
        ("firecrawl_map", "has_careers_page", True),    # present
        ("firecrawl_map", "has_pricing_page", False),   # observed false
    ]

    def test_unsupported_selector_is_unknown_everywhere(self):
        panel = real_panel()
        h = hypothesis("apify_linkedin", "recent_leadership_hire")
        self.assertFalse(score_prospect.signal_present(h, panel))
        self.assertFalse(compose_discriminator.signal_present(h, panel))
        self.assertIsNone(backcast_validate.signal_present_in_panel(h, panel))

    def test_compose_and_backcast_match_score_prospect(self):
        panel = real_panel()
        for source, field, expected in self.CASES:
            h = hypothesis(source, field)
            with self.subTest(source=source, field=field):
                self.assertEqual(score_prospect.signal_present(h, panel), expected)
                self.assertEqual(compose_discriminator.signal_present(h, panel), expected)
                self.assertEqual(
                    backcast_validate.signal_present_in_panel(h, panel), expected)

    def test_failed_source_is_unknown_everywhere(self):
        panel = real_panel()
        h = hypothesis("exa", "results.published_date")
        self.assertIs(backcast_validate.signal_present_in_panel(h, panel), None)
        self.assertFalse(compose_discriminator.signal_present(h, panel))
        self.assertEqual(score_prospect.signal_state(h, panel), "unknown")

    def test_all_three_delegate_to_the_shared_helper(self):
        self.assertIs(score_prospect.signal_state, signal_presence.signal_state)
        self.assertIs(compose_discriminator.signal_present, signal_presence.signal_present)
        self.assertIs(backcast_validate.signal_present_in_panel,
                      signal_presence.signal_present_tristate)


if __name__ == "__main__":
    unittest.main()
