"""Survival rules must reject undefined lift; Phase 7 scoring must not treat
an unobserved signal as an absent one.

Two regressions:
  * An empty 2x2 (0,0,0,0) goes down the Fisher branch and yields lift=nan.
    Every comparison against nan is False, so a hypothesis with NO evidence
    at all sailed through `apply_survival_rules` and into the rubric.
  * `compose_discriminator.score_panel` asked `signal_present()`, which reads
    "unknown" as False — a source that never ran counted against the panel at
    full weight, dragging the score below a threshold calibrated the same way.

Run:
    python -m pytest -q skills/win-loss-rewind/tests/test_survival_and_scoring.py
"""
import math
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backcast_validate as bv  # noqa: E402
import compose_discriminator as cd  # noqa: E402

LIFT_MIN = 2.0
CI_MIN = 1.2


def strong_train() -> dict:
    """A train result that survives on its own (Fisher branch, no CI needed)."""
    return {"lift": 3.0, "ci_lower": None, "method": "fisher_exact",
            "fisher_p_value": 0.01, "fisher_p_value_greater": 0.005, "n_total": 30}


class UndefinedLiftTests(unittest.TestCase):
    def test_empty_2x2_yields_non_finite_lift(self):
        out = bv.compute_lift_with_ci(0, 0, 0, 0)
        self.assertIsNotNone(out["lift"], "empty table should still report a lift field")
        self.assertFalse(math.isfinite(out["lift"]),
                         "an empty 2x2 cannot produce a finite lift")

    def test_nan_train_lift_does_not_survive(self):
        train = bv.compute_lift_with_ci(0, 0, 0, 0)
        holdout = bv.compute_lift_with_ci(0, 0, 0, 0)
        survived, reasons = bv.apply_survival_rules(train, holdout, LIFT_MIN, CI_MIN)
        self.assertFalse(survived)
        self.assertEqual(reasons, ["train lift undefined"])

    def test_infinite_train_lift_does_not_survive(self):
        train = {"lift": float("inf"), "ci_lower": None, "method": "wald_log_lift"}
        survived, reasons = bv.apply_survival_rules(
            train, strong_train(), LIFT_MIN, CI_MIN)
        self.assertFalse(survived)
        self.assertEqual(reasons, ["train lift undefined"])

    def test_nan_holdout_lift_does_not_survive(self):
        holdout = bv.compute_lift_with_ci(0, 0, 0, 0)
        survived, reasons = bv.apply_survival_rules(
            strong_train(), holdout, LIFT_MIN, CI_MIN)
        self.assertFalse(survived)
        self.assertIn("holdout lift undefined", reasons)

    def test_a_real_table_still_survives(self):
        train = {"lift": 3.0, "ci_lower": 1.5, "method": "wald_log_lift"}
        holdout = {"lift": 2.8, "ci_lower": 1.4, "method": "wald_log_lift"}
        survived, reasons = bv.apply_survival_rules(train, holdout, LIFT_MIN, CI_MIN)
        self.assertTrue(survived, reasons)
        self.assertEqual(reasons, [])


FIELDS = [("firecrawl_map", "has_careers_page", None),
          ("apify_linkedin", "employees_count", {"op": "gte", "value": 10}),
          ("exa", "count", {"op": "gte", "value": 10})]


def rubric(*weights) -> dict:
    sigs = []
    for i, w in enumerate(weights):
        src, field, pred = FIELDS[i]
        s = {"signal": "sig%d" % i, "observability_source": src, "queryable_field": field,
             "weight": w}
        if pred:
            s["predicate"] = pred
        sigs.append(s)
    return {"signals": sigs}


def panel(**sources) -> dict:
    """sources: source_key -> True (present) / False (absent) / None (failed)."""
    sp = {}
    for src, field, pred in FIELDS:
        if src not in sources:
            continue
        val = sources[src]
        if val is None:
            sp[src] = {"source": src, "success": False, "data": None, "error": "API key not set"}
        else:
            v = (50 if val else 2) if pred else bool(val)
            sp[src] = {"source": src, "success": True, "data": {field: v}, "error": None}
    return {"customer_id": "acmelegal.example", "outcome": "won", "signal_panel": sp}


class ScorePanelUnknownTests(unittest.TestCase):
    def test_unknown_is_excluded_from_the_denominator(self):
        """One of two signals never ran; the other one hit. That is 1.0, not 0.5."""
        score, _ = cd.score_panel(rubric(0.5, 0.5),
                                  panel(firecrawl_map=True, apify_linkedin=None))
        self.assertAlmostEqual(score, 1.0)

    def test_absent_still_counts_against_the_panel(self):
        score, _ = cd.score_panel(rubric(0.5, 0.5),
                                  panel(firecrawl_map=True, apify_linkedin=False))
        self.assertAlmostEqual(score, 0.5)

    def test_diff_records_the_tri_state(self):
        _, diff = cd.score_panel(
            rubric(0.4, 0.3, 0.3),
            panel(firecrawl_map=True, apify_linkedin=False, exa=None))
        self.assertEqual([d["state"] for d in diff], ["present", "absent", "unknown"])
        self.assertEqual([d["present"] for d in diff], [True, False, False])

    def test_all_unknown_scores_zero_and_does_not_divide_by_zero(self):
        score, diff = cd.score_panel(rubric(0.5, 0.5),
                                     panel(firecrawl_map=None, apify_linkedin=None))
        self.assertEqual(score, 0.0)
        self.assertEqual([d["state"] for d in diff], ["unknown", "unknown"])

    def test_all_observed_score_matches_plain_weight_sum(self):
        score, _ = cd.score_panel(rubric(0.6, 0.4),
                                  panel(firecrawl_map=True, apify_linkedin=False))
        self.assertAlmostEqual(score, 0.6)


if __name__ == "__main__":
    unittest.main()
