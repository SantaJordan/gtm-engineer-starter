#!/usr/bin/env python3
"""
schema_coerce_audit.py — W1: typed coercion audit per declared semantic class.

For every column the column-dictionary classifier tagged with a semantic class
(id_salesforce_15char, email, phone_e164, currency, date), run coercion checks.
Emits scan/column_coercion_audit.json.

Usage:
    uv run schema_coerce_audit.py --data-dir <path> --audit-slug <slug>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

from scan import read_table, walk_data_dir

REGEXES = {
    "id_salesforce_15char": re.compile(r"^[a-zA-Z0-9]{15}$"),
    "id_salesforce_18char": re.compile(r"^[a-zA-Z0-9]{18}$"),
    "id_stripe_customer": re.compile(r"^cus_[a-zA-Z0-9]+$"),
    "id_hubspot_integer": re.compile(r"^\d{4,12}$"),
    "id_npi": re.compile(r"^\d{10}$"),
    "id_dot_number": re.compile(r"^\d{6,7}$"),
    "id_duns": re.compile(r"^\d{9}$"),
    "id_ein": re.compile(r"^\d{2}-\d{7}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$"),
    "phone_e164": re.compile(r"^\+[1-9]\d{1,14}$"),
}


def coerce_currency(s: pd.Series) -> tuple[int, int, list[str]]:
    """Return (success_count, fail_count, sample_failures)."""
    success = 0
    fail = 0
    fails: list[str] = []
    for v in s.dropna().astype(str):
        cleaned = v.replace("$", "").replace(",", "").replace(" ", "").replace("€", "").replace("£", "")
        # Strip K/M/B suffix
        m = re.match(r"^(-?\d+(\.\d+)?)([KMB])?$", cleaned, re.IGNORECASE)
        if m:
            success += 1
        else:
            fail += 1
            if len(fails) < 20:
                fails.append(v)
    return success, fail, fails


def coerce_date(s: pd.Series) -> tuple[int, int, list[str], dict[str, int]]:
    """Return (success, fail, sample_failures, format_breakdown)."""
    success = 0
    fail = 0
    fails: list[str] = []
    formats: dict[str, int] = {"iso_8601": 0, "us_mdY": 0, "eu_dmY": 0, "other": 0}
    iso = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
    us = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}")
    for v in s.dropna().astype(str):
        if iso.match(v):
            formats["iso_8601"] += 1
            success += 1
        elif us.match(v):
            formats["us_mdY"] += 1
            # ambiguous DD/MM vs MM/DD without inspection
            success += 1
        else:
            try:
                pd.to_datetime(v)
                formats["other"] += 1
                success += 1
            except Exception:
                fail += 1
                if len(fails) < 20:
                    fails.append(v)
    return success, fail, fails, formats


def coerce_by_regex(s: pd.Series, regex: re.Pattern) -> tuple[int, int, list[str]]:
    success = 0
    fail = 0
    fails: list[str] = []
    for v in s.dropna().astype(str):
        if regex.match(v):
            success += 1
        else:
            fail += 1
            if len(fails) < 20:
                fails.append(v)
    return success, fail, fails


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    scan_dir = out_dir / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)

    inv = json.loads((scan_dir / "column_inventory.json").read_text())
    files = walk_data_dir(args.data_dir)
    by_file = {str(p.relative_to(args.data_dir)): p for p in files}
    df_cache: dict[str, pd.DataFrame] = {}

    results = []
    for entry in inv:
        cls = entry.get("class")
        sub = entry.get("sub_class")
        target = sub or cls
        if target not in REGEXES and target not in ("revenue_like", "date_like"):
            continue
        if entry["file"] not in df_cache:
            path = by_file.get(entry["file"])
            if path is None:
                continue
            df = read_table(path)
            if df is None:
                continue
            df_cache[entry["file"]] = df
        df = df_cache[entry["file"]]
        col = entry["column"]
        if col not in df.columns:
            continue
        series = df[col]

        if target == "revenue_like":
            success, fail, fails = coerce_currency(series)
            results.append({
                "field_path": f"{entry['file']}::{col}",
                "declared_class": cls,
                "sub_class": sub,
                "coercion": "currency",
                "success_count": success,
                "fail_count": fail,
                "coercion_failure_rate": round(fail / max(success + fail, 1), 4),
                "sample_failures": fails,
            })
        elif target == "date_like":
            success, fail, fails, formats = coerce_date(series)
            results.append({
                "field_path": f"{entry['file']}::{col}",
                "declared_class": cls,
                "sub_class": sub,
                "coercion": "date",
                "success_count": success,
                "fail_count": fail,
                "coercion_failure_rate": round(fail / max(success + fail, 1), 4),
                "format_breakdown": formats,
                "mixed_format_warning": formats["iso_8601"] > 0 and formats["us_mdY"] > 0,
                "sample_failures": fails,
            })
        elif target in REGEXES:
            success, fail, fails = coerce_by_regex(series, REGEXES[target])
            results.append({
                "field_path": f"{entry['file']}::{col}",
                "declared_class": cls,
                "sub_class": sub,
                "coercion": target,
                "success_count": success,
                "fail_count": fail,
                "coercion_failure_rate": round(fail / max(success + fail, 1), 4),
                "sample_failures": fails,
            })

    (scan_dir / "column_coercion_audit.json").write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps({
        "columns_audited": len(results),
        "high_failure_columns": [r["field_path"] for r in results if r["coercion_failure_rate"] > 0.01],
        "mixed_date_format_columns": [r["field_path"] for r in results if r.get("mixed_format_warning")],
    }, indent=2))


if __name__ == "__main__":
    main()
