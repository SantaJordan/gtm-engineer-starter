#!/usr/bin/env python3
"""
column_metrics_pentad.py — W5: compute the 5-metric pentad per column.
Fill rate + cardinality + modal-value spike + NULL-encoding diversity + last-write recency.

Reads scan/column_inventory.json + raw data files. Emits column_metrics_pentad.json
consumed by the three W5 sub-agents (distribution-statistician, recency-historian,
encoding-detective).

Usage:
    uv run column_metrics_pentad.py --data-dir <path> --audit-slug <slug> [--out ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from scan import read_table, walk_data_dir

NULL_LIKE_ENCODINGS = {"NULL", "null", "", "N/A", "n/a", "NA", "na", "Unknown", "unknown",
                       "0", "-", "TBD", "tbd", "Pending", "pending", "None", "none", "NaN", "nan"}
ROUND_NUMBERS = {1000, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000, 5000000}


def detect_null_encodings(values: pd.Series) -> dict[str, int]:
    """Return {encoding: count} for every recognized NULL-like encoding present."""
    s = values.dropna().astype(str)
    found: dict[str, int] = {}
    for encoding in NULL_LIKE_ENCODINGS:
        n = int((s == encoding).sum())
        if n > 0:
            found[encoding] = n
    return found


def detect_modal_spike(values: pd.Series, min_pct: float = 0.05, min_count: int = 50) -> dict[str, Any]:
    populated = values.dropna()
    if len(populated) < min_count:
        return {"flagged": False, "reason": "sample_too_small"}
    counts = Counter(populated.astype(str))
    top_value, top_count = counts.most_common(1)[0]
    pct = top_count / len(populated)
    return {
        "flagged": pct >= min_pct and top_count >= min_count,
        "modal_value": top_value,
        "modal_count": top_count,
        "modal_pct": round(pct, 4),
    }


def detect_scientific_notation(values: pd.Series) -> int:
    s = values.dropna().astype(str)
    import re
    pattern = re.compile(r"^\d+(\.\d+)?[Ee][+-]?\d+$")
    return int(sum(1 for v in s if pattern.match(v)))


def detect_currency_mixing(values: pd.Series) -> dict[str, Any]:
    s = values.dropna().astype(str).head(1000)
    import re
    patterns = {
        "dollar_no_comma": re.compile(r"^\$?\d+(\.\d+)?$"),
        "dollar_with_comma": re.compile(r"^\$?\d{1,3}(,\d{3})+(\.\d+)?$"),
        "k_m_b_suffix": re.compile(r"^\$?\d+(\.\d+)?[KMB]$", re.IGNORECASE),
        "euro": re.compile(r"^€\d"),
        "pound": re.compile(r"^£\d"),
    }
    matches = {k: sum(1 for v in s if p.search(v)) for k, p in patterns.items()}
    diverse = sum(1 for c in matches.values() if c > 0)
    return {"format_diversity": diverse, "format_counts": matches}


def detect_modal_lastmodified(last_modified_series: pd.Series | None) -> dict[str, Any]:
    """Returns {modal_date, modal_pct, writes_last_180d_pct}."""
    if last_modified_series is None or last_modified_series.empty:
        return {"available": False}
    try:
        dt = pd.to_datetime(last_modified_series, errors="coerce").dropna()
    except Exception:
        return {"available": False, "reason": "could_not_parse"}
    if len(dt) == 0:
        return {"available": False, "reason": "no_valid_dates"}
    date_strs = dt.dt.strftime("%Y-%m-%d")
    counts = Counter(date_strs)
    modal_date, modal_count = counts.most_common(1)[0]
    now = pd.Timestamp.now(tz=dt.dt.tz)
    last_180 = (dt >= (now - pd.Timedelta(days=180))).sum()
    return {
        "available": True,
        "modal_date": modal_date,
        "modal_count": int(modal_count),
        "modal_pct": round(modal_count / len(dt), 4),
        "writes_last_180d_pct": round(int(last_180) / len(dt), 4),
        "min": str(dt.min()),
        "max": str(dt.max()),
        "row_count_with_date": int(len(dt)),
    }


def detect_distribution_shape(values: pd.Series) -> dict[str, Any]:
    """Best-effort shape classification on numeric columns."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 30:
        return {"shape": "insufficient_data"}
    try:
        import numpy as np
        log_vals = np.log10(numeric[numeric > 0] + 1)
        if len(log_vals) < 30:
            return {"shape": "non_positive_or_sparse"}
        # Crude shape detection: compare std of values vs std of logs.
        cv_raw = numeric.std() / numeric.mean() if numeric.mean() else 0
        cv_log = log_vals.std() / log_vals.mean() if log_vals.mean() else 0
        if 0.8 < cv_log < 1.5 and cv_raw > 1.0:
            shape = "log_normal"
        elif numeric.std() / max(numeric.max() - numeric.min(), 1) < 0.15:
            shape = "uniform"
        else:
            shape = "other"
        return {
            "shape": shape,
            "mean": float(numeric.mean()),
            "std": float(numeric.std()),
            "p50": float(numeric.median()),
            "p90": float(numeric.quantile(0.9)),
            "p99": float(numeric.quantile(0.99)),
            "min": float(numeric.min()),
            "max": float(numeric.max()),
        }
    except Exception as e:
        return {"shape": "compute_error", "error": str(e)}


