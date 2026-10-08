"""A Score's rungs come back keyed by index with a legend; callers read NAMES.

Measured live against api.typesafe.ai (`jev-1.13.0`). Before this,
`composer._level(values, "same_organization", "accept_candidate")` read None on
every row, so the v2 real_website battery could never accept anything.
"""
import pytest

from bakeoff import repo

jev_client = repo.try_import("jev_client", repo.SKILL_DIR / "scripts")


@pytest.mark.skipif(jev_client is None, reason="jev_client not importable")
def test_score_probabilities_are_renamed_through_the_legend():
    out = jev_client.normalize_answer({
        "type": "score", "score": 1.77, "confidence": 0.66,
        "legend": {"0": "reject_candidate", "1": "review_candidate",
                   "2": "accept_candidate"},
        "probabilities": {"0": 0.04, "1": 0.15, "2": 0.81},
    })
    assert out["probabilities"] == {"reject_candidate": 0.04,
                                    "review_candidate": 0.15,
                                    "accept_candidate": 0.81}
    assert out["score"] == pytest.approx(1.77)
    assert out["raw"]["probabilities"] == {"0": 0.04, "1": 0.15, "2": 0.81}


@pytest.mark.skipif(jev_client is None, reason="jev_client not importable")
def test_score_without_a_legend_keeps_its_own_keys():
    out = jev_client.normalize_answer({
        "type": "score", "score": 2.97,
        "probabilities": {"2": 0.02, "3": 0.98},
    })
    assert out["probabilities"] == {"2": 0.02, "3": 0.98}


@pytest.mark.skipif(jev_client is None, reason="jev_client not importable")
def test_a_legend_that_misses_a_rung_leaves_that_rung_addressable():
    out = jev_client.normalize_answer({
        "type": "score", "score": 1.0,
        "legend": {"0": "low"},
        "probabilities": {"0": 0.3, "1": 0.7},
    })
    assert out["probabilities"] == {"low": 0.3, "1": 0.7}
