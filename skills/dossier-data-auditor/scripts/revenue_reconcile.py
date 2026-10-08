#!/usr/bin/env python3
"""
revenue_reconcile.py — W4: cluster revenue_like columns into concept groups,
apply canonical-pick rubric, run Apple-at-$20 watchlist, Benford's-law check.

Usage:
    uv run revenue_reconcile.py --data-dir <path> --audit-slug <slug>
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import pandas as pd
import yaml

from scan import read_table, walk_data_dir

ROUND_VALUES = {1000, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000}
BRAND_WATCHLIST = {
    "Apple", "Microsoft", "Google", "Amazon", "Walmart", "JPMorgan", "Goldman",
    "Tesla", "Meta", "Netflix", "Adobe", "Salesforce", "Oracle", "IBM", "Intel",
    "Stripe", "Shopify",
}


def benfords_chi_square(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    numeric = numeric[numeric > 0]
    if len(numeric) < 100:
        return None
    first_digits = numeric.astype(str).str.replace(r"^[^1-9]*", "", regex=True).str[0]
    observed = first_digits.value_counts()
    n = observed.sum()
    chi2 = 0.0
    for d in range(1, 10):
        expected = n * math.log10(1 + 1 / d)
        obs = observed.get(str(d), 0)
        chi2 += (obs - expected) ** 2 / expected
    return chi2


def cluster_revenue_columns(columns: list[dict], rubric: dict) -> dict[str, list[dict]]:
    """Cluster revenue_like columns by concept keyword match."""
    clusters = rubric.get("concept_clusters", [])
    by_cluster: dict[str, list[dict]] = {c["id"]: [] for c in clusters}
    by_cluster["unclassified"] = []
    for col in columns:
        name = (col.get("column") or "").lower()
        matched = False
        for cluster in clusters:
            for kw in cluster.get("keywords", []):
                if kw.lower() in name:
                    by_cluster[cluster["id"]].append(col)
                    matched = True
                    break
            if matched:
                break
        if not matched:
            by_cluster["unclassified"].append(col)
    return by_cluster


def score_candidate(col: dict, df: pd.DataFrame, rubric: dict, do_not_use: set[str]) -> dict:
    field_path = f"{col['file']}::{col['column']}"
    score = 0.0
    factors = []

    # Fill rate
    fr = col.get("fill_rate", 0) or 0
    score += 10.0 * fr
    factors.append(("highest_fill_rate", 10.0 * fr))

    # do_not_use
    if field_path in do_not_use or any(p in col.get("column", "") for p in rubric["selection_rules"][1]["known_patterns"]):
        score -= 100.0
        factors.append(("do_not_use_demotion", -100.0))

    # Round-number clustering
    values = pd.to_numeric(df[col["column"]], errors="coerce").dropna()
    if len(values) > 50:
        round_count = int(values.isin(ROUND_VALUES).sum())
        round_pct = round_count / len(values)
        if round_pct >= 0.10:
            score -= 3.0
            factors.append(("round_number_demotion", -3.0))

    # Benford check
    chi2 = benfords_chi_square(values) if len(values) > 100 else None
    if chi2 is not None and chi2 > 20:  # p < 0.01 threshold approximation
        score -= 2.0
        factors.append(("benford_failure", -2.0))

    return {
        "field_path": field_path,
        "column": col["column"],
        "file": col["file"],
        "fill_rate": fr,
        "score": round(score, 3),
        "factors": factors,
        "rejected": score < 0,
    }


def detect_apple_at_20(df: pd.DataFrame, name_col: str | None, mrr_col: str) -> list[dict]:
    if not name_col or name_col not in df.columns or mrr_col not in df.columns:
        return []
    hits = []
    for _, row in df.iterrows():
        n = str(row[name_col]) if pd.notna(row[name_col]) else ""
        v = pd.to_numeric(row[mrr_col], errors="coerce")
        if pd.isna(v) or v >= 1000:
            continue
        for brand in BRAND_WATCHLIST:
            if brand.lower() in n.lower():
                hits.append({"account_name": n[:60], "value": float(v), "matched_brand": brand, "column": mrr_col})
                break
    return hits[:50]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--do-not-use-fields", action="append", default=[],
                        help="Field paths to demote (can repeat)")
    parser.add_argument("--name-col-hint", default="account_name")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--skill-root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    audit_dir = out_dir / "audit"
    scan_dir = out_dir / "scan"
    audit_dir.mkdir(parents=True, exist_ok=True)

    with (args.skill_root / "config" / "revenue-rubric.yaml").open() as f:
        rubric = yaml.safe_load(f)

    inv = json.loads((scan_dir / "column_inventory.json").read_text())
    revenue_cols = [e for e in inv if e.get("class") == "revenue_like"]

    files = walk_data_dir(args.data_dir)
    by_file = {str(p.relative_to(args.data_dir)): p for p in files}
    df_cache: dict[str, pd.DataFrame] = {}

    by_cluster = cluster_revenue_columns(revenue_cols, rubric)
    do_not_use = set(args.do_not_use_fields)

    results = {"clusters": [], "apple_at_20_hits": [], "benfords_law_failures": []}

    for cluster_id, columns in by_cluster.items():
        scored_candidates = []
        for col in columns:
            f = col["file"]
            if f not in df_cache:
                path = by_file.get(f)
                if path is None:
                    continue
                df = read_table(path)
                if df is None:
                    continue
                df_cache[f] = df
            df = df_cache[f]
            if col["column"] not in df.columns:
                continue
            scored = score_candidate(col, df, rubric, do_not_use)
            scored_candidates.append(scored)

            # Apple-at-$20 per column
            name_col = next((c for c in df.columns if args.name_col_hint.lower() in c.lower()), None)
            hits = detect_apple_at_20(df, name_col, col["column"])
            results["apple_at_20_hits"].extend(hits)

        scored_candidates.sort(key=lambda x: -x["score"])
        canonical = scored_candidates[0] if scored_candidates and not scored_candidates[0]["rejected"] else None
        runner_up = scored_candidates[1] if len(scored_candidates) > 1 else None

        results["clusters"].append({
            "cluster": cluster_id,
            "candidate_columns": scored_candidates,
            "canonical_pick": canonical["field_path"] if canonical else None,
            "canonical_score": canonical["score"] if canonical else None,
            "runner_up": runner_up["field_path"] if runner_up else None,
            "margin": (canonical["score"] - runner_up["score"]) if (canonical and runner_up) else None,
        })

    (audit_dir / "revenue_findings.json").write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps({
        "clusters_scored": len(results["clusters"]),
        "apple_at_20_count": len(results["apple_at_20_hits"]),
    }, indent=2))


if __name__ == "__main__":
    main()
