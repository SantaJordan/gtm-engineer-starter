#!/usr/bin/env python3
"""
scan.py — W1: walk a folder, parse schemas, classify columns per column-dictionary.yaml,
test every candidate join pair for inner-join fraction. Emits scan/*.json.

Usage:
    uv run scan.py --data-dir <path> --audit-slug <slug> [--out outputs/audits/<slug>/]

Outputs:
    {out}/scan/files_inventory.json
    {out}/scan/column_inventory.json
    {out}/scan/join_candidates.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet", ".jsonl", ".json", ".sqlite", ".db"}
MAX_VALUES_PER_COLUMN_FOR_SAMPLING = 2000


def load_column_dictionary(skill_root: Path) -> dict[str, Any]:
    path = skill_root / "config" / "column-dictionary.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


def walk_data_dir(data_dir: Path) -> list[Path]:
    found: list[Path] = []
    for p in data_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            found.append(p)
    return sorted(found)


def read_table(path: Path) -> pd.DataFrame | None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(path, low_memory=False, on_bad_lines="skip")
        if suffix in (".xlsx", ".xls"):
            return pd.read_excel(path)
        if suffix == ".parquet":
            return pd.read_parquet(path)
        if suffix == ".jsonl":
            return pd.read_json(path, lines=True)
        if suffix == ".json":
            return pd.read_json(path)
        if suffix in (".sqlite", ".db"):
            # Best-effort: read every table in the SQLite file as a separate DataFrame.
            # For simplicity we concatenate only the first table here; multi-table
            # SQLite needs a CLI flag (out of scope for v1).
            import sqlite3
            with sqlite3.connect(str(path)) as conn:
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if not tables:
                    return None
                first = tables[0][0]
                return pd.read_sql(f"SELECT * FROM '{first}'", conn)
    except Exception as e:
        print(f"WARN failed to read {path}: {e}", file=sys.stderr)
        return None
    return None


def classify_column(header: str, values: pd.Series, dictionary: dict[str, Any]) -> dict[str, Any]:
    """Apply column-dictionary classifiers in order; first match wins. Returns
    {class, sub_class, match_evidence}. Falls through to 'unknown'."""
    sample = values.dropna().astype(str).head(MAX_VALUES_PER_COLUMN_FOR_SAMPLING)
    populated = len(sample)
    classes = dictionary.get("classes", {})

    for cls_name, cls_spec in classes.items():
        if cls_name == "unknown":
            continue
        # Header match
        header_match = False
        for header_re in cls_spec.get("matcher_headers", []) or []:
            try:
                if re.search(header_re, header):
                    header_match = True
                    break
            except re.error:
                continue
        # Value match
        value_match_rate = 0.0
        matcher_values = cls_spec.get("matcher_values") or {}
        if "regex" in matcher_values and populated > 0:
            try:
                pattern = re.compile(matcher_values["regex"], re.IGNORECASE)
                matches = sum(1 for v in sample if pattern.search(v))
                value_match_rate = matches / populated
            except re.error:
                pass
        elif "values_in" in matcher_values and populated > 0:
            allowed = set(matcher_values["values_in"])
            matches = sum(1 for v in sample if v in allowed)
            value_match_rate = matches / populated
        elif "min_avg_length" in matcher_values and populated > 0:
            avg_len = sample.str.len().mean()
            if avg_len >= matcher_values["min_avg_length"]:
                value_match_rate = 1.0

        min_match_rate = matcher_values.get("min_match_rate", 0.5)
        # Decision
        if header_match or value_match_rate >= min_match_rate:
            sub_class = _classify_sub(cls_spec.get("sub_classes", {}), header, sample)
            return {
                "class": cls_name,
                "sub_class": sub_class,
                "header_match": header_match,
                "value_match_rate": round(value_match_rate, 3),
            }
    return {"class": "unknown", "sub_class": None, "header_match": False, "value_match_rate": 0.0}


def _classify_sub(sub_classes: dict[str, Any], header: str, sample: pd.Series) -> str | None:
    for sub_name, sub_spec in (sub_classes or {}).items():
        for header_re in sub_spec.get("matcher_headers", []) or []:
            try:
                if re.search(header_re, header):
                    return sub_name
            except re.error:
                continue
        matcher_values = sub_spec.get("matcher_values") or {}
        if "regex" in matcher_values and len(sample) > 0:
            try:
                pattern = re.compile(matcher_values["regex"], re.IGNORECASE)
                if sum(1 for v in sample if pattern.search(v)) / len(sample) >= 0.50:
                    return sub_name
            except re.error:
                continue
    return None


def column_stats(values: pd.Series) -> dict[str, Any]:
    populated = values.dropna()
    populated_count = len(populated)
    total = len(values)
    fill_rate = round(populated_count / total, 4) if total else 0.0
    cardinality = populated.nunique() if populated_count else 0
    return {
        "row_count": int(total),
        "populated_count": int(populated_count),
        "fill_rate": fill_rate,
        "cardinality": int(cardinality),
        "cardinality_ratio": round(cardinality / total, 4) if total else 0.0,
    }


def test_join_pairs(tables: dict[str, pd.DataFrame], id_columns: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """For every pair of id_like columns from different tables, compute
    inner-join fraction. Returns sorted candidates."""
    candidates = []
    for i, (left_table, left_col) in enumerate(id_columns):
        for right_table, right_col in id_columns[i + 1:]:
            if left_table == right_table:
                continue
            try:
                left_values = tables[left_table][left_col].dropna().astype(str)
                right_values = tables[right_table][right_col].dropna().astype(str)
                if left_values.empty or right_values.empty:
                    continue
                right_set = set(right_values)
                inner = sum(1 for v in left_values if v in right_set)
                inner_fraction = inner / len(left_values)
                if inner_fraction > 0.10:  # only surface pairs with meaningful overlap
                    candidates.append({
                        "left": {"table": left_table, "column": left_col},
                        "right": {"table": right_table, "column": right_col},
                        "left_count": int(len(left_values)),
                        "right_count": int(len(right_values)),
                        "inner_join_count": int(inner),
                        "inner_join_fraction": round(inner_fraction, 4),
                    })
            except Exception:
                continue
    candidates.sort(key=lambda x: -x["inner_join_fraction"])
    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output dir (default: outputs/audits/<slug>/)")
    parser.add_argument("--skill-root", type=Path,
                        default=Path(__file__).resolve().parent.parent,
                        help="Path to dossier-data-auditor skill root")
    args = parser.parse_args()

    if not args.data_dir.exists():
        print(f"ERROR: data-dir {args.data_dir} does not exist", file=sys.stderr)
        sys.exit(2)

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    scan_dir = out_dir / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)

    dictionary = load_column_dictionary(args.skill_root)

    files = walk_data_dir(args.data_dir)
    files_inventory = []
    column_inventory: list[dict[str, Any]] = []
    tables: dict[str, pd.DataFrame] = {}
    id_columns: list[tuple[str, str]] = []

    for path in files:
        df = read_table(path)
        rel = str(path.relative_to(args.data_dir))
        if df is None:
            files_inventory.append({"path": rel, "status": "unreadable", "size_bytes": path.stat().st_size})
            continue
        tables[rel] = df
        files_inventory.append({
            "path": rel,
            "status": "ok",
            "row_count": int(len(df)),
            "column_count": int(len(df.columns)),
            "size_bytes": path.stat().st_size,
            "mtime": path.stat().st_mtime,
        })
        for col in df.columns:
            classification = classify_column(str(col), df[col], dictionary)
            stats = column_stats(df[col])
            entry = {
                "file": rel,
                "column": str(col),
                **classification,
                **stats,
            }
            column_inventory.append(entry)
            if classification["class"] == "id_like":
                id_columns.append((rel, str(col)))

    join_candidates = test_join_pairs(tables, id_columns)

    (scan_dir / "files_inventory.json").write_text(json.dumps(files_inventory, indent=2, default=str))
    (scan_dir / "column_inventory.json").write_text(json.dumps(column_inventory, indent=2, default=str))
    (scan_dir / "join_candidates.json").write_text(json.dumps(join_candidates, indent=2, default=str))

    summary = {
        "files_scanned": len(files_inventory),
        "files_ok": sum(1 for f in files_inventory if f["status"] == "ok"),
        "total_columns": len(column_inventory),
        "id_like_columns": len(id_columns),
        "join_candidates": len(join_candidates),
        "class_distribution": _count_by(column_inventory, "class"),
    }
    print(json.dumps(summary, indent=2))


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        v = it.get(key, "unknown") or "unknown"
        out[v] = out.get(v, 0) + 1
    return out


if __name__ == "__main__":
    main()
