#!/usr/bin/env python3
"""W5 — Fill-rate cohort fitness.

Per-column fill rate + cohort-fitness verdict. Reads scan/column_inventory.json
(produced by W1 scan-agent) and any additional data files needed for fuller stats.

Verdicts per column:
    cohort_fit       — fill_rate >= 0.95
    segmentation_fit — fill_rate >= 0.80
    caveat_only      — fill_rate >= 0.30
    unfit            — fill_rate < 0.30

Silent pass-through if all critical columns land cohort_fit or segmentation_fit.

Usage:
    python fill_rate_report.py --audit-slug myslug [--full-recompute]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


CRITICAL_CLASSES = {"company_id_like", "domain_like", "company_name_like"}


def assign_verdict(fill_rate: float) -> str:
    if fill_rate >= 0.95:
        return "cohort_fit"
    if fill_rate >= 0.80:
        return "segmentation_fit"
    if fill_rate >= 0.30:
        return "caveat_only"
    return "unfit"


def build_report(column_inventory: dict) -> dict:
    """Roll up column inventory into a fill-rate report."""
    columns = column_inventory.get("columns", [])
    report_rows = []
    has_unfit_or_caveat = False
    critical_unfit = []

    for col in columns:
        verdict = assign_verdict(col.get("fill_rate", 0.0))
        is_critical = bool(set(col.get("classes", [])) & CRITICAL_CLASSES)
        row = {
            "file": col["file"],
            "column": col["column"],
            "classes": col.get("classes", []),
            "fill_rate": col.get("fill_rate", 0.0),
            "distinct_count": col.get("distinct_count", 0),
            "verdict": verdict,
            "is_critical": is_critical,
        }
        report_rows.append(row)
        if verdict in {"unfit", "caveat_only"}:
            has_unfit_or_caveat = True
            if is_critical:
                critical_unfit.append(row)

    return {
        "rows": report_rows,
        "needs_panel": has_unfit_or_caveat,
        "critical_unfit": critical_unfit,
        "summary": {
            "total_columns": len(report_rows),
            "cohort_fit": sum(1 for r in report_rows if r["verdict"] == "cohort_fit"),
            "segmentation_fit": sum(1 for r in report_rows if r["verdict"] == "segmentation_fit"),
            "caveat_only": sum(1 for r in report_rows if r["verdict"] == "caveat_only"),
            "unfit": sum(1 for r in report_rows if r["verdict"] == "unfit"),
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-slug", required=True)
    ap.add_argument("--output-base", type=Path, default=Path("outputs/audits"))
    args = ap.parse_args()

    audit_dir = args.output_base / args.audit_slug
    inv_path = audit_dir / "scan" / "column_inventory.json"
    if not inv_path.exists():
        print(f"ERROR: column_inventory.json not found at {inv_path}. Run W1 scan first.",
              file=sys.stderr)
        sys.exit(1)

    inventory = json.loads(inv_path.read_text())
    report = build_report(inventory)

    out_dir = audit_dir / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "fill_rate_findings.json"
    out_path.write_text(json.dumps(report, indent=2))

    print(json.dumps({
        "wrote": str(out_path),
        "summary": report["summary"],
        "needs_panel": report["needs_panel"],
    }, indent=2))


if __name__ == "__main__":
    main()
