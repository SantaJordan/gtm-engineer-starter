"""Crash-safe writes, strict JSONL reads, and check accounting for Crawford
CLIs (reliability retrofit).

Adapted from tam-qa's engine (apply_integrity + common) so Crawford stays a
self-contained subscriber bundle — no cross-skill imports at runtime.
Stdlib only, no LLM calls, no network.

Three concerns, one base module:
  atomic_write_*      tmp -> flush -> fsync -> replace -> dir-fsync; a crash
                      leaves the old bytes or the new bytes, never a torn file
  read_jsonl_strict   malformed lines are a counted, line-numbered hard error
                      (read_jsonl_lenient returns them for escape-hatch flows)
  record_check / …    durable ran|failed|skipped accounting per run dir —
                      a check that never ran is NOT a pass
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import file_lock
from .portable import iter_lines_compat, read_text_compat, replace_with_retry

CHECK_STATUSES = ("ran", "failed", "skipped")
CHECK_MANIFEST = "check_manifest.json"
_HASH_CHUNK = 1024 * 1024
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class MalformedRecordError(ValueError):
    """A JSONL artifact contains lines that are not valid JSON objects."""

    def __init__(self, path, bad_lines):
        self.path = str(path)
        self.bad_lines = list(bad_lines)            # [(line_no, error), ...]
        preview = "; ".join(f"line {n}: {e}" for n, e in self.bad_lines[:3])
        more = ("" if len(self.bad_lines) <= 3
                else f" (+{len(self.bad_lines) - 3} more)")
        super().__init__(f"{path}: {len(self.bad_lines)} malformed JSONL "
                         f"line(s) — {preview}{more}")


class CheckSkipped(RuntimeError):
    """A check was not applicable or lacked a declared prerequisite."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def exclusive_file_lock(path):
    """Serialize one read/modify/write transaction across threads + processes.

    Atomic replacement protects bytes from tearing, but without this lock two
    writers may both read version N and each publish a different N+1, silently
    losing one update.  A persistent sibling lock file is deliberate: locking
    the data inode itself is unsafe because ``os.replace`` swaps that inode.
    """
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock_for(lock_path)
    with thread_lock:
        if not file_lock.available():
            raise RuntimeError(
                "cross-process file locking unavailable on this platform; "
                "refusing an unsafe read/modify/write")
        with lock_path.open("a+b") as handle:
            file_lock.acquire(handle)
            try:
                yield
            finally:
                file_lock.release(handle)


# --------------------------------------------------------------- atomic writes