def find_last_modified_column(df: pd.DataFrame) -> pd.Series | None:
    """Heuristic: column whose name looks like a last-modified timestamp."""
    candidates = [c for c in df.columns if any(
        kw in str(c).lower() for kw in ("last_modified", "lastmodified", "modified_at",
                                          "updated_at", "updated", "modified_date", "modified date")
    )]
    if not candidates:
        return None
    return df[candidates[0]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--scan-dir", type=Path, default=None,
                        help="Pre-computed scan dir (defaults to <out>/scan)")
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    scan_dir = args.scan_dir or (out_dir / "scan")
    out_dir.mkdir(parents=True, exist_ok=True)

    if not (scan_dir / "column_inventory.json").exists():
        print(f"ERROR: {scan_dir}/column_inventory.json missing — run scan.py first", file=sys.stderr)
        sys.exit(2)

    inv = json.loads((scan_dir / "column_inventory.json").read_text())

    files = walk_data_dir(args.data_dir)
    by_file = {str(p.relative_to(args.data_dir)): p for p in files}

    results = []
    df_cache: dict[str, pd.DataFrame] = {}
    for entry in inv:
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
        values = df[col]

        modal = detect_modal_spike(values)
        encodings = detect_null_encodings(values)
        sci_count = detect_scientific_notation(values) if entry.get("class") == "id_like" else 0
        currency = detect_currency_mixing(values) if entry.get("class") == "revenue_like" else None
        recency = detect_modal_lastmodified(find_last_modified_column(df))
        shape = detect_distribution_shape(values) if entry.get("class") in ("revenue_like", "employee_like", "numeric") else None

        results.append({
            "field_path": f"{f}::{col}",
            "file": f,
            "column": col,
            "declared_class": entry.get("class"),
            "fill_rate": entry.get("fill_rate"),
            "row_count": entry.get("row_count"),
            "cardinality": entry.get("cardinality"),
            "cardinality_ratio": entry.get("cardinality_ratio"),
            "modal_spike": modal,
            "null_encodings_observed": [
                {"encoding": k, "count": v} for k, v in encodings.items()
            ],
            "scientific_notation_count": sci_count,
            "currency_format_mixing": currency,
            "distribution_shape": shape,
            "last_modified_distribution": recency,
        })

    (out_dir / "column_metrics_pentad.json").write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps({
        "columns_analyzed": len(results),
        "modal_spike_flagged_count": sum(1 for r in results if r["modal_spike"].get("flagged")),
        "scientific_notation_columns": sum(1 for r in results if r["scientific_notation_count"] > 0),
        "null_encoding_diverse_columns": sum(1 for r in results if len(r["null_encodings_observed"]) > 1),
    }, indent=2))


if __name__ == "__main__":
    main()
