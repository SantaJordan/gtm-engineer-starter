"""Phase 8 scoring: typed signals, the shared scorer, coverage, insufficient evidence.

Run:
    python -m pytest -q skills/win-loss-rewind/tests/test_score_prospect.py
"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import compose_discriminator as cd  # noqa: E402
import score_prospect  # noqa: E402


def sig(source, field, weight=1.0, predicate=None):
    s = {"signal": f"{source}.{field}", "observability_source": source,
         "queryable_field": field, "weight": weight}
    if predicate:
        s["predicate"] = predicate
    return s


def arc(signals, threshold=0.5):
    return {"archetype_id": 1, "archetype_name": "Test archetype", "threshold": threshold,
            "rubric": {"signals": signals}, "ship_status": "ship"}


def ok(source, **data):
    return {"source": source, "success": True, "data": data, "error": None}


def failed(source):
    return {"source": source, "success": False, "data": None, "error": "API key not set"}


CAREERS = sig("firecrawl_map", "has_careers_page")
OFFICES = sig("firecrawl_scrape", "location_count", predicate={"op": "gte", "value": 2})


class SignalStateTests(unittest.TestCase):
    def test_present_and_absent(self):
        p = {"signal_panel": {"firecrawl_map": ok("firecrawl_map", has_careers_page=True)}}
        self.assertEqual(score_prospect.signal_state(CAREERS, p), "present")
        p["signal_panel"]["firecrawl_map"]["data"]["has_careers_page"] = False
        self.assertEqual(score_prospect.signal_state(CAREERS, p), "absent")

    def test_threshold_is_applied_to_numbers(self):
        p = {"signal_panel": {"firecrawl_scrape": ok("firecrawl_scrape", location_count=1)}}
        self.assertEqual(score_prospect.signal_state(OFFICES, p), "absent")
        p["signal_panel"]["firecrawl_scrape"]["data"]["location_count"] = 5
        self.assertEqual(score_prospect.signal_state(OFFICES, p), "present")

    def test_unknown_when_source_missing_or_failed_or_not_extracted(self):
        self.assertEqual(score_prospect.signal_state(CAREERS, {"warning": "failed"}), "unknown")
        p = {"signal_panel": {"firecrawl_map": failed("firecrawl_map")}}
        self.assertEqual(score_prospect.signal_state(CAREERS, p), "unknown")
        p = {"signal_panel": {"firecrawl_scrape": ok("firecrawl_scrape", location_count=None)}}
        self.assertEqual(score_prospect.signal_state(OFFICES, p), "unknown")

    def test_legacy_signals_key_still_read(self):
        p = {"signals": {"firecrawl_map": ok("firecrawl_map", has_careers_page=True)}}
        self.assertTrue(score_prospect.signal_present(CAREERS, p))

    def test_unsupported_selector_never_scores(self):
        p = {"signal_panel": {"job_postings": {"hiring_ops_lead": 3}}}
        scored = score_prospect.score_against_archetype(arc([sig("job_postings", "hiring_ops_lead")]), p)
        self.assertFalse(scored["looks_like"])
        self.assertEqual(scored["status"], "insufficient_evidence")


class SharedScorerTests(unittest.TestCase):
    def test_calibration_and_prospect_scoring_agree(self):
        """One present, one unknown: training scored 1.0, Phase 8 used to say 0.5."""
        rubric = [sig("firecrawl_map", "has_careers_page", 0.5), dict(OFFICES, weight=0.5)]
        p = {"signal_panel": {"firecrawl_map": ok("firecrawl_map", has_careers_page=True),
                              "firecrawl_scrape": failed("firecrawl_scrape")}}
        train_score, _ = cd.score_panel({"signals": rubric}, p)
        scored = score_prospect.score_against_archetype(arc(rubric, threshold=0.8), p)
        self.assertAlmostEqual(train_score, 1.0)
        self.assertAlmostEqual(scored["fit_score"], train_score)
        self.assertTrue(scored["looks_like"])
        self.assertAlmostEqual(scored["coverage"], 0.5)
        self.assertEqual(scored["n_unknown"], 1)

    def test_wholly_unobserved_panel_is_insufficient_not_a_no(self):
        scored = score_prospect.score_against_archetype(arc([CAREERS, OFFICES]), {"signal_panel": {}})
        self.assertEqual(scored["status"], "insufficient_evidence")
        self.assertEqual(scored["coverage"], 0.0)

    def test_absent_counts_against(self):
        p = {"signal_panel": {"firecrawl_map": ok("firecrawl_map", has_careers_page=True),
                              "firecrawl_scrape": ok("firecrawl_scrape", location_count=1)}}
        scored = score_prospect.score_against_archetype(
            arc([dict(CAREERS, weight=0.6), dict(OFFICES, weight=0.4)], threshold=0.7), p)
        self.assertAlmostEqual(scored["fit_score"], 0.6)
        self.assertEqual(scored["status"], "not_like")
        self.assertEqual([d["state"] for d in scored["signal_diff"]], ["present", "absent"])


class RubricCarriesPredicateTests(unittest.TestCase):
    def test_composed_rubric_keeps_the_typed_test(self):
        surv = [dict(OFFICES, hypothesis_id="h1", train={"lift": 3.0}, holdout={"lift": 2.8})]
        rubric = cd.compose_rubric_for_archetype(surv)
        self.assertEqual(rubric["signals"][0]["predicate"], {"op": "gte", "value": 2})
        p = {"signal_panel": {"firecrawl_scrape": ok("firecrawl_scrape", location_count=4)}}
        self.assertEqual(score_prospect.score_against_archetype(arc(rubric["signals"]), p)["status"],
                         "looks_like")


if __name__ == "__main__":
    unittest.main()
