#!/usr/bin/env python3
"""Strict per-row cost ledger.

Existing callers remain valid: ``Ledger(path).log(row_id, provider, usd)``.
Retry/reconciliation callers should additionally pass stable ``event_id`` and
``attempt_id`` values. Repeating an exact event is idempotent; reusing either
identifier for different content is refused.

Cost basis is explicit: measured (default), imputed, or unknown. Measured and
imputed events require finite nonnegative dollars. Unknown events require a
null dollar value so an unknown charge can never silently become $0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from lib import file_lock
from lib.integrity import strict_json_loads
from lib.portable import read_text_compat


SKILL = Path(__file__).resolve().parent.parent
COST_BASES = frozenset(("measured", "imputed", "unknown"))
REQUIRED_FIELDS = frozenset((
    "ts", "event_id", "attempt_id", "row_id", "provider", "action",
    "units", "usd", "basis",
))
OPTIONAL_TEXT_FIELDS = (
    "retry_of", "outcome", "rung", "build_id", "intent_sha",
)

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class LedgerError(Exception):
    """Base class for cost-ledger failures."""


class LedgerAuditError(LedgerError):
    """The ledger cannot be trusted or totaled."""


class LedgerConflictError(LedgerError):
    """A stable event/attempt id was reused for different content."""


class LedgerLockError(LedgerError):
    """Cross-process locking is unavailable."""


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _locked(path: Path, exclusive: bool):
    """One lock shared by threads and processes for this ledger path."""
    if not file_lock.available():
        raise LedgerLockError(
            "no file locking (fcntl/msvcrt) available; refusing an unsafe "
            "ledger operation")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with _thread_lock(path):
        with lock_path.open("a+", encoding="utf-8", newline="\n") as lock_file:
            file_lock.acquire(lock_file, exclusive=exclusive)
            try:
                yield
            finally:
                file_lock.release(lock_file)


def _nonempty_text(value, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def _optional_text(value, field: str):
    if value is None:
        return None
    return _nonempty_text(value, field)


def _positive_int(value, field: str = "units") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _normalize_cost(usd, basis: str):
    if basis not in COST_BASES:
        raise ValueError(
            f"basis must be one of {', '.join(sorted(COST_BASES))}")
    if basis == "unknown":
        if usd is not None:
            raise ValueError("basis='unknown' requires usd=None; unknown is "
                             "never represented as $0")
        return None
    if usd is None or isinstance(usd, bool):
        raise ValueError(f"basis='{basis}' requires finite nonnegative usd")
    try:
        value = float(usd)
    except (TypeError, ValueError) as exc:
        raise ValueError("usd must be a finite nonnegative number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("usd must be a finite nonnegative number")
    return round(value, 6)  # preserve the existing on-disk precision contract


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _semantic(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != "ts"}


def _validate_entry(obj, line_no: int) -> list[str]:
    errors: list[str] = []
    prefix = f"line {line_no}"
    if not isinstance(obj, dict):
        return [f"{prefix}: entry must be an object"]
    missing = sorted(REQUIRED_FIELDS - set(obj))
    if missing:
        return [f"{prefix}: missing field(s): {', '.join(missing)}"]
    allowed = REQUIRED_FIELDS | set(OPTIONAL_TEXT_FIELDS) | {"meta"}
    unknown = sorted(set(obj) - allowed)
    if unknown:
        errors.append(f"{prefix}: unknown field(s): {', '.join(unknown)}")
    for field in ("ts", "event_id", "attempt_id", "row_id", "provider"):
        value = obj.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{prefix}: {field} must be a non-empty string")
    try:
        parsed_ts = datetime.fromisoformat(
            str(obj.get("ts") or "").replace("Z", "+00:00"))
        if parsed_ts.tzinfo is None:
            raise ValueError("timestamp lacks timezone")
    except ValueError:
        errors.append(f"{prefix}: ts must be an ISO-8601 timestamp with timezone")
    if not isinstance(obj.get("action"), str):
        errors.append(f"{prefix}: action must be a string")
    try:
        _positive_int(obj.get("units"))
    except ValueError as exc:
        errors.append(f"{prefix}: {exc}")
    basis, usd = obj.get("basis"), obj.get("usd")
    if not isinstance(basis, str):
        errors.append(f"{prefix}: basis must be a string")
    elif basis != "unknown" and (
            isinstance(usd, bool) or not isinstance(usd, (int, float))):
        errors.append(f"{prefix}: usd must be a JSON number for basis='{basis}'")
    else:
        try:
            _normalize_cost(usd, basis)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
    for field in OPTIONAL_TEXT_FIELDS:
        if field in obj and (
                not isinstance(obj[field], str) or not obj[field].strip()):
            errors.append(f"{prefix}: {field} must be a non-empty string")
    if "meta" in obj and not isinstance(obj["meta"], dict):
        errors.append(f"{prefix}: meta must be an object")
    try:
        _canonical(obj)
    except (TypeError, ValueError) as exc:
        errors.append(f"{prefix}: not strict JSON: {exc}")
    return errors


def _scan_unlocked(path: Path, missing_ok: bool = False):
    """Return unique entries, physical count, exact dupes, and issues."""
    if not path.exists():
        if missing_ok:
            return [], 0, 0, []
        return [], 0, 0, [f"ledger not found: {path}"]
    entries: list[dict] = []
    physical = exact_duplicates = 0
    issues: list[str] = []
    event_seen: dict[str, dict] = {}
    attempt_seen: dict[str, dict] = {}
    for line_no, raw in enumerate(
            read_text_compat(path).splitlines(), 1):
        if not raw.strip():
            continue
        physical += 1
        try:
            obj = strict_json_loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            issues.append(f"line {line_no}: malformed JSON: {exc}")
            continue
        row_errors = _validate_entry(obj, line_no)
        if row_errors:
            issues.extend(row_errors)
            continue
        event_id, attempt_id = obj["event_id"], obj["attempt_id"]
        prior_event = event_seen.get(event_id)
        prior_attempt = attempt_seen.get(attempt_id)
        if prior_event is not None:
            if _canonical(_semantic(prior_event)) == _canonical(_semantic(obj)):
                exact_duplicates += 1
                continue
            issues.append(f"line {line_no}: event_id '{event_id}' conflicts "
                          "with an earlier event")
            continue
        if prior_attempt is not None:
            issues.append(f"line {line_no}: attempt_id '{attempt_id}' "
                          "conflicts with an earlier event")
            continue
        event_seen[event_id] = obj
        attempt_seen[attempt_id] = obj
        entries.append(obj)
    attempt_ids = set(attempt_seen)
    for entry in entries:
        retry_of = entry.get("retry_of")
        if retry_of and retry_of not in attempt_ids:
            issues.append(
                f"event_id '{entry['event_id']}': retry_of '{retry_of}' does "
                "not name an attempt in this ledger")
        if retry_of and retry_of == entry["attempt_id"]:
            issues.append(f"event_id '{entry['event_id']}': retry_of cannot "
                          "reference its own attempt_id")
    return entries, physical, exact_duplicates, issues


def _raise_issues(issues: list[str]):
    if issues:
        preview = "; ".join(issues[:10])
        suffix = f"; +{len(issues) - 10} more" if len(issues) > 10 else ""
        raise LedgerAuditError(
            f"ledger audit failed with {len(issues)} issue(s): "
            f"{preview}{suffix}")


def read_entries(path: Path) -> list[dict]:
    """Strict reader: bad or missing cost fields never become implicit $0."""
    path = Path(path)
    with _locked(path, exclusive=False):
        entries, _physical, _dupes, issues = _scan_unlocked(path)
    _raise_issues(issues)
    return entries


def audit(path: Path) -> dict:
    """Strict integrity and total audit over a ledger."""
    path = Path(path)
    with _locked(path, exclusive=False):
        entries, physical, dupes, issues = _scan_unlocked(path)
    _raise_issues(issues)
    known = [entry["usd"] for entry in entries if entry["usd"] is not None]
    unknown = sum(entry["usd"] is None for entry in entries)
    subtotal = round(sum(known), 6)
    return {
        "path": str(path), "entries": len(entries),
        "physical_entries": physical,
        "exact_duplicate_events_ignored": dupes,
        "basis_counts": {
            basis: sum(entry["basis"] == basis for entry in entries)
            for basis in sorted(COST_BASES)
        },
        "unknown_cost_entries": unknown,
        "known_subtotal_usd": subtotal,
        "total_usd": None if unknown else subtotal,
    }


def _append_durable(path: Path, entry: dict):
    data = (_canonical(entry) + "\n").encode("utf-8")
    existed = path.exists()
    # O_BINARY (Windows only; 0 elsewhere) keeps "\n" from becoming "\r\n".
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                 | getattr(os, "O_BINARY", 0), 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:  # pragma: no cover - defensive kernel boundary
                raise OSError("short write to cost ledger")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    if not existed:
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass  # not every filesystem permits directory fsync


class Ledger:
    def __init__(self, path):
        self.path = Path(path)

    def log(self, row_id, provider, usd, action="", units=1,
            basis="measured", event_id=None, attempt_id=None, retry_of=None,
            outcome=None, rung=None, build_id=None, intent_sha=None, **meta):
        """Validate and durably append one idempotent billing event."""
        row_id = _nonempty_text(row_id, "row_id")
        provider = _nonempty_text(provider, "provider")
        if not isinstance(action, str):
            raise ValueError("action must be a string")
        units = _positive_int(units)
        basis = _nonempty_text(basis, "basis").lower()
        usd = _normalize_cost(usd, basis)
        event_id = _optional_text(event_id, "event_id")
        attempt_id = _optional_text(attempt_id, "attempt_id")
        if event_id and not attempt_id:
            attempt_id = event_id
        if not attempt_id:
            attempt_id = "attempt-" + uuid.uuid4().hex
        retry_of = _optional_text(retry_of, "retry_of")
        outcome = _optional_text(outcome, "outcome")
        rung = _optional_text(rung, "rung")
        build_id = _optional_text(build_id, "build_id")
        intent_sha = _optional_text(intent_sha, "intent_sha")
        if retry_of == attempt_id:
            raise ValueError("retry_of cannot reference its own attempt_id")
        if meta:
            try:
                _canonical(meta)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"meta must be strict JSON: {exc}") from exc

        semantic = {
            "event_id": event_id, "attempt_id": attempt_id,
            "row_id": row_id, "provider": provider, "action": action,
            "units": units, "usd": usd, "basis": basis,
        }
        for field, value in (
            ("retry_of", retry_of), ("outcome", outcome), ("rung", rung),
            ("build_id", build_id), ("intent_sha", intent_sha),
        ):
            if value is not None:
                semantic[field] = value
        if meta:
            semantic["meta"] = meta
        if event_id is None:
            digest = hashlib.sha256(
                _canonical(semantic).encode("utf-8")).hexdigest()
            event_id = "event-" + digest[:24]
            semantic["event_id"] = event_id
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **semantic,
        }
        problems = _validate_entry(entry, 1)
        if problems:  # pragma: no cover - helpers should catch these earlier
            raise ValueError("; ".join(problems))

        with _locked(self.path, exclusive=True):
            existing, _physical, _dupes, issues = _scan_unlocked(
                self.path, missing_ok=True)
            _raise_issues(issues)
            if retry_of and retry_of not in {e["attempt_id"] for e in existing}:
                raise ValueError(
                    f"retry_of '{retry_of}' does not name an existing attempt")
            for old in existing:
                same_event = old["event_id"] == event_id
                same_attempt = old["attempt_id"] == attempt_id
                if not (same_event or same_attempt):
                    continue
                if (same_event and same_attempt and
                        _canonical(_semantic(old)) == _canonical(semantic)):
                    return old
                which = "event_id" if same_event else "attempt_id"
                value = event_id if same_event else attempt_id
                raise LedgerConflictError(
                    f"{which} '{value}' already exists with different content")
            _append_durable(self.path, entry)
        return entry


def _token_count(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def llm_cost(model_id: str, in_tokens: int, out_tokens: int) -> float:
    in_tokens = _token_count(in_tokens, "in_tokens")
    out_tokens = _token_count(out_tokens, "out_tokens")
    catalog = json.loads((SKILL / "references" / "catalog.json").read_text(
        encoding="utf-8"))
    # A substring such as "flash" is not a priced model. Accept only an exact
    # catalog id or its explicit provider-qualified spelling, as used by roles.
    exact = [model for model in catalog["models"] if model["id"] == model_id]
    matches = exact or [
        model for model in catalog["models"]
        if model.get("provider")
        and f"{model['provider']}/{model['id']}" == model_id
    ]
    if len(matches) != 1:
        reason = "ambiguous" if matches else "not in catalog price table"
        raise SystemExit(
            f"model '{model_id}' {reason} — use an exact, unique id from "
            "seeds/models.json")
    model = matches[0]
    # Validate rates independently: an invalid negative rate must not be
    # hidden by a larger positive rate in the final sum.
    input_rate = _normalize_cost(model["in"], "imputed")
    output_rate = _normalize_cost(model["out"], "imputed")
    cost = in_tokens / 1e6 * input_rate + out_tokens / 1e6 * output_rate
    return _normalize_cost(cost, "imputed")


def report(path: Path):
    """Print strict totals; unknown cost keeps the overall total unknown."""
    path = Path(path)
    entries = read_entries(path)
    summary = audit(path)
    if not entries:
        print("empty ledger")
        return summary
    rows: dict[str, float] = {}
    providers: dict[str, float] = {}
    unknown_rows: set[str] = set()
    for entry in entries:
        if entry["usd"] is None:
            unknown_rows.add(entry["row_id"])
            continue
        providers[entry["provider"]] = (
            providers.get(entry["provider"], 0.0) + entry["usd"])
        rows[entry["row_id"]] = rows.get(entry["row_id"], 0.0) + entry["usd"]
    if summary["total_usd"] is None:
        total_text = (f"UNKNOWN ({summary['unknown_cost_entries']} "
                      f"unknown-cost event(s); known subtotal "
                      f"${summary['known_subtotal_usd']:.4f})")
    else:
        total_text = f"${summary['total_usd']:.4f}"
    print(f"entries: {summary['entries']} | rows with known spend: "
          f"{len(rows)} | TOTAL: {total_text}")
    if rows:
        per_row = sorted(rows.values())

        def percentile(percent):
            index = min(len(per_row) - 1,
                        int(percent / 100 * len(per_row)))
            return per_row[index]

        print(f"per-row known: median ${percentile(50):.5f} | "
              f"p90 ${percentile(90):.5f} | max ${per_row[-1]:.4f} | "
              f"mean ${sum(per_row) / len(rows):.5f}")
    if unknown_rows:
        print(f"rows with unknown cost: {len(unknown_rows)}")
    print("by provider (known subtotal):")
    for provider, cost in sorted(providers.items(), key=lambda item: -item[1]):
        print(f"  {provider:24s} ${cost:.4f}")
    print("top-10 most expensive rows (known subtotal):")
    for row_id, cost in sorted(rows.items(), key=lambda item: -item[1])[:10]:
        print(f"  {row_id}: ${cost:.4f}")
    return summary


def _parse_usd(text: str):
    if str(text).strip().lower() in ("unknown", "null", "none"):
        return None
    return float(text)


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    log_parser = sub.add_parser("log")
    log_parser.add_argument("ledger")
    log_parser.add_argument("--row", required=True)
    log_parser.add_argument("--provider", required=True)
    log_parser.add_argument("--action", default="")
    log_parser.add_argument("--usd", required=True, type=_parse_usd,
                            help="finite nonnegative dollars, or 'unknown'")
    log_parser.add_argument("--units", type=int, default=1)
    log_parser.add_argument("--basis", choices=sorted(COST_BASES),
                            default="measured")
    log_parser.add_argument("--event-id")
    log_parser.add_argument("--attempt-id")
    log_parser.add_argument("--retry-of")
    log_parser.add_argument("--outcome")
    log_parser.add_argument("--rung")
    log_parser.add_argument("--build-id")
    log_parser.add_argument("--intent-sha")
    log_parser.add_argument("--meta", default=None)
    llm_parser = sub.add_parser("llm-cost")
    llm_parser.add_argument("model")
    llm_parser.add_argument("in_tokens", type=int)
    llm_parser.add_argument("out_tokens", type=int)
    for command in ("audit", "report"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("ledger")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "log":
            meta = json.loads(args.meta) if args.meta else {}
            if not isinstance(meta, dict):
                raise ValueError("--meta must decode to a JSON object")
            Ledger(args.ledger).log(
                args.row, args.provider, args.usd, args.action, args.units,
                basis=args.basis, event_id=args.event_id,
                attempt_id=args.attempt_id, retry_of=args.retry_of,
                outcome=args.outcome, rung=args.rung, build_id=args.build_id,
                intent_sha=args.intent_sha, **meta)
            return 0
        if args.cmd == "llm-cost":
            print(f"{llm_cost(args.model, args.in_tokens, args.out_tokens):.6f}")
            return 0
        if args.cmd == "audit":
            print(json.dumps(audit(Path(args.ledger)), indent=2,
                             sort_keys=True))
            return 0
        if args.cmd == "report":
            report(Path(args.ledger))
            return 0
    except (LedgerError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"cost ledger: {exc}", file=sys.stderr)
        return 2
    print(__doc__)
    return 1


if __name__ == "__main__":
    from lib.portable import utf8_stdio
    utf8_stdio()  # Windows console: UTF-8 before any output
    sys.exit(main())
