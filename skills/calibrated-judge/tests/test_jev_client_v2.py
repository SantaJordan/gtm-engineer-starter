"""Client changes v2 needs: the returned confidence must survive, and the
cache key must not let a different backend/model/battery reuse an old answer."""
import pytest

import jev_client as J


# ------------------------------------------------------------- confidence
def test_a_choice_answer_exposes_its_winning_option_probability():
    out = J.normalize_answer({"type": "choice", "choice": "parent",
                              "probabilities": {"parent": 0.83,
                                                "subsidiary": 0.17}})
    assert out["confidence"] == 0.83


def test_a_score_answer_exposes_its_maximum_rung_probability():
    out = J.normalize_answer({"type": "score", "score": 1.8,
                              "probabilities": {"low": 0.1, "mid": 0.2,
                                                "high": 0.7}})
    assert out["confidence"] == 0.7


def test_a_score_keeps_its_own_score_and_is_never_a_probability_of_true():
    out = J.normalize_answer({"type": "score", "score": 1.8,
                              "probabilities": {"low": 0.1, "high": 0.9}})
    assert out["score"] == 1.8
    assert "probability" not in out


def test_a_noul_has_no_separate_confidence_field():
    out = J.normalize_answer({"type": "noul", "noul": 0.91})
    assert out["probability"] == 0.91
    assert out.get("confidence") is None


def test_a_choice_with_no_probabilities_has_no_invented_confidence():
    out = J.normalize_answer({"type": "choice", "choice": "parent"})
    assert out["confidence"] is None


def test_the_raw_response_is_still_carried_through():
    raw = {"type": "choice", "choice": "parent", "probabilities": {"parent": 0.8}}
    assert J.normalize_answer(raw)["raw"] == raw


# ---------------------------------------------------------------- cache key
QS = {"q": {"type": "noul", "instructions": "Is it so?"}}


def test_the_cache_key_separates_two_backends():
    a = J.cache_key("s", QS, "m", backends=("openrouter",))
    b = J.cache_key("s", QS, "m", backends=("vercel",))
    assert a != b


def test_the_cache_key_separates_two_models():
    a = J.cache_key("s", QS, "model-a", backends=("openrouter",))
    b = J.cache_key("s", QS, "model-b", backends=("openrouter",))
    assert a != b


def test_the_cache_key_separates_two_battery_versions():
    a = J.cache_key("s", QS, "m", backends=("openrouter",),
                    scope={"battery_version": "v1", "composer_version": "v1"})
    b = J.cache_key("s", QS, "m", backends=("openrouter",),
                    scope={"battery_version": "v2", "composer_version": "v1"})
    assert a != b


def test_the_cache_key_separates_two_composer_versions():
    a = J.cache_key("s", QS, "m", scope={"composer_version": "v1"})
    b = J.cache_key("s", QS, "m", scope={"composer_version": "v2"})
    assert a != b


def test_the_cache_key_is_stable_for_the_same_triple():
    a = J.cache_key("s", QS, "m", backends=("openrouter",), scope={"x": 1})
    b = J.cache_key("s", QS, "m", backends=("openrouter",), scope={"x": 1})
    assert a == b


def test_ask_passes_its_scope_into_the_key(monkeypatch):
    seen = []

    class Cache:
        enabled = True

        def get(self, key):
            seen.append(key)
            return {"answers": {}, "usage": {}, "model": "m", "backend": "fake"}

        def put(self, key, value):
            pass

    J.ask("s", QS, model="m", cache=Cache(), cache_scope={"battery_version": "v2"})
    J.ask("s", QS, model="m", cache=Cache(), cache_scope={"battery_version": "v1"})
    assert seen[0] != seen[1]
