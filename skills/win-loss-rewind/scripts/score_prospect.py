#!/usr/bin/env python3
"""
Phase 8 — Score a new prospect against the latest discriminator.

Usage:
    python score_prospect.py acmelegal.example
    python score_prospect.py acmelegal.example --run-id wlr-2026-05-05
    python score_prospect.py acmelegal.example --out outputs/wlr-xxxx/scores/acmelegal.example.json

Default: finds the most recently modified outputs/wlr-*/discriminator.jsonl,
runs Phase-2-style enrichment for the prospect via scripts/enrich_panel.py
(invoked as a subprocess against a single-row dataset), and scores the
resulting panel against every shipped archetype.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from signal_presence import signal_present, signal_state  # noqa: F401
import rubric_score  # noqa: E402

load_dotenv()

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"
SCRIPTS_DIR = Path(__file__).resolve().parent

# ──────────────────────────────────────────────────────────────
# Discriminator discovery
# ──────────────────────────────────────────────────────────────


def find_latest_discriminator(run_id: str | None) -> tuple[Path, str]:
    if run_id:
        path = OUTPUTS_ROOT / run_id / "discriminator.jsonl"
        if not path.exists():
            raise SystemExit(f"Error: {path} not found")
        return path, run_id
    candidates = sorted(
        OUTPUTS_ROOT.glob("wlr-*/discriminator.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit("Error: no outputs/wlr-*/discriminator.jsonl found")
    latest = candidates[0]
    return latest, latest.parent.name


def load_discriminators(path: Path) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# ──────────────────────────────────────────────────────────────
# Enrich a single domain via enrich_panel.py
# ──────────────────────────────────────────────────────────────


def enrich_one(domain: str, run_id: str) -> dict:
    """
    Pull a Phase-2-style panel for one cold domain by importing
    `enrich_panel.enrich_one_customer` (the real Phase 2 entry point).
    Time travel is deliberately NOT applied here — scoring a cold prospect
    with today's data is legitimate; visibility tags exist to validate the
    rubric at build time, not to gate its application.
    """
    enrich_script = SCRIPTS_DIR / "enrich_panel.py"
    if not enrich_script.exists():
        # Soft fallback so this script still works in scaffold environments
        # without the upstream Phase-2 module wired up yet.
        return {
            "customer_id": domain,
            "domain": domain,
            "signal_panel": {},
            "warning": "enrich_panel.py not present; returning empty signal panel",
        }

    customer = {
        "id": domain,
        "customer_id": domain,
        "company": domain,
        "domain": domain,
        "outcome": "unknown",
    }
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import enrich_panel  # type: ignore
        panel = enrich_panel.enrich_one_customer(customer, enrich_panel.ALL_SOURCES)
        if isinstance(panel, dict):
            panel.setdefault("customer_id", domain)
            panel.setdefault("domain", domain)
            return panel
        return {
            "customer_id": domain,
            "domain": domain,
            "signal_panel": {},
            "warning": f"enrich_one_customer returned {type(panel).__name__}, expected dict",
        }
    except Exception as e:
        return {
            "customer_id": domain,
            "domain": domain,
            "signal_panel": {},
            "warning": f"enrichment failed: {e}",
        }
    finally:
        if str(SCRIPTS_DIR) in sys.path:
            sys.path.remove(str(SCRIPTS_DIR))


# ──────────────────────────────────────────────────────────────
# Scoring
# ──────────────────────────────────────────────────────────────


def score_against_archetype(arc: dict, panel: dict) -> dict:
    """Score with the SAME function Phase 7 calibrated the threshold with."""
    res = rubric_score.verdict(arc.get("rubric") or {"signals": []}, panel,
                               arc.get("threshold", 0.5))
    return {
        "id": arc.get("archetype_id"),
        "name": arc.get("archetype_name"),
        "fit_score": round(res["score"], 4),
        "coverage": round(res["coverage"], 4),
        "threshold": res["threshold"],
        "looks_like": res["looks_like"],
        "status": res["status"],
        "n_unknown": res["n_unknown"],
        "signal_diff": res["diff"],
    }


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("domain", help="Prospect domain (e.g., acmelegal.example)")
    ap.add_argument("--run-id", default=None,
                    help="Force a specific run-id; default = latest")
    ap.add_argument("--out", default=None,
                    help="Output path (default: outputs/<run-id>/scores/<domain>.json)")
    args = ap.parse_args()

    discriminator_path, run_id = find_latest_discriminator(args.run_id)
    archetypes = load_discriminators(discriminator_path)

    # Filter to shippable archetypes only.
    shipped = [a for a in archetypes if a.get("ship_status") == "ship"]
    if not shipped:
        print("Warning: no archetypes shipped a discriminator; scoring against "
              "every archetype with a rubric anyway.", file=sys.stderr)
        shipped = [a for a in archetypes if a.get("rubric")]

    panel = enrich_one(args.domain, run_id)

    scored = [score_against_archetype(a, panel) for a in shipped]
    scored.sort(key=lambda x: x["fit_score"], reverse=True)
    top = scored[0] if scored else None

    result = {
        "prospect_domain": args.domain,
        "scored_at": _utcnow_iso(),
        "run_id": run_id,
        "discriminator_path": str(discriminator_path),
        "archetypes": scored,
        "top_match": top,
        "panel": panel,
    }

    out_path = Path(args.out) if args.out else (
        OUTPUTS_ROOT / run_id / "scores" / f"{args.domain}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str))

    # Human-readable summary.
    print(f"\nProspect: {args.domain}")
    print(f"Scored against discriminator: {discriminator_path}")
    if top:
        print(f"\nTop match: {top['name']} (id={top['id']})")
        print(f"  fit_score = {top['fit_score']}  (threshold = {top['threshold']})")
        print(f"  looks_like = {top['looks_like']}")
        print(f"  status = {top['status']}  coverage = {top['coverage']:.0%} of rubric weight observed")
        print(f"  unobserved signals = {top.get('n_unknown', 0)} "
              f"(source missing, failed, or field not extracted)")
    print("\nAll archetypes:")
    for a in scored:
        marker = "✓" if a["looks_like"] else " "
        print(f"  [{marker}] {a['id']:<3} {a['name'] or '':<40} fit={a['fit_score']} thr={a['threshold']}")
    print(f"\nFull JSON → {out_path}")
    return 0


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    sys.exit(main())
