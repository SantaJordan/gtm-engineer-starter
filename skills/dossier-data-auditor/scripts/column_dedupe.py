#!/usr/bin/env python3
"""Cluster columns by semantic concept; flag duplicates.

Implements the W4 prep step: take all revenue_like columns (or any class) and
group them into concept-clusters via keyword + value-distribution similarity.

Drives the "6 revenue columns" detection from references/revenue-audit.md.

Usage as library:
    from column_dedupe import cluster_revenue_columns

    clusters = cluster_revenue_columns(column_inventory, revenue_rubric)
    # clusters = {
    #     "probable_mrr": [col_a, col_b, col_c],
    #     "probable_arr": [col_d],
    #     "probable_deal_amount": [col_e, col_f],
    # }

CLI:
    python column_dedupe.py --audit-slug myslug --class revenue_like
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml


def cluster_columns_by_keywords(
    columns: list[dict],
    target_class: str,
    concept_clusters: list[dict],
) -> dict[str, list[dict]]:
    """Group columns of target_class into concept-clusters by header keywords.

    columns: from scan/column_inventory.json
    concept_clusters: from config/revenue-rubric.yaml#concept_clusters
    """
    clusters: dict[str, list[dict]] = {c["id"]: [] for c in concept_clusters}
    clusters["unclustered"] = []

    for col in columns:
        if target_class not in col.get("classes", []):
            continue
        header = col["column"].lower()
        assigned = False
        for cluster_def in concept_clusters:
            for kw in cluster_def.get("keywords", []):
                if re.search(r"\b" + re.escape(kw.lower()) + r"\b", header):
                    clusters[cluster_def["id"]].append(col)
                    assigned = True
                    break
            if assigned:
                break
        if not assigned:
            clusters["unclustered"].append(col)

    # Drop empty clusters
    return {k: v for k, v in clusters.items() if v}


def flag_duplicates(clusters: dict[str, list[dict]]) -> list[dict]:
    """Return flags for any cluster with ≥2 columns — the "6 revenue columns" problem."""
    flags = []
    for cluster_id, cols in clusters.items():
        if len(cols) >= 2:
            flags.append({
                "cluster_id": cluster_id,
                "n_columns": len(cols),
                "columns": [{"file": c["file"], "column": c["column"], "fill_rate": c["fill_rate"]} for c in cols],
                "severity": "warn",
                "message": (
                    f"{len(cols)} columns cluster to {cluster_id}. Pick one canonical at W4."
                    if len(cols) <= 6 else
                    f"{len(cols)} columns cluster to {cluster_id} — that's a lot. Audit for stale/legacy at W4."
                ),
            })
    return flags


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-slug", required=True)
    ap.add_argument("--class", dest="target_class", default="revenue_like",
                    help="Column class to cluster (default: revenue_like)")
    ap.add_argument("--output-base", type=Path, default=Path("outputs/audits"))
    ap.add_argument("--config-path", type=Path,
                    default=Path(__file__).parent.parent / "config" / "revenue-rubric.yaml")
    args = ap.parse_args()

    audit_dir = args.output_base / args.audit_slug
    inv_path = audit_dir / "scan" / "column_inventory.json"
    if not inv_path.exists():
        print(f"ERROR: column_inventory.json not found at {inv_path}.", file=sys.stderr)
        sys.exit(1)

    columns = json.loads(inv_path.read_text()).get("columns", [])
    rubric = yaml.safe_load(args.config_path.read_text())
    concept_clusters = rubric.get("concept_clusters", [])

    clusters = cluster_columns_by_keywords(columns, args.target_class, concept_clusters)
    flags = flag_duplicates(clusters)

    out_dir = audit_dir / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"column_clusters_{args.target_class}.json"
    out_path.write_text(json.dumps({
        "target_class": args.target_class,
        "clusters": {k: [c["column"] for c in v] for k, v in clusters.items()},
        "flags": flags,
    }, indent=2))

    print(json.dumps({
        "wrote": str(out_path),
        "n_clusters": len(clusters),
        "n_duplicate_flags": len(flags),
    }, indent=2))


if __name__ == "__main__":
    main()
