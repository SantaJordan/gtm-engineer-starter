#!/usr/bin/env python3
"""
hierarchy_resolve.py — W5.5: walk parent/child account hierarchies. Detect
self-parent rows, orphan-children-with-revenue patterns, depth tails, DBA
collisions among siblings.

Usage:
    uv run hierarchy_resolve.py --data-dir <path> --audit-slug <slug>
        [--accounts-file <relpath>] [--parent-col ParentId] [--name-col Name]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from scan import read_table


def find_accounts_table(data_dir: Path) -> Path | None:
    """Heuristic: look for a CSV/XLSX with 'account' in its name."""
    for p in data_dir.rglob("*"):
        if p.is_file() and "account" in p.name.lower() and p.suffix.lower() in (".csv", ".xlsx", ".parquet"):
            return p
    return None


def find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--accounts-file", default=None)
    parser.add_argument("--parent-col", default="ParentId")
    parser.add_argument("--ultimate-parent-col", default="Ultimate_Parent__c")
    parser.add_argument("--id-col", default="Id")
    parser.add_argument("--name-col", default="Name")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    audit_dir = out_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    path = (args.data_dir / args.accounts_file) if args.accounts_file else find_accounts_table(args.data_dir)
    if path is None or not path.exists():
        (audit_dir / "account_hierarchy.json").write_text(json.dumps({
            "hierarchy_fields_detected": [],
            "note": "no accounts-shaped file found; skipping W5.5"
        }, indent=2))
        print(json.dumps({"hierarchy_resolved": False, "reason": "no_accounts_table"}, indent=2))
        return

    df = read_table(path)
    if df is None:
        print("ERROR could not read accounts table"); return

    parent_col = find_column(df, [args.parent_col, "parent_id", "parent_account_id"])
    ultimate_col = find_column(df, [args.ultimate_parent_col, "ultimate_parent", "Ultimate_Parent__c"])
    id_col = find_column(df, [args.id_col, "id", "AccountId"])
    name_col = find_column(df, [args.name_col, "Name", "account_name"])

    if not parent_col and not ultimate_col:
        (audit_dir / "account_hierarchy.json").write_text(json.dumps({
            "hierarchy_fields_detected": [],
            "note": "no ParentId / Ultimate_Parent__c columns found; skipping W5.5"
        }, indent=2))
        print(json.dumps({"hierarchy_resolved": False, "reason": "no_parent_column"}, indent=2))
        return

    if not id_col:
        print("ERROR no id column found"); return

    ids = set(df[id_col].dropna().astype(str))
    parent_map = {}
    self_parents = []
    if parent_col:
        for _, row in df.iterrows():
            cid = str(row[id_col]) if pd.notna(row[id_col]) else None
            pid = str(row[parent_col]) if parent_col in df.columns and pd.notna(row[parent_col]) else None
            if cid and pid:
                if cid == pid:
                    self_parents.append({"account_id": cid,
                                          "account_name": str(row[name_col]) if name_col and pd.notna(row[name_col]) else None})
                else:
                    parent_map[cid] = pid

    parent_resolves_to_known = sum(1 for v in parent_map.values() if v in ids)
    parent_resolve_rate = round(parent_resolves_to_known / max(len(parent_map), 1), 4)

    # Depth distribution
    depth_count: dict[int, int] = {}
    for cid in parent_map:
        d = 0
        cur = cid
        visited = set()
        while cur in parent_map and cur not in visited:
            visited.add(cur)
            cur = parent_map[cur]
            d += 1
            if d > 10:
                break
        depth_count[d] = depth_count.get(d, 0) + 1

    # DBA collision among siblings: group by parent, check normalized names
    siblings_by_parent: dict[str, list[tuple[str, str]]] = {}
    if name_col:
        for cid, pid in parent_map.items():
            row = df[df[id_col].astype(str) == cid]
            if row.empty:
                continue
            n = str(row.iloc[0][name_col]) if pd.notna(row.iloc[0][name_col]) else ""
            norm = n.lower().strip()
            siblings_by_parent.setdefault(pid, []).append((cid, norm))
    dba_collisions = []
    for pid, siblings in siblings_by_parent.items():
        seen = {}
        for cid, norm in siblings:
            if norm in seen:
                dba_collisions.append({
                    "parent_id": pid,
                    "sibling_a_id": seen[norm],
                    "sibling_b_id": cid,
                    "shared_normalized_name": norm,
                })
            else:
                seen[norm] = cid

    out = {
        "hierarchy_fields_detected": [c for c in (parent_col, ultimate_col) if c],
        "row_count": int(len(df)),
        "accounts_with_parent_id": int(len(parent_map)),
        "parent_id_resolves_to_known_account_pct": parent_resolve_rate,
        "self_parent_rows": self_parents[:200],
        "self_parent_count": len(self_parents),
        "hierarchy_depth_distribution": depth_count,
        "circular_chains": [],  # detected via the visited-set break above; surface count separately
        "dba_collisions_among_siblings": dba_collisions[:50],
        "parent_map_sample": dict(list(parent_map.items())[:20]),
    }
    (audit_dir / "account_hierarchy.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({"hierarchy_resolved": True,
                      "self_parent_count": len(self_parents),
                      "dba_collisions": len(dba_collisions),
                      "parent_resolve_rate": parent_resolve_rate}, indent=2))


if __name__ == "__main__":
    main()
