#!/usr/bin/env python3
"""Calibrated-judge client for TypeSafe's Jev "System One" evaluation model.

Jev answers *typed questions* about a piece of shared state and returns
calibrated probabilities instead of prose. Three question types exist:

    noul    -> P(true), a single float in [0, 1]     (the AI SDK calls this `boolean`)
    choice  -> one option from a named set + per-option probabilities
    score   -> an interpolated score on an ordered scale + per-rung probabilities

Three backends serve the same model. The client tries them in order and
skips any backend whose key is not set:

    typesafe    POST https://api.typesafe.ai/v1/systemone
                       Bearer TYPESAFE_API_KEY (or TYPESAFE_AI_API_KEY),
                       model `jev-1.13.0`. Get a key at console.typesafe.ai.
                       Without a key the endpoint answers 403 "Must supply an
                       API key!". The score-legend handling below was measured
                       live against this endpoint.
    openrouter  POST https://openrouter.ai/api/alpha/decisions
                       Bearer OPENROUTER_API_KEY, model `typesafe/jev-1.13`.
                       Note: chat/completions REJECTS this model with
                       "is a decisions model ... Use the /api/alpha/decisions
                       endpoint instead" — the decisions endpoint is the door.
    vercel      AI Gateway lists `typesafe-ai/jev`, but evaluation is exposed
                       through the AI SDK only (no REST route). It needs a
                       dedicated AI_GATEWAY_API_KEY plus the node `ai` package.
                       Treat it as unsupported in this starter.

This module reads keys that already exist in the environment or the repo .env
and never creates, rotates, prints or logs one.

Usage:
    python3 jev_client.py probe
    python3 jev_client.py probe --backend openrouter --no-cache
    python3 jev_client.py ask --state "..." --question 'id=noul:Did X happen?'
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

#: Backend attempt order when the caller does not pin one.
BACKEND_ORDER = ("typesafe", "openrouter", "vercel")

#: The model id each backend knows this model by.
MODEL_IDS = {
    "openrouter": "typesafe/jev-1.13",
    "vercel": "typesafe-ai/jev",
    "typesafe": "jev-1.13.0",
}

#: `noul` is TypeSafe's native name for a P(true) question. The Vercel AI SDK
#: surfaces the same primitive as `boolean`, so both spellings are accepted on
#: input and normalized to `noul` on output.
NOUL_ALIASES = ("noul", "boolean", "bool")
QUESTION_TYPES = ("noul", "choice", "score")

RETRY_STATUSES = (429, 529)
MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 1.0

DEFAULT_TIMEOUT_S = 60


#: Override for the repo root (where `.env` and `outputs/` live).
ENV_ROOT = "GTM_STARTER_ROOT"
#: A file that marks the starter repo root.
ROOT_MARKER = "START-HERE.md"


def blueprint_root() -> Path:
    """Repo root: $GTM_STARTER_ROOT, else the nearest parent of this file that
    holds START-HERE.md, else the current directory. Never a home-dir guess."""
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ROOT_MARKER).is_file():
            return parent
    return Path.cwd()


def default_cache_path() -> Path:
    override = os.environ.get("JEV_CACHE_PATH")
    if override:
        return Path(override).expanduser()
    # outputs/ is gitignored, so cached answers never reach a commit.
    return blueprint_root() / "outputs" / "calibrated-judge" / "jev-cache.jsonl"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class JevError(RuntimeError):
    """Base error."""


class BackendUnavailable(JevError):
    """A backend cannot be attempted at all (missing credential, missing dep)."""


class BackendFailed(JevError):
    """A backend was attempted and refused the request."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


