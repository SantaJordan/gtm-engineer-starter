#!/usr/bin/env python3
"""The HUMAN write-back store — the only thing in this system that is true.

Shape borrowed verbatim from Crawford's operational actuals
(the full Crawford harness's `record-actuals` command, not included,
`references/harness.md` § Operational actuals):

  * append-only JSONL, one record per human answer,
  * every record SELF-HASHED over its stable fields, so an edited ledger is
    detected instead of trusted,
  * a correction is a NEW record citing the `record_sha` it revises — history
    is never rewritten,
  * `unknown` is kept distinct from `false`.

Crawford keys on (row_id, field) inside one run directory. This store is
cross-run, so it keys on **(entity_key, decision)** and lives at
`outputs/calibrated-judge/actuals.jsonl`.

The lookup is consulted FIRST, before any battery is built. A settled human
answer short-circuits the judge and is never shown to the model — otherwise
the "calibration" would be the model reading back its own graded homework.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config

#: What a human answer is. `ratified` is the ordinary case; the other three
#: keep an honest distinction Crawford also keeps.
KINDS = ("ratified", "corrected", "excluded", "unknown")

#: Fields the hash covers. Anything outside it is annotation.
HASHED = ("recorded_at", "entity_key", "decision", "kind", "verdict", "who",
          "evidence", "note", "source", "revises")


class LedgerTampered(RuntimeError):
    """A record's hash does not match its contents."""


def path() -> Path:
    return config.actuals_path()


def record_sha(rec: dict) -> str:
    base = {k: rec.get(k) for k in HASHED if k in rec or k != "revises"}
    blob = json.dumps(base, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def read_all(verify: bool = True) -> list[dict]:
    p = path()
    if not p.exists():
        return []
    rows = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerTampered(f"{p} line {i} is not JSON") from exc
        if not isinstance(row, dict):
            raise LedgerTampered(f"{p} line {i} is not an object")
        if verify and row.get("record_sha") != record_sha(row):
            raise LedgerTampered(
                f"{p} line {i} fails its own hash — the ledger was edited; "
                "actuals are immutable, a correction is a new record")
        rows.append(row)
    return rows


def record(*, entity_key, decision, verdict, who, evidence="", note="",
           source="", kind="ratified", judge_probabilities=None,
           judge_action=None, revises=None, when=None) -> dict:
    """Append one human answer. Returns the record, including its sha."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not str(entity_key or "").strip() or not str(decision or "").strip():
        raise ValueError("an actual binds to an entity AND a decision")
    existing = read_all()
    if revises and revises not in {r.get("record_sha") for r in existing}:
        raise ValueError(f"--revises {revises} matches no existing record_sha")

    rec = {
        "recorded_at": (when or datetime.now(timezone.utc)).isoformat(),
        "entity_key": str(entity_key).strip(),
        "decision": str(decision).strip(),
        "kind": kind,
        "verdict": verdict,
        "who": who,
        "evidence": evidence or "",
        "note": note or "",
        "source": source or "",
    }
    if revises:
        rec["revises"] = revises
    rec["record_sha"] = record_sha(rec)
    # Annotations ride OUTSIDE the hash: the judge's probabilities at the time
    # are context for calibration, not part of the human's answer.
    if judge_probabilities is not None:
        rec["judge_probabilities"] = judge_probabilities
    if judge_action is not None:
        rec["judge_action"] = judge_action

    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def append_many(records) -> int:
    n = 0
    for r in records:
        record(**r)
        n += 1
    return n


def lookup(entity_key, decision):
    """The settled human answer for this (entity, decision), or None.

    Latest record wins, and a record that has been revised is skipped — the
    revision is what stands.
    """
    rows = [r for r in read_all()
            if r.get("entity_key") == str(entity_key)
            and r.get("decision") == str(decision)]
    if not rows:
        return None
    revised = {r.get("revises") for r in rows if r.get("revises")}
    live = [r for r in rows if r.get("record_sha") not in revised]
    return (live or rows)[-1]


def settled_map(decision=None) -> dict:
    """{(entity_key, decision): record} for everything settled."""
    out = {}
    for r in read_all():
        if decision and r.get("decision") != decision:
            continue
        out[(r.get("entity_key"), r.get("decision"))] = r
    revised = {r.get("revises") for r in read_all() if r.get("revises")}
    return {k: v for k, v in out.items() if v.get("record_sha") not in revised}


def as_result(rec: dict, decision: str, sha: str):
    """A settled human answer, in the judge's own result shape.

    `backend="actuals"` is how a log row says a human decided this, not a
    model, and `cost_usd` is 0.0 because nothing was spent.
    """
    from .core import JudgeResult
    return JudgeResult(
        verdict=rec.get("verdict"), action=_action_for(rec, decision),
        decision_score=None, per_question={}, review_reasons=[],
        model=None, backend="actuals", battery_version="human",
        composer_version="human", latency_ms=0, cost_usd=0.0,
        evidence_sha256=sha, cached=True,
        why=f"settled by {rec.get('who') or 'a human'} on "
            f"{str(rec.get('recorded_at'))[:10]}",
        raw={"actual": rec})


def _action_for(rec, decision) -> str:
    """A human label -> the judge's action vocabulary."""
    kind = rec.get("kind")
    if kind in ("excluded", "unknown"):
        return "review"
    verdict = rec.get("verdict")
    if verdict is None:
        return "review"
    v = str(verdict).strip().lower()
    if v in ("unsure", "unknown", "evidence_insufficient", ""):
        return "review"
    try:
        from .core import resolve_battery
        battery, _ = resolve_battery(decision)
    except Exception:
        return "accept"
    reject = str(battery.get("reject_verdict") or "").strip().lower()
    return "reject" if v == reject else "accept"
