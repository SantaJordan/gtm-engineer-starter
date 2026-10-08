#!/usr/bin/env python3
"""
referential_metrics.py — W3 Hat 1 prep: deterministic FK arithmetic per
proposed join. Computes orphan_rate, fan_out, scientific-notation count,
SF 15-vs-18 collision, etc. Emits referential_metrics_raw.json that
prompts/03a-referential-integrity.md reads.

Usage:
    uv run referential_metrics.py --data-dir <path> --audit-slug <slug>
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from scan import read_table, walk_data_dir

SCI_NOTATION_RE = re.compile(r"^\d+(\.\d+)?[Ee][+-]?\d+$")
SFDC_15_RE = re.compile(r"^[a-zA-Z0-9]{15}$")
SFDC_18_RE = re.compile(r"^[a-zA-Z0-9]{18}$")


def compute_join_metrics(left: pd.Series, right: pd.Series) -> dict:
    left = left.dropna().astype(str)
    right_dropped = right.dropna().astype(str)
    right_set = set(right_dropped)
    right_counts = right_dropped.value_counts().to_dict()

    fk_distinct = left.nunique()
    pk_distinct = len(right_set)
    orphan_count = int(sum(1 for v in left if v not in right_set))
    orphan_rate = orphan_count / max(len(left), 1)

    # fan_out: how many target rows per source value
    fan_out_per_source = [right_counts.get(v, 0) for v in left if v in right_set]
    fan_out_max = max(fan_out_per_source, default=0)
    fan_out_mean = sum(fan_out_per_source) / max(len(fan_out_per_source), 1)

    # fan_in: how many source rows per target value
    left_counts = left.value_counts()
    fan_in_max = int(left_counts.max()) if len(left_counts) else 0
    fan_in_mean = float(left_counts.mean()) if len(left_counts) else 0.0

    # Case sensitivity flip
    right_lower = {v.lower(): v for v in right_set}
    case_flip = sum(1 for v in left if v not in right_set and v.lower() in right_lower)

    # Whitespace flip
    right_trim = {v.strip(): v for v in right_set}
    ws_flip = sum(1 for v in left if v not in right_set and v.strip() in right_trim)

    # Scientific notation
    sci_count = int(sum(1 for v in left if SCI_NOTATION_RE.match(v)))

    # SF 15 vs 18 collision
    sf_collision = 0
    right_15 = {v[:15]: v for v in right_set if SFDC_18_RE.match(v)}
    for v in left:
        if SFDC_15_RE.match(v) and v in right_15 and right_15[v] != v:
            sf_collision += 1

    return {
        "fk_distinct_count": int(fk_distinct),
        "pk_distinct_count": int(pk_distinct),
        "orphan_count": orphan_count,
        "orphan_rate": round(orphan_rate, 4),
        "fan_out_max": int(fan_out_max),
        "fan_out_mean": round(fan_out_mean, 3),
        "fan_in_max": fan_in_max,
        "fan_in_mean": round(fan_in_mean, 3),
        "case_sensitivity_flip_count": int(case_flip),
        "whitespace_flip_count": int(ws_flip),
        "scientific_notation_count": sci_count,
        "sf_15v18_collision": int(sf_collision),
        "fk_fill_rate_inferred": round(len(left) / max(len(left), 1), 4),
    }


def sample_evidence(left_df: pd.DataFrame, right_df: pd.DataFrame,
                    left_col: str, right_col: str, n: int = 20) -> list[dict]:
    right_dropped = right_df[right_col].dropna().astype(str)
    right_set = set(right_dropped)
    left = left_df[left_col].dropna().astype(str)
    out = []
    # Take a mix: matches and orphans
    for i, v in enumerate(left.head(2 * n)):
        if len(out) >= n:
            break
        matched = v in right_set
        out.append({
            "source_row_index": int(left.index[i]) if i < len(left.index) else i,
            "source_fk_value": v[:60],
            "matched": matched,
            "match_method": "exact" if matched else "no_match",
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
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
        metrics = compute_join_metrics(left_df[l_col], right_df[r_col])
        evidence = sample_evidence(left_df, right_df, l_col, r_col)
        results.append({
            "source": {"file": l_file, "column": l_col},
            "target": {"file": r_file, "column": r_col},
            "declared_relationship": "many_to_one",
            "metrics": metrics,
            "evidence_sample": evidence,
        })

    (out_dir / "referential_metrics_raw.json").write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps({
        "joins_evaluated": len(results),
        "high_orphan_count": sum(1 for r in results if r["metrics"]["orphan_rate"] > 0.20),
        "scientific_notation_count": sum(1 for r in results if r["metrics"]["scientific_notation_count"] > 0),
        "case_flip_count": sum(1 for r in results if r["metrics"]["case_sensitivity_flip_count"] > 0),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
