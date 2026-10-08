#!/usr/bin/env python3
"""
name_cluster.py — W1b: cluster account name variants. Block-cluster by first-3-token
prefix; pairwise token-set Jaccard within block. Cross-reference against domain
matches when available.

Usage:
    uv run name_cluster.py --data-dir <path> --audit-slug <slug>
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from scan import read_table, walk_data_dir

CORP_STOPWORDS = {
    "inc", "llc", "llp", "pc", "pa", "pllc", "ltd", "lp", "corp", "corporation",
    "company", "co", "the", "gmbh", "ag", "sa", "sas", "pty", "pte", "bv", "nv",
    "kk", "oy", "ab", "as", "limited", "incorporated",
}
JACCARD_THRESHOLD = 0.85


def normalize_name(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in CORP_STOPWORDS]
    return " ".join(tokens)


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def classify_variant(raw_name: str) -> str:
    n = raw_name.strip()
    n_lower = n.lower()
    if re.search(r",?\s+a\s+[a-z]+\s+(corporation|llc|company)$", n_lower):
        return "legal_entity"
    if re.search(r"\b(inc|llc|corp|ltd)\.?$", n_lower):
        if "," in n:
            return "legal_with_comma"
        if n.isupper():
            return "all_caps_variant"
        return "legal_short"
    if " dba " in n_lower or "doing business as" in n_lower:
        return "dba"
    if n.isupper():
        return "all_caps_variant"
    return "brand"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    scan_dir = out_dir / "scan"
    out_dir.mkdir(parents=True, exist_ok=True)

    inv = json.loads((scan_dir / "column_inventory.json").read_text())

    files = walk_data_dir(args.data_dir)
    by_file = {str(p.relative_to(args.data_dir)): p for p in files}
    df_cache: dict[str, pd.DataFrame] = {}

    # Find company_name_like columns
    name_columns = [e for e in inv if e.get("class") == "company_name_like"]
    if not name_columns:
        (out_dir / "name_clusters.json").write_text(json.dumps({"clusters": []}, indent=2))
        print(json.dumps({"name_clusters": 0, "note": "no company_name_like columns detected"}))
        return

    records = []  # {row_index, file, column, raw_name, normalized}
    for entry in name_columns:
        f = entry["file"]
        if f not in df_cache:
            path = by_file.get(f)
            if path is None:
                continue
            df = read_table(path)
            if df is None:
                continue
            df_cache[f] = df
        df = df_cache[f]
        col = entry["column"]
        if col not in df.columns:
            continue
        for idx, v in df[col].items():
            if pd.isna(v):
                continue
            raw = str(v).strip()
            if not raw:
                continue
            records.append({
                "row_index": int(idx),
                "file": f,
                "column": col,
                "raw_name": raw,
                "normalized": normalize_name(raw),
            })

    # Block cluster
    blocks: dict[str, list[dict]] = {}
    for r in records:
        if not r["normalized"]:
            continue
        first_tokens = " ".join(r["normalized"].split()[:3])
        blocks.setdefault(first_tokens, []).append(r)

    clusters = []
    for block_key, block_records in blocks.items():
        if len(block_records) < 2:
            continue
        # Pairwise jaccard within block
        used = set()
        for i in range(len(block_records)):
            if i in used:
                continue
            cluster_members = [block_records[i]]
            tokens_i = set(block_records[i]["normalized"].split())
            for j in range(i + 1, len(block_records)):
                if j in used:
                    continue
                tokens_j = set(block_records[j]["normalized"].split())
                if jaccard(tokens_i, tokens_j) >= JACCARD_THRESHOLD:
                    cluster_members.append(block_records[j])
                    used.add(j)
            if len(cluster_members) >= 2:
                used.add(i)
                # Decide display + legal candidates
                brand_candidates = [m for m in cluster_members if classify_variant(m["raw_name"]) == "brand"]
                legal_candidates = [m for m in cluster_members if classify_variant(m["raw_name"]) in ("legal_entity", "legal_with_comma")]
                clusters.append({
                    "cluster_id": f"cl_{len(clusters):04d}",
                    "block_key": block_key,
                    "members": [
                        {
                            **m,
                            "variant_class": classify_variant(m["raw_name"]),
                        } for m in cluster_members
                    ],
                    "recommended_display_name": brand_candidates[0]["raw_name"] if brand_candidates else cluster_members[0]["raw_name"],
                    "recommended_legal_name": legal_candidates[0]["raw_name"] if legal_candidates else cluster_members[0]["raw_name"],
                    "evidence": [f"jaccard_{JACCARD_THRESHOLD}_within_block_{block_key}"],
                    "verdict": "MERGE_CANDIDATE",
                    "human_decision": None,
                })

    (out_dir / "name_clusters.json").write_text(json.dumps({"clusters": clusters}, indent=2, default=str))
    print(json.dumps({
        "clusters_found": len(clusters),
        "total_normalized_names": len(records),
    }, indent=2))


if __name__ == "__main__":
    main()
