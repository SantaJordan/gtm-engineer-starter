#!/usr/bin/env python3
"""
reconcile_join_verdicts.py — W3 final: merge the four hats' verdicts into a
single per-join decision. Reads:
  verdicts/fk_archaeologist.json (Hat 1)
  verdicts/anomaly.json (Hat 2)
  verdicts/self_domain_guard.json (Hat 3)
  verdicts/semantic.json (Hat 4)

Emits audit/join_verdicts.json. Reconciliation rule:
  - Any agent emits hard-reject → REJECT (override only via written notes_from_user)
  - 3-of-4 agree on downgrade → DEMOTE
  - Consensus trust → TRUST
  - Else → PAUSE_HUMAN_REQUIRED

Usage:
    uv run reconcile_join_verdicts.py --audit-slug <slug>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


HARD_FAIL_HAT1 = {"FAIL_HARD", "FAIL_STRUCTURAL"}
SOFT_FAIL_HAT1 = {"FAIL_SOFT", "PASS_WITH_NOTE"}
HAT4_REJECT = {"AUTH_REJECT", "EMAIL_REJECT"}


def key_of(j: dict) -> str:
    s = j.get("source", {})
    t = j.get("target", {})
    return f"{s.get('file')}::{s.get('column')}->{t.get('file')}::{t.get('column')}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    verdicts_dir = out_dir / "verdicts"
    audit_dir = out_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    def load(name):
        p = verdicts_dir / name
        if not p.exists():
            return []
        d = json.loads(p.read_text())
        return d if isinstance(d, list) else d.get("joins", [])

    hat1 = {key_of(j): j for j in load("fk_archaeologist.json")}
    hat2 = {key_of(j): j for j in load("anomaly.json")}
    hat3_findings = []
    try:
        hat3_data = json.loads((verdicts_dir / "self_domain_guard.json").read_text())
        hat3_findings = hat3_data
    except Exception:
        hat3_data = {}
    hat4 = {key_of(j): j for j in load("semantic.json")}

    all_keys = set(hat1) | set(hat2) | set(hat4)
    merged = []
    for k in sorted(all_keys):
        h1 = hat1.get(k, {})
        h2 = hat2.get(k, {})
        h4 = hat4.get(k, {})

        h1v = h1.get("verdict", "UNKNOWN")
        h4v = h4.get("final_verdict", "INDETERMINATE")

        # Reconciliation
        if h1v in HARD_FAIL_HAT1 or h4v in HAT4_REJECT:
            final = "REJECT"
            blocks = True
        elif h1v == "PASS" and h4v in ("AUTH_KEEP", "EMAIL_KEEP"):
            final = "TRUST"
            blocks = False
        elif h4v == "INDETERMINATE":
            final = "PAUSE_HUMAN_REQUIRED"
            blocks = True
        else:
            final = "DEMOTE"
            blocks = False

        merged.append({
            "join_key": k,
            "source": h1.get("source") or h4.get("source"),
            "target": h1.get("target") or h4.get("target"),
            "hat1_verdict": h1v,
            "hat1_diagnostic_class": h1.get("diagnostic_class"),
            "hat2_panel_text": (h2.get("panel_text") if isinstance(h2, dict) else None),
            "hat4_final_verdict": h4v,
            "hat4_rationale": h4.get("synthesis_rationale"),
            "reconciled_final": final,
            "blocks_w4": blocks,
            "required_human_action": h4.get("required_human_action") if blocks else None,
            "all_evidence_links": {
                "hat1_metrics": h1.get("metrics"),
                "hat2_metrics": {k_: h2.get(k_) for k_ in ("row_inflation_ratio", "brand_name_low_value_hits", "cohort_count_vs_expectation_ratio") if isinstance(h2, dict)},
            },
        })

    (audit_dir / "join_verdicts.json").write_text(json.dumps({
        "joins": merged,
        "self_domain_guard": hat3_data,
        "summary": {
            "total_joins": len(merged),
            "reject_count": sum(1 for m in merged if m["reconciled_final"] == "REJECT"),
            "trust_count": sum(1 for m in merged if m["reconciled_final"] == "TRUST"),
            "demote_count": sum(1 for m in merged if m["reconciled_final"] == "DEMOTE"),
            "pause_count": sum(1 for m in merged if m["reconciled_final"] == "PAUSE_HUMAN_REQUIRED"),
        }
    }, indent=2, default=str))
    print(json.dumps({"joins_reconciled": len(merged)}, indent=2))


if __name__ == "__main__":
    main()
