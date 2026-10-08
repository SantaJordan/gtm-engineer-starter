#!/usr/bin/env python3
"""The append-only decision log — one JSON object per judged row.

`outputs/calibrated-judge/decisions/<YYYY-MM>.jsonl`, month-partitioned so a
year of shadow traffic stays greppable. `outputs/` is gitignored at the repo
root, so nothing written here can reach a commit.

Every row carries BOTH verdicts — the judge's and the incumbent's — and the raw
per-question probabilities, because the whole point of shadow mode is to be
able to ask, later and from evidence, whether the judge should ever have been
allowed to decide anything.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import config


def path_for(when: datetime | None = None) -> Path:
    when = when or datetime.now(timezone.utc)
    return config.decisions_dir() / f"{when:%Y-%m}.jsonl"


def _norm(v):
    """Compare verdicts the way a human would: case and space are noise."""
    if v is None:
        return None
    return " ".join(str(v).strip().lower().split())


def agreement(judge_verdict, incumbent_verdict):
    """None when there is nothing to compare — never a default False."""
    if incumbent_verdict is None or judge_verdict is None:
        return None
    return _norm(judge_verdict) == _norm(incumbent_verdict)


def row_for(result, *, skill, hook_point, decision, entity_key, run_id=None,
            incumbent_verdict=None, incumbent_confidence=None, mode=None,
            when=None) -> dict:
    when = when or datetime.now(timezone.utc)
    d = result.as_dict() if hasattr(result, "as_dict") else dict(result)
    return {
        "ts": when.isoformat(),
        "run_id": run_id,
        "skill": skill,
        "hook_point": hook_point,
        "decision": decision,
        "entity_key": entity_key,
        "evidence_sha256": d.get("evidence_sha256"),
        "battery_version": d.get("battery_version"),
        "composer_version": d.get("composer_version"),
        "model": d.get("model"),
        "backend": d.get("backend"),
        "per_question": d.get("per_question") or {},
        "decision_score": d.get("decision_score"),
        "action": d.get("action"),
        "verdict": d.get("verdict"),
        "review_reasons": d.get("review_reasons") or [],
        "incumbent_verdict": incumbent_verdict,
        "incumbent_confidence": incumbent_confidence,
        "agreement": agreement(d.get("verdict"), incumbent_verdict),
        "latency_ms": d.get("latency_ms"),
        "cost_usd": d.get("cost_usd"),
        # how much evidence this row actually had, and whether it was enriched
        # and asked again. A precision number read without these is a number
        # about the packets, not about the judge.
        "packet_richness": d.get("packet_richness") or {},
        "round": d.get("round", 1),
        "enrichment_added": d.get("enrichment_added") or [],
        "cached": d.get("cached"),
        "mode": mode or config.mode(),
        "error": d.get("error"),
    }


def append(result, **kw) -> Path:
    """Append one row. Raises only on a real I/O failure — callers that must
    not break (every hook) wrap this in their own try."""
    path = path_for(kw.get("when"))
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row_for(result, **kw), ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


def read_all(paths=None):
    """Every logged row, oldest file first. A corrupt line is SKIPPED, not
    fatal: a half-written row from a killed process must not cost you the
    month's calibration evidence."""
    if paths is None:
        d = config.decisions_dir()
        paths = sorted(d.glob("*.jsonl")) if d.exists() else []
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row