def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_text(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        replace_with_retry(str(tmp), str(path))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def atomic_write_json(path, obj, indent: int = 1) -> None:
    atomic_write_text(path, json.dumps(
        obj, ensure_ascii=False, indent=indent, default=str,
        allow_nan=False) + "\n")


def atomic_write_jsonl(path, rows) -> None:
    atomic_write_text(path, "".join(
        json.dumps(r, ensure_ascii=False, default=str, allow_nan=False) + "\n"
        for r in rows))


# --------------------------------------------------------------------- hashing

def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha(obj) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------- JSONL reading

def _strict_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON object key {key!r}")
        obj[key] = value
    return obj


def strict_json_loads(text):
    """Parse JSON without Python's duplicate-key/NaN extensions."""
    def reject_constant(value):
        raise ValueError(f"non-finite JSON number {value}")

    return json.loads(text, object_pairs_hook=_strict_object,
                      parse_constant=reject_constant)


def read_jsonl_lenient(path):
    """-> (rows, bad_lines). Blank lines skipped; a parseable line that is
    not a JSON object still counts as malformed (downstream does row.get)."""
    path = Path(path)
    rows, bad = [], []
    if not path.exists():
        return rows, bad
    # streamed; legacy cp1252 lines decode too
    for n, line in enumerate(iter_lines_compat(path), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = strict_json_loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            bad.append((n, str(exc)))
            continue
        if not isinstance(obj, dict):
            bad.append((n, "not a JSON object"))
            continue
        rows.append(obj)
    return rows, bad


def read_jsonl_strict(path):
    rows, bad = read_jsonl_lenient(path)
    if bad:
        raise MalformedRecordError(path, bad)
    return rows


# ------------------------------------------------------------ check accounting

def read_check_manifest(run_dir):
    """A missing manifest is distinct from an empty one — ship decisions
    check path existence separately."""
    path = Path(run_dir) / CHECK_MANIFEST
    if not path.exists():
        return []
    data = strict_json_loads(read_text_compat(path))
    checks = data.get("checks") if isinstance(data, dict) else None
    if not isinstance(checks, list):
        raise ValueError(f"{path} must contain a checks list")
    seen = set()
    for i, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ValueError(f"{path} checks[{i}] must be an object")
        name = check.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path} checks[{i}].name must be non-empty")
        if name in seen:
            raise ValueError(f"{path} contains duplicate check '{name}'")
        seen.add(name)
        if check.get("status") not in CHECK_STATUSES:
            raise ValueError(f"{path} check '{name}' has invalid status "
                             f"{check.get('status')!r}")
    return checks


def _check_record(name, status, reason="", critical=False, stage=None):
    if status not in CHECK_STATUSES:
        raise ValueError(f"invalid check status {status!r}")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("check name must be a non-empty string")
    rec = {"name": name.strip(), "status": status,
           "reason": str(reason or ""), "critical": bool(critical)}
    if stage:
        rec["stage"] = str(stage)
    return rec


def _upsert_check_unlocked(run_dir: Path, rec: dict):
    checks = read_check_manifest(run_dir)
    by_name = {c["name"]: c for c in checks}
    by_name[rec["name"]] = rec
    atomic_write_json(run_dir / CHECK_MANIFEST, {
        "updated_at": utc_now_iso(),
        "checks": [by_name[k] for k in sorted(by_name)],
    }, indent=2)
    return rec


def record_check(run_dir, name, status, reason="", critical=False, stage=None):
    """Locked upsert into <run_dir>/check_manifest.json."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    rec = _check_record(name, status, reason, critical, stage)
    lock_path = run_dir / f"{CHECK_MANIFEST}.lock"
    with exclusive_file_lock(lock_path):
        return _upsert_check_unlocked(run_dir, rec)


def execute_check(run_dir, name, fn, critical=False, stage=None):
    """Run one check, persist its outcome, keep its failure isolated."""
    try:
        value = fn()
    except CheckSkipped as exc:
        record_check(run_dir, name, "skipped", str(exc),
                     critical=critical, stage=stage)
        return None
    except Exception as exc:                        # noqa: BLE001 — isolate
        record_check(run_dir, name, "failed", str(exc),
                     critical=critical, stage=stage)
        return None
    record_check(run_dir, name, "ran", critical=critical, stage=stage)
    return value


def record_stage_summary(run_dir, stage, check_names):
    """Fail a stage when any declared check failed OR was never recorded."""
    run_dir = Path(run_dir)
    wanted = set(check_names)
    if not wanted:
        raise ValueError("check_names must not be empty")
    lock_path = run_dir / f"{CHECK_MANIFEST}.lock"
    with exclusive_file_lock(lock_path):
        checks = [c for c in read_check_manifest(run_dir)
                  if c.get("name") in wanted]
        recorded = {c.get("name") for c in checks}
        missing = sorted(wanted - recorded)
        failures = [c for c in checks if c.get("status") == "failed"]
        if failures or missing:
            reasons = [f"{c.get('name')}: {c.get('reason')}"
                       for c in failures[:5]]
            if missing:
                reasons.append("checks never recorded: "
                               + ", ".join(missing[:10]))
            rec = _check_record(f"stage.{stage}", "failed",
                                "; ".join(reasons), critical=True, stage=stage)
        else:
            rec = _check_record(f"stage.{stage}", "ran", critical=True,
                                stage=stage)
        return _upsert_check_unlocked(run_dir, rec)
