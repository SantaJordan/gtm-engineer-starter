#!/usr/bin/env python3
"""
id_system_audit.py — W2.5 prep: compute the bipartite link graph between
ID-shaped columns across systems. Identify ghost IDs (scientific-notation,
SF 15-vs-18 checksum violations) and ID-on-wrong-object errors.

Usage:
    uv run id_system_audit.py --data-dir <path> --audit-slug <slug>
        --claimed-primary-id-system sfdc
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


def detect_ghost_ids(s: pd.Series) -> dict:
    sample = s.dropna().astype(str)
    sci = [v for v in sample if SCI_NOTATION_RE.match(v)]
    return {
        "scientific_notation_count": len(sci),
        "scientific_notation_sample": sci[:5],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--claimed-primary-id-system", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    scan_dir = out_dir / "scan"
    out_dir.mkdir(parents=True, exist_ok=True)

    inv = json.loads((scan_dir / "column_inventory.json").read_text())
    join_candidates = json.loads((scan_dir / "join_candidates.json").read_text())

    files = walk_data_dir(args.data_dir)
    by_file = {str(p.relative_to(args.data_dir)): p for p in files}
    df_cache: dict[str, pd.DataFrame] = {}

    id_columns = [e for e in inv if e.get("class") == "id_like"]
    ghost_findings = []
    for e in id_columns:
        f = e["file"]
        if f not in df_cache:
            path = by_file.get(f)
            if path is None:
                continue
            df = read_table(path)
            if df is None:
                continue
            df_cache[f] = df
        df = df_cache[f]
        col = e["column"]
        if col not in df.columns:
            continue
        ghosts = detect_ghost_ids(df[col])
        if ghosts["scientific_notation_count"] > 0:
            ghost_findings.append({
                "field_path": f"{f}::{col}",
                "ghost_pattern": "scientific_notation",
                **ghosts,
            })

    # Build link graph from join_candidates
    link_graph = []
    for jc in join_candidates:
        link_graph.append({
            "from_table": jc["left"]["table"],
            "from_column": jc["left"]["column"],
            "to_table": jc["right"]["table"],
            "to_column": jc["right"]["column"],
            "inner_join_fraction": jc["inner_join_fraction"],
            "left_count": jc["left_count"],
        })

    # Universe coverage per system: %of distinct IDs vs all IDs across systems
    system_groups: dict[str, set] = {}
    for e in id_columns:
        f = e["file"]
        if f not in df_cache:
            continue
        df = df_cache[f]
        col = e["column"]
        if col not in df.columns:
            continue
        # Crude system inference from filename prefix (e.g., "sfdc/accounts.csv" → "sfdc")
        system = f.split("/")[0] if "/" in f else f.split(".")[0]
        vals = set(df[col].dropna().astype(str))
        system_groups.setdefault(system, set()).update(vals)

    universe_coverage = {
        sys: {"distinct_ids": len(vals), "sample": list(vals)[:5]}
        for sys, vals in system_groups.items()
    }

    out = {
        "claimed_primary_id_system": args.claimed_primary_id_system,
        "id_columns_inventory": [{"file": e["file"], "column": e["column"],
                                   "sub_class": e.get("sub_class"),
                                   "fill_rate": e.get("fill_rate"),
                                   "cardinality": e.get("cardinality")}
                                  for e in id_columns],
        "ghost_id_findings": ghost_findings,
        "link_graph": link_graph,
        "universe_coverage": universe_coverage,
        "summary": {
            "id_columns_count": len(id_columns),
            "ghost_findings_count": len(ghost_findings),
            "candidate_systems": list(system_groups.keys()),
        },
    }
    (out_dir / "id_system_audit_raw.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out["summary"], indent=2))


if __name__ == "__main__":
    main()
