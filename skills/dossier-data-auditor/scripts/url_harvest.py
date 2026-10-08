#!/usr/bin/env python3
"""
url_harvest.py — W2a step 1: extract every URL-like value across every URL-class
column in every file. Canonicalize each value. Emit domain_inventory.json.

Usage:
    uv run url_harvest.py --data-dir <path> --audit-slug <slug>
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from scan import read_table, walk_data_dir

URL_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?:/[^\s,]*)?",
    re.IGNORECASE,
)


def canonicalize_domain(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Quick reject for emails
    if "@" in s and s.index("@") > 0:
        s = s.split("@", 1)[1]
    # Strip scheme + path
    try:
        if "://" in s:
            parsed = urlparse(s.lower())
            host = parsed.netloc or parsed.path
        else:
            host = s.lower()
        host = host.split("/")[0].split("?")[0].split("#")[0]
        if host.startswith("www."):
            host = host[4:]
        host = host.strip(".")
        if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host):
            return None
        return host
    except Exception:
        return None


def harvest_from_value(value, column_class: str) -> list[str]:
    """Given a cell value and the column's URL-sub-class, return canonicalized
    domain(s) extracted from it."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    s = str(value)
    if column_class == "email_like" or "@" in s:
        # Email: extract domain via split
        if "@" in s:
            dom = canonicalize_domain(s.split("@", 1)[1])
            return [dom] if dom else []
    # URL-shaped: try direct canonicalize first
    direct = canonicalize_domain(s)
    if direct:
        return [direct]
    # Free-text URL extraction via regex
    found = URL_REGEX.findall(s)
    out = []
    for f in found:
        c = canonicalize_domain(f)
        if c:
            out.append(c)
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

    inv = json.loads((scan_dir / "column_inventory.json").read_text())
    files = walk_data_dir(args.data_dir)
    by_file = {str(p.relative_to(args.data_dir)): p for p in files}
    df_cache: dict[str, pd.DataFrame] = {}

    url_class_columns = [e for e in inv if e.get("class") in ("domain_like", "linkedin_like", "email_like", "free_text")]

    # Group by (file, account_id_column) — but we don't have account IDs at this
    # point, so we harvest at the row level keyed by (file, row_index).
    domain_inventory: list[dict] = []
    per_account_buckets: dict[str, dict[str, list[str]]] = {}

    for entry in url_class_columns:
        f = entry["file"]
        col = entry["column"]
        if f not in df_cache:
            path = by_file.get(f)
            if path is None:
                continue
            df = read_table(path)
            if df is None:
                continue
            df_cache[f] = df
        df = df_cache[f]
        if col not in df.columns:
            continue
        for idx, val in df[col].items():
            extracted = harvest_from_value(val, entry.get("class") or "")
            for d in extracted:
                domain_inventory.append({
                    "file": f,
                    "row_index": int(idx),
                    "column": col,
                    "column_class": entry.get("class"),
                    "column_sub_class": entry.get("sub_class"),
                    "raw_value": str(val) if val is not None else None,
                    "canonical_domain": d,
                })
                key = f"{f}::{idx}"
                if key not in per_account_buckets:
                    per_account_buckets[key] = {}
                bucket = per_account_buckets[key]
                sub = entry.get("sub_class") or entry.get("class") or "unknown"
                bucket.setdefault(sub, []).append(d)

    (out_dir / "domain_inventory.json").write_text(json.dumps(domain_inventory, indent=2, default=str))

    # Compact per-account view for canonical-pick rubric
    per_account = []
    for key, buckets in per_account_buckets.items():
        file_, idx = key.split("::", 1)
        per_account.append({
            "account_row_key": key,
            "file": file_,
            "row_index": int(idx),
            "url_field_values": buckets,
        })
    (out_dir / "domain_candidates_per_account.json").write_text(json.dumps(per_account, indent=2, default=str))

    print(json.dumps({
        "rows_harvested": len(domain_inventory),
        "distinct_domains": len({d["canonical_domain"] for d in domain_inventory}),
        "account_rows_with_at_least_one_url": len(per_account),
    }, indent=2))


if __name__ == "__main__":
    main()