# --------------------------------------------------------------------------
# Secrets: read what exists, never emit it
# --------------------------------------------------------------------------


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse KEY=VALUE lines from .env. Stdlib only; python-dotenv is optional.

    Values are returned for use, never printed by this module.
    """
    path = path or (blueprint_root() / ".env")
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def get_secret(name: str, env_file: dict[str, str] | None = None) -> str | None:
    """Real environment wins over .env. Returns None when absent."""
    val = os.environ.get(name)
    if val:
        return val
    if env_file is None:
        env_file = load_env_file()
    val = env_file.get(name)
    return val or None


def redact(text: str, secrets: list[str | None], limit: int = 300) -> str:
    """Truncate a response body and scrub any secret that leaked into it."""
    out = text or ""
    for s in secrets:
        if s and len(s) >= 8:
            out = out.replace(s, "[REDACTED]")
    return out[:limit]


# --------------------------------------------------------------------------
# Question / answer normalization
# --------------------------------------------------------------------------


def normalize_questions(questions: dict[str, dict], wire_noul: str = "noul") -> dict[str, dict]:
    """Validate questions and render them in a backend's wire vocabulary.

    `wire_noul` is the spelling that backend wants for a P(true) question:
    "noul" for TypeSafe/OpenRouter, "boolean" for the Vercel AI SDK.
    """
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions must be a non-empty dict keyed by question id")

    out: dict[str, dict] = {}
    for qid, spec in questions.items():
        if not isinstance(spec, dict):
            raise ValueError(f"question {qid!r}: spec must be a dict")
        raw_type = str(spec.get("type", "")).strip().lower()
        if not raw_type:
            raise ValueError(f"question {qid!r}: missing 'type'")

        if raw_type in NOUL_ALIASES:
            qtype = "noul"
        elif raw_type in QUESTION_TYPES:
            qtype = raw_type
        else:
            raise ValueError(
                f"question {qid!r}: unknown type {raw_type!r}; "
                f"expected one of noul/boolean, choice, score"
            )

        instructions = spec.get("instructions")
        if not instructions or not str(instructions).strip():
            raise ValueError(f"question {qid!r}: 'instructions' is required")

        wire: dict[str, Any] = {
            "type": wire_noul if qtype == "noul" else qtype,
            "instructions": str(instructions),
        }

        criteria = spec.get("criteria")
        if qtype == "choice":
            if not isinstance(criteria, dict) or len(criteria) < 2:
                raise ValueError(
                    f"question {qid!r}: choice needs 'criteria' as a dict of "
                    f"at least 2 option->description pairs"
                )
            wire["criteria"] = dict(criteria)
        elif qtype == "score":
            if not isinstance(criteria, (list, tuple)) or len(criteria) < 2:
                raise ValueError(
                    f"question {qid!r}: score needs 'criteria' as an ordered "
                    f"list of at least 2 labels, lowest to highest"
                )
            wire["criteria"] = list(criteria)
        elif criteria is not None:
            # noul criteria is optional: {"true": "...", "false": "..."}
            if not isinstance(criteria, dict):
                raise ValueError(f"question {qid!r}: noul 'criteria' must be a dict")
            wire["criteria"] = dict(criteria)

        out[qid] = wire
    return out


def normalize_answer(raw: dict) -> dict:
    """Collapse each backend's answer dialect into one shape.

    noul   -> {"type": "noul",   "probability": float}
    choice -> {"type": "choice", "choice": str, "probabilities": dict}
    score  -> {"type": "score",  "score": float, "probabilities": dict}
    """
    if not isinstance(raw, dict):
        return {"type": "unknown", "raw": raw}

    atype = str(raw.get("type", "")).strip().lower()

    if atype in NOUL_ALIASES:
        prob = raw.get("noul")
        if prob is None:
            prob = raw.get("probability")
        if prob is None:
            prob = raw.get("p_true")
        return {"type": "noul", "probability": _as_float(prob), "raw": raw}

    if atype == "choice":
        probs = raw.get("probabilities") or {}
        choice = raw.get("choice")
        # PRESERVE the returned confidence. The review (finding 8) caught it
        # being buried in `raw`, so the arm had to re-derive it or ignore it.
        conf = _as_float(raw.get("confidence"))
        if conf is None and choice is not None:
            conf = _as_float(probs.get(choice))
        return {
            "type": "choice",
            "choice": choice,
            "probabilities": probs,
            "confidence": conf,
            "raw": raw,
        }

    if atype == "score":
        probs = _apply_legend(raw.get("probabilities") or {}, raw.get("legend"))
        # A Score's confidence is its maximum rung probability. It is NEVER
        # P(true): banding a score on the noul bands is a category error.
        conf = _as_float(raw.get("confidence"))
        if conf is None and probs:
            rungs = [_as_float(v) for v in probs.values()]
            rungs = [v for v in rungs if v is not None]
            conf = max(rungs) if rungs else None
        return {
            "type": "score",
            "score": _as_float(raw.get("score")),
            "probabilities": probs,
            "confidence": conf,
            "raw": raw,
        }

    return {"type": atype or "unknown", "raw": raw}


def _apply_legend(probs: dict, legend: Any) -> dict:
    """Score rungs come back keyed by RUNG INDEX with a `legend` naming each one.

    Measured live against api.typesafe.ai (`jev-1.13.0`): a score
    question declaring `["reject_candidate", "review_candidate",
    "accept_candidate"]` answers
    `{"probabilities": {"0": .04, "1": .15, "2": .81},
      "legend": {"0": "reject_candidate", ...}}`.
    Without this remap every caller that looks a LEVEL up by name reads None
    and a score can never decide anything. The legend is applied only when it
    covers a key; anything it does not name keeps its original key, and a
    backend that already returns named rungs is untouched.
    """
    if not isinstance(legend, dict) or not isinstance(probs, dict) or not probs:
        return probs
    out = {}
    for key, value in probs.items():
        name = legend.get(key, legend.get(str(key)))
        out[str(name) if name is not None else str(key)] = value
    return out


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def cache_key(
    state: Any,
    questions: dict,
    model: str,
    backends: tuple[str, ...] | list[str] | None = None,
    scope: dict | None = None,
) -> str:
    """sha256 over the canonical (state, questions, model, backends, scope) tuple.

    Key stability matters more than readability: dict ordering and whitespace
    must not produce a second entry for the same question.

    `backends` and `scope` are in the key because
    the old key used `model or "default"` and NOT the backend, so a default or
    fallback change could silently replay an answer produced by a different
    model on a different backend — and a battery/composer version change could
    reuse answers to questions that no longer exist. Anything that changes what
    was asked, or who answered it, belongs in the key.
    """
    payload = json.dumps(
        {
            "state": state,
            "questions": questions,
            "model": model,
            "backends": list(backends) if backends else None,
            "scope": scope or None,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# One in-memory index per cache file, shared by every JsonlCache instance in the
# process. `ask()` builds a fresh JsonlCache per call, and the shared cache file
# can grow to hundreds of MB, so re-reading the whole file per lookup crawls.
# The index is built once, then each get() reads only the bytes
# appended since the last read, so writes from other instances or processes are
# still seen.
_INDEX_LOCK = threading.Lock()
_INDEXES: dict[str, dict] = {}  # resolved path -> {"ino", "offset", "map"}
_READ_CHUNK = 8 * 1024 * 1024


class JsonlCache:
    """Append-only JSONL cache. Last write for a key wins."""

    def __init__(self, path: Path | None = None, enabled: bool = True):
        self.path = Path(path) if path else default_cache_path()
        self.enabled = enabled

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            index_key = str(self.path.resolve())
        except OSError:
            index_key = str(self.path.absolute())
        with _INDEX_LOCK:
            try:
                with self.path.open("rb") as fh:
                    st = os.fstat(fh.fileno())
                    entry = _INDEXES.get(index_key)
                    if entry is None or entry["ino"] != st.st_ino or st.st_size < entry["offset"]:
                        # first read, or the file was replaced/truncated: rebuild
                        entry = {"ino": st.st_ino, "offset": 0, "map": {}}
                        _INDEXES[index_key] = entry
                    if st.st_size > entry["offset"]:
                        self._read_new_lines(fh, entry)
            except OSError:
                return None  # missing or unreadable file: a miss, never an error
            hit = entry["map"].get(key)
        return copy.deepcopy(hit) if hit is not None else None

    @staticmethod
    def _read_new_lines(fh, entry: dict) -> None:
        """Index complete lines appended since entry["offset"].

        The offset only moves past a terminating newline, so a torn trailing
        write is re-read once its writer finishes the line.
        """
        fh.seek(entry["offset"])
        index = entry["map"]
        pending = b""
        while True:
            chunk = fh.read(_READ_CHUNK)
            if not chunk:
                break
            buf = pending + chunk
            end = buf.rfind(b"\n")
            if end < 0:
                pending = buf
                continue
            for raw in buf[:end].split(b"\n"):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue  # tolerate a corrupt line, as before
                if isinstance(row, dict) and "key" in row:
                    index[row["key"]] = row.get("value")
            entry["offset"] += end + 1
            pending = buf[end + 1:]

    def put(self, key: str, value: dict) -> None:
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "cached_at": time.time(), "value": value}) + "\n")
        except OSError:
            pass  # a cache that cannot write must not break a live call


# --------------------------------------------------------------------------
# HTTP with backoff
# --------------------------------------------------------------------------


def http_post_json(
    url: str,
    body: dict,
    headers: dict[str, str],
    timeout: int = DEFAULT_TIMEOUT_S,
    max_attempts: int = MAX_ATTEMPTS,
    sleep=time.sleep,
) -> tuple[int, str, int]:
    """POST JSON, retrying 429/529 and transient network errors.

    Returns (status, body_text, latency_ms). status -1 means no HTTP response.
    """
    data = json.dumps(body).encode("utf-8")
    last: tuple[int, str, int] = (-1, "no attempt made", 0)

    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                return resp.status, text, int((time.time() - started) * 1000)
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            last = (exc.code, text, int((time.time() - started) * 1000))
            retryable = exc.code in RETRY_STATUSES
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = (-1, f"{type(exc).__name__}: {exc}", int((time.time() - started) * 1000))
            retryable = True

        if not retryable or attempt == max_attempts:
            return last
        sleep(BACKOFF_BASE_S * (2 ** (attempt - 1)) + random.uniform(0, 0.25))

    return last


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


class Backend:
    name = "base"

    def ask(self, state: Any, questions: dict, model: str | None, timeout: int, max_attempts: int = MAX_ATTEMPTS) -> dict:
        raise NotImplementedError


class OpenRouterBackend(Backend):
    """VERIFIED WORKING.

    POST https://openrouter.ai/api/alpha/decisions
      Authorization: Bearer $OPENROUTER_API_KEY
      {"model": "typesafe/jev-1.13", "state": ..., "questions": {...}}
    -> {"model": "typesafe/jev-1.13-20260917",
        "answers": {"refund": {"type": "noul", "noul": 0.98}},
        "usage": {"input_tokens": 281, "output_tokens": 20, "cost": 1.18e-05},
        "id": "gen-dec-...", "provider": "TypeSafe"}
    """

    name = "openrouter"
    url = "https://openrouter.ai/api/alpha/decisions"

    def ask(self, state, questions, model, timeout, max_attempts=MAX_ATTEMPTS):
        key = get_secret("OPENROUTER_API_KEY")
        if not key:
            raise BackendUnavailable("OPENROUTER_API_KEY is not set (env or .env)")

        wire = normalize_questions(questions, wire_noul="noul")
        body = {"model": model or MODEL_IDS["openrouter"], "state": state, "questions": wire}
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get("JEV_HTTP_REFERER", "https://github.com/SantaJordan/gtm-engineer-starter"),
            "X-Title": "gtm-engineer-starter calibrated-judge",
        }
        status, text, latency = http_post_json(self.url, body, headers, timeout=timeout, max_attempts=max_attempts)
        if status != 200:
            raise BackendFailed(
                f"openrouter HTTP {status}: {redact(text, [key])}", status=status, body=text
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BackendFailed(f"openrouter returned non-JSON: {redact(text, [key])}") from exc

        return {
            "answers": {k: normalize_answer(v) for k, v in (payload.get("answers") or {}).items()},
            "usage": payload.get("usage") or {},
            "model": payload.get("model") or body["model"],
            "latency_ms": latency,
            "backend": self.name,
            "request_id": payload.get("id"),
            "provider": payload.get("provider"),
        }


class VercelBackend(Backend):
    """BLOCKED — needs a dedicated AI Gateway key.

    AI Gateway lists `typesafe-ai/jev` (type "evaluation", input $0.000000042/tok,
    output free), but evaluation is reachable through the AI SDK only:
    /v1/evaluate, /v1/decisions and /v1/systemone all return 404, and
    chat/completions rejects evaluation models by design.

    Auth needs a dedicated AI Gateway key. VERCEL_TOKEN is NOT accepted —
    the gateway answers 401 "Create an API key and set in AI_GATEWAY_API_KEY".

    This backend therefore drives Node + `ai@7`'s experimental_evaluate. It
    stays dormant until AI_GATEWAY_API_KEY (or VERCEL_OIDC_TOKEN) exists and
    `ai` is installed. Creating that key is your call, not this script's.
    """

    name = "vercel"

    def ask(self, state, questions, model, timeout, max_attempts=MAX_ATTEMPTS):
        key = get_secret("AI_GATEWAY_API_KEY") or get_secret("VERCEL_OIDC_TOKEN")
        if not key:
            raise BackendUnavailable(
                "AI_GATEWAY_API_KEY / VERCEL_OIDC_TOKEN is not set. VERCEL_TOKEN does "
                "not work here: the gateway returns 401 and asks for a dedicated AI "
                "Gateway key. Create one at vercel.com -> AI Gateway -> API Keys."
            )

        node_dir = os.environ.get("JEV_NODE_DIR") or str(Path(__file__).resolve().parent)
        if not (Path(node_dir) / "node_modules" / "ai").exists():
            raise BackendUnavailable(
                f"the `ai` package (v7+) is not installed in {node_dir}; "
                f"run `npm install ai@^7` there to enable the vercel backend"
            )

        wire = normalize_questions(questions, wire_noul="boolean")
        payload = {
            "model": model or MODEL_IDS["vercel"],
            "state": state,
            "questions": wire,
        }

        script = (
            "import { experimental_evaluate as evaluate } from 'ai';\n"
            "const input = JSON.parse(process.argv[2]);\n"
            "const r = await evaluate(input);\n"
            "process.stdout.write(JSON.stringify("
            "{ answers: r.answers, usage: r.usage, model: r.response?.modelId ?? input.model }));\n"
        )

        import subprocess  # local import: only the vercel path needs it

        script_path = Path(node_dir) / ".jev_evaluate.mjs"
        started = time.time()
        try:
            script_path.write_text(script, encoding="utf-8")
            env = dict(os.environ)
            env["AI_GATEWAY_API_KEY"] = key
            proc = subprocess.run(
                ["node", str(script_path), json.dumps(payload)],
                cwd=node_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendFailed(f"vercel node bridge failed: {type(exc).__name__}: {exc}") from exc
        finally:
            try:
                script_path.unlink()
            except OSError:
                pass

        latency = int((time.time() - started) * 1000)
        if proc.returncode != 0:
            raise BackendFailed(
                f"vercel node bridge exit {proc.returncode}: "
                f"{redact(proc.stderr, [key])}"
            )
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise BackendFailed(
                f"vercel bridge returned non-JSON: {redact(proc.stdout, [key])}"
            ) from exc

        return {
            "answers": {k: normalize_answer(v) for k, v in (result.get("answers") or {}).items()},
            "usage": result.get("usage") or {},
            "model": result.get("model") or payload["model"],
            "latency_ms": latency,
            "backend": self.name,
        }


class TypeSafeBackend(Backend):
    """TypeSafe's own API. Needs TYPESAFE_API_KEY (or TYPESAFE_AI_API_KEY).

    POST https://api.typesafe.ai/v1/systemone with no key returns 403
    {"detail":{"error_type":"authentication_error",
               "message":"Must supply an API key! Check your request and try again."}}

    The wire shape matches OpenRouter's decisions body (OpenRouter proxies this
    endpoint).
    """

    name = "typesafe"
    url = "https://api.typesafe.ai/v1/systemone"

    def ask(self, state, questions, model, timeout, max_attempts=MAX_ATTEMPTS):
        # Either explicit process-environment alias wins over legacy file lookup.
        # Otherwise a file's AI alias could select a different account ahead of
        # the caller's explicitly supplied TYPESAFE_API_KEY.
        key = (os.environ.get("TYPESAFE_AI_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
               or get_secret("TYPESAFE_AI_API_KEY") or get_secret("TYPESAFE_API_KEY"))
        if not key:
            raise BackendUnavailable(
                "TYPESAFE_API_KEY (or TYPESAFE_AI_API_KEY) is not set (env or .env); api.typesafe.ai "
                "answers 403 'Must supply an API key!' without one"
            )

        wire = normalize_questions(questions, wire_noul="noul")
        body = {"model": model or MODEL_IDS["typesafe"], "state": state, "questions": wire}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        status, text, latency = http_post_json(self.url, body, headers, timeout=timeout, max_attempts=max_attempts)
        if status != 200:
            raise BackendFailed(
                f"typesafe HTTP {status}: {redact(text, [key])}", status=status, body=text
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BackendFailed(f"typesafe returned non-JSON: {redact(text, [key])}") from exc

        return {
            "answers": {k: normalize_answer(v) for k, v in (payload.get("answers") or {}).items()},
            "usage": payload.get("usage") or {},
            "model": payload.get("model") or body["model"],
            "latency_ms": latency,
            "backend": self.name,
        }


BACKENDS: dict[str, type[Backend]] = {
    "openrouter": OpenRouterBackend,
    "vercel": VercelBackend,
    "typesafe": TypeSafeBackend,
}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def ask(
    state: Any,
    questions: dict[str, dict],
    model: str | None = None,
    backends: tuple[str, ...] | list[str] = BACKEND_ORDER,
    cache: JsonlCache | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
    cache_scope: dict | None = None,
    max_attempts: int | None = None,
) -> dict:
    """Ask Jev a set of typed questions about `state`.

    Returns {answers, usage, model, latency_ms, backend, cached, attempts}.
    Tries each backend in order and raises JevError only if all of them fail.
    """
    # Budgeted callers can own each retry and its durable reservation. Existing
    # callers retain the transport's original retry policy when this is omitted.
    if max_attempts is not None and (isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1):
        raise ValueError("max_attempts must be a positive integer")
    # Validate once, up front, so a bad question fails before any network call.
    normalize_questions(questions, wire_noul="noul")

    cache = cache if cache is not None else JsonlCache()
    # Key on the canonical form so `boolean` and `noul` spellings of the same
    # question share one cache entry.
    key = cache_key(
        state,
        normalize_questions(questions, wire_noul="noul"),
        model or "default",
        backends=tuple(backends),
        scope=cache_scope,
    )

    hit = cache.get(key)
    if hit is not None:
        out = dict(hit)
        out["cached"] = True
        return out

    attempts: list[dict] = []
    for name in backends:
        cls = BACKENDS.get(name)
        if cls is None:
            attempts.append({"backend": name, "result": "unknown backend"})
            continue
        try:
            if max_attempts is None:
                result = cls().ask(state, questions, model, timeout)
            else:
                result = cls().ask(state, questions, model, timeout, max_attempts=max_attempts)
        except BackendUnavailable as exc:
            attempts.append({"backend": name, "result": f"unavailable: {exc}"})
            continue
        except (BackendFailed, ValueError) as exc:
            attempts.append({"backend": name, "result": f"failed: {exc}"})
            continue
        result["cached"] = False
        result["attempts"] = attempts
        cache.put(key, {k: v for k, v in result.items() if k != "attempts"})
        return result

    raise JevError(
        "every backend failed:\n"
        + "\n".join(f"  {a['backend']}: {a['result']}" for a in attempts)
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

PROBE_STATE = "The customer asked for a refund twice."
PROBE_QUESTIONS = {
    "refund_requested": {
        "type": "noul",
        "instructions": "Did the customer request a refund?",
    }
}


def parse_question_arg(spec: str) -> tuple[str, dict]:
    """Parse `id=type:instructions` from the command line."""
    if "=" not in spec:
        raise ValueError(f"--question {spec!r} must look like id=type:instructions")
    qid, _, rest = spec.partition("=")
    qtype, _, instructions = rest.partition(":")
    if not instructions:
        raise ValueError(f"--question {spec!r} is missing instructions after the colon")
    return qid.strip(), {"type": qtype.strip(), "instructions": instructions.strip()}


def cmd_probe(args) -> int:
    backends = [args.backend] if args.backend else list(BACKEND_ORDER)
    cache = JsonlCache(enabled=not args.no_cache)

    print(f"Jev probe — backends in order: {', '.join(backends)}")
    print(f"state:    {PROBE_STATE!r}")
    print(f"question: refund_requested (noul) 'Did the customer request a refund?'\n")

    try:
        result = ask(
            PROBE_STATE,
            PROBE_QUESTIONS,
            model=args.model,
            backends=backends,
            cache=cache,
            timeout=args.timeout,
        )
    except JevError as exc:
        print(f"FAILED — {exc}")
        return 1

    for attempt in result.get("attempts") or []:
        print(f"  [skipped] {attempt['backend']}: {attempt['result']}")

    answer = result["answers"].get("refund_requested", {})
    print(f"\nbackend:    {result['backend']}{'  (cached)' if result.get('cached') else ''}")
    print(f"model:      {result['model']}")
    print(f"latency:    {result['latency_ms']} ms")
    print(f"usage:      {json.dumps(result.get('usage') or {})}")
    print(f"answer:     type={answer.get('type')} P(true)={answer.get('probability')}")
    return 0


def cmd_ask(args) -> int:
    questions = dict(parse_question_arg(q) for q in args.question)
    try:
        result = ask(
            args.state,
            questions,
            model=args.model,
            backends=[args.backend] if args.backend else list(BACKEND_ORDER),
            cache=JsonlCache(enabled=not args.no_cache),
            timeout=args.timeout,
        )
    except (JevError, ValueError) as exc:
        print(f"FAILED — {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Shared flags live on a parent parser so they work either before or after
    # the subcommand name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--backend", choices=sorted(BACKENDS), help="pin one backend")
    common.add_argument("--model", help="override the backend's default model id")
    common.add_argument("--no-cache", action="store_true", help="bypass the JSONL cache")
    common.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)

    parser = argparse.ArgumentParser(
        prog="jev_client.py",
        parents=[common],
        description="Call TypeSafe's Jev evaluation model.",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "probe",
        parents=[common],
        help="send one harmless question and print the answer",
    )
    p_ask = sub.add_parser("ask", parents=[common], help="ask your own questions")
    p_ask.add_argument("--state", required=True)
    p_ask.add_argument(
        "--question",
        action="append",
        required=True,
        metavar="id=type:instructions",
        help="repeatable, e.g. refunded=noul:Was a refund issued?",
    )

    args = parser.parse_args(argv)
    if args.command == "probe":
        return cmd_probe(args)
    if args.command == "ask":
        return cmd_ask(args)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
