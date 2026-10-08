"""Tests for jev_client — pure logic only, no network calls.

Run: python3 -m pytest -q skills/calibrated-judge/tests/test_jev_client.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import jev_client as jc  # noqa: E402


# --------------------------------------------------------------------------
# normalize_questions
# --------------------------------------------------------------------------


def test_noul_aliases_collapse_to_wire_vocabulary():
    for alias in ("noul", "boolean", "bool", "BOOLEAN"):
        wire = jc.normalize_questions({"q": {"type": alias, "instructions": "Did X?"}})
        assert wire["q"]["type"] == "noul", alias

    # The Vercel AI SDK wants the same primitive spelled `boolean`.
    wire = jc.normalize_questions(
        {"q": {"type": "noul", "instructions": "Did X?"}}, wire_noul="boolean"
    )
    assert wire["q"]["type"] == "boolean"


def test_choice_and_score_types_are_not_renamed_by_wire_noul():
    wire = jc.normalize_questions(
        {
            "c": {"type": "choice", "instructions": "Route it.", "criteria": {"a": "A", "b": "B"}},
            "s": {"type": "score", "instructions": "Rate it.", "criteria": ["low", "high"]},
        },
        wire_noul="boolean",
    )
    assert wire["c"]["type"] == "choice"
    assert wire["s"]["type"] == "score"


def test_choice_requires_a_dict_of_at_least_two_options():
    with pytest.raises(ValueError, match="choice needs 'criteria'"):
        jc.normalize_questions(
            {"c": {"type": "choice", "instructions": "Route it.", "criteria": ["a", "b"]}}
        )
    with pytest.raises(ValueError, match="choice needs 'criteria'"):
        jc.normalize_questions(
            {"c": {"type": "choice", "instructions": "Route it.", "criteria": {"only": "one"}}}
        )


def test_score_requires_an_ordered_list_of_at_least_two_labels():
    with pytest.raises(ValueError, match="score needs 'criteria'"):
        jc.normalize_questions(
            {"s": {"type": "score", "instructions": "Rate it.", "criteria": {"a": "A"}}}
        )
    with pytest.raises(ValueError, match="score needs 'criteria'"):
        jc.normalize_questions(
            {"s": {"type": "score", "instructions": "Rate it.", "criteria": ["only"]}}
        )


def test_score_criteria_order_is_preserved():
    labels = ["poor", "fair", "good", "excellent"]
    wire = jc.normalize_questions(
        {"s": {"type": "score", "instructions": "Rate it.", "criteria": labels}}
    )
    assert wire["s"]["criteria"] == labels


def test_missing_or_blank_instructions_is_rejected():
    with pytest.raises(ValueError, match="'instructions' is required"):
        jc.normalize_questions({"q": {"type": "noul"}})
    with pytest.raises(ValueError, match="'instructions' is required"):
        jc.normalize_questions({"q": {"type": "noul", "instructions": "   "}})


def test_unknown_question_type_is_rejected():
    with pytest.raises(ValueError, match="unknown type"):
        jc.normalize_questions({"q": {"type": "freetext", "instructions": "Say something"}})


def test_empty_questions_is_rejected():
    with pytest.raises(ValueError, match="non-empty dict"):
        jc.normalize_questions({})


def test_optional_noul_criteria_is_passed_through():
    wire = jc.normalize_questions(
        {
            "passed": {
                "type": "boolean",
                "instructions": "Did the build succeed?",
                "criteria": {"true": "exit code 0", "false": "any non-zero exit code"},
            }
        }
    )
    assert wire["passed"]["criteria"] == {"true": "exit code 0", "false": "any non-zero exit code"}


# --------------------------------------------------------------------------
# normalize_answer — one shape out of three backend dialects
# --------------------------------------------------------------------------


def test_openrouter_noul_answer_normalizes_to_probability():
    # Exactly the payload the live OpenRouter probe returned.
    out = jc.normalize_answer({"type": "noul", "noul": 0.98})
    assert out["type"] == "noul"
    assert out["probability"] == pytest.approx(0.98)


def test_ai_sdk_boolean_answer_normalizes_to_the_same_shape():
    out = jc.normalize_answer({"type": "boolean", "probability": 0.99})
    assert out["type"] == "noul"
    assert out["probability"] == pytest.approx(0.99)


def test_choice_answer_keeps_choice_and_probabilities():
    out = jc.normalize_answer(
        {
            "type": "choice",
            "choice": "billing",
            "probabilities": {"billing": 1, "shipping": 0, "technical": 0},
        }
    )
    assert out["choice"] == "billing"
    assert out["probabilities"]["billing"] == 1


def test_score_answer_keeps_score_and_probabilities():
    out = jc.normalize_answer(
        {"type": "score", "score": 2.97, "probabilities": {"2": 0.02, "3": 0.98}}
    )
    assert out["type"] == "score"
    assert out["score"] == pytest.approx(2.97)


def test_unparseable_probability_becomes_none_rather_than_raising():
    out = jc.normalize_answer({"type": "noul", "noul": "not-a-number"})
    assert out["probability"] is None


def test_unknown_answer_type_is_preserved_as_raw():
    out = jc.normalize_answer({"type": "mystery", "value": 1})
    assert out["type"] == "mystery"
    assert out["raw"]["value"] == 1


# --------------------------------------------------------------------------
# cache_key — stability is the whole point
# --------------------------------------------------------------------------


def test_cache_key_ignores_dict_ordering():
    a = jc.cache_key("s", {"x": {"type": "noul", "instructions": "i"}}, "m")
    b = jc.cache_key("s", {"x": {"instructions": "i", "type": "noul"}}, "m")
    assert a == b


def test_cache_key_changes_with_state_questions_and_model():
    base = jc.cache_key("s", {"x": {"type": "noul", "instructions": "i"}}, "m")
    assert base != jc.cache_key("s2", {"x": {"type": "noul", "instructions": "i"}}, "m")
    assert base != jc.cache_key("s", {"x": {"type": "noul", "instructions": "i2"}}, "m")
    assert base != jc.cache_key("s", {"x": {"type": "noul", "instructions": "i"}}, "m2")


def test_cache_key_is_sha256_hex():
    key = jc.cache_key("s", {"x": {"type": "noul", "instructions": "i"}}, "m")
    assert len(key) == 64
    int(key, 16)  # raises if not hex


def test_cache_key_handles_structured_state():
    key = jc.cache_key(
        {"order": {"id": "A-1", "total": 42.5}}, {"x": {"type": "noul", "instructions": "i"}}, "m"
    )
    assert len(key) == 64


# --------------------------------------------------------------------------
# JsonlCache
# --------------------------------------------------------------------------


def test_cache_roundtrip(tmp_path):
    cache = jc.JsonlCache(tmp_path / "c.jsonl")
    assert cache.get("k") is None
    cache.put("k", {"answers": {"q": {"type": "noul", "probability": 0.5}}})
    assert cache.get("k")["answers"]["q"]["probability"] == 0.5


def test_cache_last_write_wins(tmp_path):
    cache = jc.JsonlCache(tmp_path / "c.jsonl")
    cache.put("k", {"v": 1})
    cache.put("k", {"v": 2})
    assert cache.get("k")["v"] == 2


def test_cache_tolerates_a_torn_trailing_line(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(
        json.dumps({"key": "k", "value": {"v": 1}}) + "\n" + '{"key": "k2", "val',
        encoding="utf-8",
    )
    cache = jc.JsonlCache(path)
    assert cache.get("k")["v"] == 1
    assert cache.get("k2") is None


def test_disabled_cache_never_reads_or_writes(tmp_path):
    path = tmp_path / "c.jsonl"
    cache = jc.JsonlCache(path, enabled=False)
    cache.put("k", {"v": 1})
    assert not path.exists()
    assert cache.get("k") is None


def test_cache_creates_parent_directories(tmp_path):
    cache = jc.JsonlCache(tmp_path / "nested" / "deeper" / "c.jsonl")
    cache.put("k", {"v": 1})
    assert cache.get("k")["v"] == 1


# --------------------------------------------------------------------------
# ask() orchestration — backend fallback, caching, validation
# --------------------------------------------------------------------------


class _StubBackend(jc.Backend):
    name = "stub"

    def __init__(self, calls):
        self.calls = calls

    def ask(self, state, questions, model, timeout):
        self.calls.append(state)
        return {
            "answers": {"q": jc.normalize_answer({"type": "noul", "noul": 0.7})},
            "usage": {"input_tokens": 10},
            "model": "stub-1",
            "latency_ms": 5,
            "backend": self.name,
        }


def test_ask_falls_through_unavailable_backends_to_a_working_one(tmp_path, monkeypatch):
    calls: list = []

    class Unavailable(jc.Backend):
        name = "nope"

        def ask(self, *a, **k):
            raise jc.BackendUnavailable("no key")

    monkeypatch.setitem(jc.BACKENDS, "nope", Unavailable)
    monkeypatch.setitem(jc.BACKENDS, "stub", lambda: _StubBackend(calls))

    result = jc.ask(
        "state",
        {"q": {"type": "noul", "instructions": "i"}},
        backends=("nope", "stub"),
        cache=jc.JsonlCache(tmp_path / "c.jsonl"),
    )
    assert result["backend"] == "stub"
    assert result["cached"] is False
    assert result["attempts"][0]["backend"] == "nope"
    assert "unavailable" in result["attempts"][0]["result"]


def test_ask_serves_the_second_identical_call_from_cache(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setitem(jc.BACKENDS, "stub", lambda: _StubBackend(calls))
    cache = jc.JsonlCache(tmp_path / "c.jsonl")
    questions = {"q": {"type": "noul", "instructions": "i"}}

    first = jc.ask("state", questions, backends=("stub",), cache=cache)
    second = jc.ask("state", questions, backends=("stub",), cache=cache)

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(calls) == 1, "the cached call must not hit the backend"


def test_cached_hit_is_shared_across_noul_and_boolean_spellings(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setitem(jc.BACKENDS, "stub", lambda: _StubBackend(calls))
    cache = jc.JsonlCache(tmp_path / "c.jsonl")

    jc.ask("s", {"q": {"type": "noul", "instructions": "i"}}, backends=("stub",), cache=cache)
    out = jc.ask(
        "s", {"q": {"type": "boolean", "instructions": "i"}}, backends=("stub",), cache=cache
    )
    assert out["cached"] is True
    assert len(calls) == 1


def test_ask_raises_with_every_blocker_named_when_all_backends_fail(tmp_path, monkeypatch):
    class A(jc.Backend):
        def ask(self, *a, **k):
            raise jc.BackendUnavailable("A has no key")

    class B(jc.Backend):
        def ask(self, *a, **k):
            raise jc.BackendFailed("B returned 403")

    monkeypatch.setitem(jc.BACKENDS, "a", A)
    monkeypatch.setitem(jc.BACKENDS, "b", B)

    with pytest.raises(jc.JevError) as excinfo:
        jc.ask(
            "s",
            {"q": {"type": "noul", "instructions": "i"}},
            backends=("a", "b"),
            cache=jc.JsonlCache(tmp_path / "c.jsonl"),
        )
    msg = str(excinfo.value)
    assert "A has no key" in msg
    assert "B returned 403" in msg


def test_ask_validates_questions_before_touching_the_network(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setitem(jc.BACKENDS, "stub", lambda: _StubBackend(calls))
    with pytest.raises(ValueError):
        jc.ask(
            "s",
            {"q": {"type": "freetext", "instructions": "i"}},
            backends=("stub",),
            cache=jc.JsonlCache(tmp_path / "c.jsonl"),
        )
    assert calls == [], "a malformed question must not reach a backend"


# --------------------------------------------------------------------------
# Retry / backoff
# --------------------------------------------------------------------------


def test_http_post_retries_on_429_then_succeeds(monkeypatch):
    attempts = {"n": 0}
    slept: list[float] = []

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.error as ue

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ue.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        return FakeResponse()

    monkeypatch.setattr(jc.urllib.request, "urlopen", fake_urlopen)
    status, body, _ = jc.http_post_json(
        "https://example.test", {}, {}, sleep=slept.append
    )
    assert status == 200
    assert attempts["n"] == 2
    assert len(slept) == 1, "exactly one backoff sleep between two attempts"


def test_http_post_retries_on_529(monkeypatch):
    attempts = {"n": 0}
    import urllib.error as ue

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        raise ue.HTTPError(req.full_url, 529, "Overloaded", {}, None)

    monkeypatch.setattr(jc.urllib.request, "urlopen", fake_urlopen)
    status, _, _ = jc.http_post_json(
        "https://example.test", {}, {}, max_attempts=3, sleep=lambda s: None
    )
    assert status == 529
    assert attempts["n"] == 3, "529 is retried up to max_attempts"


def test_http_post_does_not_retry_a_400(monkeypatch):
    attempts = {"n": 0}
    import urllib.error as ue

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        raise ue.HTTPError(req.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(jc.urllib.request, "urlopen", fake_urlopen)
    status, _, _ = jc.http_post_json(
        "https://example.test", {}, {}, max_attempts=4, sleep=lambda s: None
    )
    assert status == 400
    assert attempts["n"] == 1, "a 400 is the caller's fault; retrying wastes money"


def test_http_post_backoff_grows_exponentially(monkeypatch):
    slept: list[float] = []
    import urllib.error as ue

    def fake_urlopen(req, timeout=None):
        raise ue.HTTPError(req.full_url, 429, "Too Many", {}, None)

    monkeypatch.setattr(jc.urllib.request, "urlopen", fake_urlopen)
    jc.http_post_json("https://example.test", {}, {}, max_attempts=4, sleep=slept.append)
    assert len(slept) == 3
    # 1s, 2s, 4s plus up to 0.25s jitter each
    assert 1.0 <= slept[0] < 1.3
    assert 2.0 <= slept[1] < 2.3
    assert 4.0 <= slept[2] < 4.3


# --------------------------------------------------------------------------
# Secret hygiene
# --------------------------------------------------------------------------


def test_redact_scrubs_a_secret_that_leaked_into_a_response_body():
    secret = "sk-or-v1-abcdef0123456789"
    body = f"unauthorized key {secret} rejected"
    out = jc.redact(body, [secret])
    assert secret not in out
    assert "[REDACTED]" in out


def test_redact_ignores_short_strings_to_avoid_mangling_everything():
    out = jc.redact("the answer is 0.98", ["0.98"])
    assert out == "the answer is 0.98"


def test_redact_truncates_to_the_limit():
    assert len(jc.redact("x" * 5000, [], limit=300)) == 300


def test_env_file_parser_handles_quotes_export_and_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "PLAIN=value1",
                'QUOTED="value2"',
                "SINGLE='value3'",
                "export EXPORTED=value4",
                "SPACED = value5 ",
                "NOT_A_PAIR",
            ]
        ),
        encoding="utf-8",
    )
    env = jc.load_env_file(path)
    assert env["PLAIN"] == "value1"
    assert env["QUOTED"] == "value2"
    assert env["SINGLE"] == "value3"
    assert env["EXPORTED"] == "value4"
    assert env["SPACED"] == "value5"
    assert "NOT_A_PAIR" not in env


def test_env_file_parser_returns_empty_when_file_is_absent(tmp_path):
    assert jc.load_env_file(tmp_path / "nope.env") == {}


def test_real_environment_wins_over_env_file(monkeypatch):
    monkeypatch.setenv("JEV_TEST_KEY", "from-environ")
    assert jc.get_secret("JEV_TEST_KEY", {"JEV_TEST_KEY": "from-dotenv"}) == "from-environ"


def test_get_secret_falls_back_to_env_file(monkeypatch):
    monkeypatch.delenv("JEV_TEST_KEY", raising=False)
    assert jc.get_secret("JEV_TEST_KEY", {"JEV_TEST_KEY": "from-dotenv"}) == "from-dotenv"


def test_get_secret_returns_none_when_absent_everywhere(monkeypatch):
    monkeypatch.delenv("JEV_TEST_KEY", raising=False)
    assert jc.get_secret("JEV_TEST_KEY", {}) is None


# --------------------------------------------------------------------------
# Backends refuse cleanly when their credential is missing
# --------------------------------------------------------------------------


def test_vercel_backend_names_the_right_key_and_rejects_vercel_token(monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    monkeypatch.setattr(jc, "load_env_file", lambda path=None: {"VERCEL_TOKEN": "irrelevant"})
    with pytest.raises(jc.BackendUnavailable) as excinfo:
        jc.VercelBackend().ask("s", {"q": {"type": "noul", "instructions": "i"}}, None, 5)
    msg = str(excinfo.value)
    assert "AI_GATEWAY_API_KEY" in msg
    assert "VERCEL_TOKEN does not work" in msg


def test_typesafe_backend_reports_the_missing_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_AI_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(jc, "load_env_file", lambda path=None: {})
    with pytest.raises(jc.BackendUnavailable, match="TYPESAFE_AI_API_KEY"):
        jc.TypeSafeBackend().ask("s", {"q": {"type": "noul", "instructions": "i"}}, None, 5)


def test_openrouter_backend_reports_the_missing_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(jc, "load_env_file", lambda path=None: {})
    with pytest.raises(jc.BackendUnavailable, match="OPENROUTER_API_KEY"):
        jc.OpenRouterBackend().ask("s", {"q": {"type": "noul", "instructions": "i"}}, None, 5)


def test_openrouter_sends_noul_on_the_wire_and_parses_the_live_shape(monkeypatch):
    """Locks the exact request/response contract proven against the live API."""
    captured = {}

    def fake_post(url, body, headers, timeout=60, max_attempts=4, sleep=None):
        captured["url"] = url
        captured["body"] = body
        captured["auth"] = headers.get("Authorization", "")
        return (
            200,
            json.dumps(
                {
                    "model": "typesafe/jev-1.13-20260917",
                    "answers": {"refund": {"type": "noul", "noul": 0.98}},
                    "usage": {"input_tokens": 281, "output_tokens": 20, "cost": 0.000011802},
                    "id": "gen-dec-1",
                    "provider": "TypeSafe",
                }
            ),
            610,
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-1234567890")
    monkeypatch.setattr(jc, "http_post_json", fake_post)

    out = jc.OpenRouterBackend().ask(
        "The customer asked for a refund twice.",
        {"refund": {"type": "boolean", "instructions": "Did the customer request a refund?"}},
        None,
        30,
    )

    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["body"]["model"] == "typesafe/jev-1.13"
    # boolean must be translated to noul for this backend
    assert captured["body"]["questions"]["refund"]["type"] == "noul"
    assert captured["auth"].startswith("Bearer ")
    assert out["backend"] == "openrouter"
    assert out["model"] == "typesafe/jev-1.13-20260917"
    assert out["answers"]["refund"]["probability"] == pytest.approx(0.98)
    assert out["usage"]["cost"] == pytest.approx(0.000011802)


def test_openrouter_raises_backendfailed_on_the_chat_completions_mistake(monkeypatch):
    """The live 400 we got from chat/completions must surface, not be swallowed."""

    def fake_post(url, body, headers, timeout=60, max_attempts=4, sleep=None):
        return (
            400,
            json.dumps(
                {
                    "error": {
                        "message": "typesafe/jev-1.13 is a decisions model and cannot be "
                        "used with the chat/completions endpoint.",
                        "code": 400,
                    }
                }
            ),
            50,
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-1234567890")
    monkeypatch.setattr(jc, "http_post_json", fake_post)
    with pytest.raises(jc.BackendFailed) as excinfo:
        jc.OpenRouterBackend().ask("s", {"q": {"type": "noul", "instructions": "i"}}, None, 30)
    assert excinfo.value.status == 400
    assert "decisions model" in str(excinfo.value)


def test_openrouter_never_echoes_the_key_into_an_error(monkeypatch):
    secret = "sk-or-v1-supersecret-0123456789"

    def fake_post(url, body, headers, timeout=60, max_attempts=4, sleep=None):
        return (401, f"invalid key {secret}", 10)

    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    monkeypatch.setattr(jc, "http_post_json", fake_post)
    with pytest.raises(jc.BackendFailed) as excinfo:
        jc.OpenRouterBackend().ask("s", {"q": {"type": "noul", "instructions": "i"}}, None, 30)
    assert secret not in str(excinfo.value)


# --------------------------------------------------------------------------
# CLI argument parsing
# --------------------------------------------------------------------------


def test_parse_question_arg():
    qid, spec = jc.parse_question_arg("refunded=noul:Was a refund issued?")
    assert qid == "refunded"
    assert spec == {"type": "noul", "instructions": "Was a refund issued?"}


def test_parse_question_arg_rejects_malformed_input():
    with pytest.raises(ValueError, match="id=type:instructions"):
        jc.parse_question_arg("refunded-noul-nope")
    with pytest.raises(ValueError, match="missing instructions"):
        jc.parse_question_arg("refunded=noul")


def test_shared_flags_work_after_the_subcommand():
    """`probe --backend openrouter` must parse, matching the documented usage."""
    parser_args = jc.main.__wrapped__ if hasattr(jc.main, "__wrapped__") else None
    assert parser_args is None  # main is plain; exercise it through parse below

    # Reach into main's parser indirectly by calling with a no-op command path.
    # A SystemExit here would mean the flag placement is rejected.
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):
        with pytest.raises(SystemExit) as excinfo:
            jc.main(["probe", "--backend", "openrouter", "--help"])
    assert excinfo.value.code == 0, "--help after the subcommand should exit cleanly"
