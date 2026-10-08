#!/usr/bin/env python3
"""
join_anomaly_metrics.py — W3 Hat 2 prep: cohort-level pathology per proposed
join. Row-count inflation, distribution shift, brand-name-at-low-value (Apple-at-$20),
suspicious-signature row count.

Usage:
    uv run join_anomaly_metrics.py --data-dir <path> --audit-slug <slug>
        [--user-expected-count <N>]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from scan import read_table, walk_data_dir

BRAND_WATCHLIST = {
    "Apple", "Microsoft", "Google", "Amazon", "Walmart", "JPMorgan", "Goldman",
    "Tesla", "Meta", "Netflix", "Adobe", "Salesforce", "Oracle", "IBM", "Intel",
    "Stripe", "Shopify",
}
LOW_VALUE_USD = 1000


def distribution_summary(s: pd.Series) -> dict:
    numeric = pd.to_numeric(s, errors="coerce").dropna()
    if numeric.empty:
        return {"shape": "unknown", "p50": None, "p90": None}
    return {
        "p50": float(numeric.median()),
        "p90": float(numeric.quantile(0.9)),
        "p99": float(numeric.quantile(0.99)),
        "row_count_numeric": int(len(numeric)),
    }


def detect_brand_at_low_value(joined_df: pd.DataFrame, name_col: str | None, mrr_col: str | None) -> list[dict]:
    if not name_col or not mrr_col or name_col not in joined_df.columns or mrr_col not in joined_df.columns:
        return []
    hits = []
    for _, row in joined_df.iterrows():
        n = str(row[name_col]) if pd.notna(row[name_col]) else ""
        v = pd.to_numeric(row[mrr_col], errors="coerce")
        if pd.isna(v) or v >= LOW_VALUE_USD:
            continue
        for brand in BRAND_WATCHLIST:
            if brand.lower() in n.lower():
                hits.append({"account_name": n[:60], "value": float(v), "field": mrr_col, "matched_brand": brand})
                break
    return hits[:50]  # cap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--user-expected-count", type=int, default=None)
    parser.add_argument("--name-col-hint", default="account_name",
                        help="Column name to look for as account name (used for brand-watchlist)")
    parser.add_argument("--mrr-col-hint", default="MRR",
                        help="Column name to look for as MRR (used for brand-watchlist)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    scan_dir = out_dir / "scan"
    out_dir.mkdir(parents=True, exist_ok=True)

    join_candidates = json.loads((scan_dir / "join_candidates.json").read_text())

    files = walk_data_dir(args.data_dir)
    by_file = {str(p.relative_to(args.data_dir)): p for p in files}
    df_cache: dict[str, pd.DataFrame] = {}

    results = []
    for jc in join_candidates:
        l_file, l_col = jc["left"]["table"], jc["left"]["column"]
        r_file, r_col = jc["right"]["table"], jc["right"]["column"]
        for f in (l_file, r_file):
            if f not in df_cache:
                path = by_file.get(f)
                if path is None:
                    continue
                df = read_table(path)
                if df is None:
                    continue
                df_cache[f] = df
        if l_file not in df_cache or r_file not in df_cache:
            continue
        left_df = df_cache[l_file]
        right_df = df_cache[r_file]
        if l_col not in left_df.columns or r_col not in right_df.columns:
            continue
        # Inner-join attempt for cohort-level metrics
        try:
            merged = left_df.merge(right_df, left_on=l_col, right_on=r_col, how="inner", suffixes=("_l", "_r"))
        except Exception:
            merged = pd.DataFrame()

        pre_count = int(len(left_df))
        post_count = int(len(merged))
        inflation = post_count / max(pre_count, 1)

        # Pick the first MRR-like column in the merged result for cohort distribution
        revenue_col = next((c for c in merged.columns if "mrr" in c.lower() or "revenue" in c.lower()), None)
        pre_dist = distribution_summary(left_df[revenue_col]) if revenue_col and revenue_col in left_df.columns else None
        post_dist = distribution_summary(merged[revenue_col]) if revenue_col else None

        name_col = next((c for c in merged.columns if args.name_col_hint.lower() in c.lower()), None)
        mrr_col = next((c for c in merged.columns if "mrr" in c.lower()), None)
        brand_hits = detect_brand_at_low_value(merged, name_col, mrr_col)

        cohort_ratio = None
        if args.user_expected_count:
            cohort_ratio = round(post_count / args.user_expected_count, 3)

        results.append({
            "source": {"file": l_file, "column": l_col},
            "target": {"file": r_file, "column": r_col},
            "pre_join_row_count": pre_count,
            "post_join_row_count": post_count,
            "row_inflation_ratio": round(inflation, 3),
            "pre_join_distribution_revenue": pre_dist,
            "post_join_distribution_revenue": post_dist,
            "brand_name_low_value_hits": brand_hits,
            "user_stated_expected_count": args.user_expected_count,
            "cohort_count_vs_expectation_ratio": cohort_ratio,
        })

    (out_dir / "join_anomaly_metrics_raw.json").write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps({
        "joins_evaluated": len(results),
        "inflated_joins": sum(1 for r in results if r["row_inflation_ratio"] > 1.1),
        "brand_at_low_value_hits_total": sum(len(r["brand_name_low_value_hits"]) for r in results),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
