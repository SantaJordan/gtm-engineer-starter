#!/usr/bin/env python3
"""
CSV adapter for Win-Loss Rewind Phase 0.

Reads a customer CSV, infers schema, samples rows, and writes a structured
Dataset JSON (the contract consumed by run_init.py and the data-archaeology
prompt). The Dataset shape is identical across adapters (csv / salesforce /
gong_only) so downstream phases never branch on adapter.

Dataset shape (written to --out):
{
  "adapter": "csv",
  "source_path": "<abs path>",
  "row_count": <int>,
  "column_count": <int>,
  "schema": [
    {"column": str, "dtype": str, "null_rate": float,
     "sample_values": [v1, v2, v3], "unique_count": int}
  ],
  "sample_rows": [ {col: val, ...}, ... ],         # 10-row random sample (--sample)
  "high_arr_sample": [ {col: val, ...}, ... ],     # 5 rows for Phase 0 reality check
  "arr_column_guess": str | null,
  "customers": [ {col: val, ...}, ... ],           # full row data, consumed by Phase 1+
}

Usage:
    python adapters/csv.py <path> [--sample 10] [--out outputs/<run-id>/raw_dataset.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# When this file is executed directly (`python adapters/csv.py ...`),
# Python puts `scripts/adapters/` at sys.path[0], which causes pandas's
# internal `from csv import QUOTE_NONNUMERIC` to resolve to THIS file.
# Strip the offending entry before importing pandas.
if __name__ == "__main__":
    _here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p not in ("", _here)]

import pandas as pd  # noqa: E402


# Columns that look like ARR / revenue. Used as a heuristic only — Phase 0's
# whole point is that we don't trust the user's labels, so this is just a
# best-guess to seed the high-ARR sample.
ARR_COLUMN_HINTS = (
    "arr",
    "annual_recurring_revenue",
    "annual_revenue",
    "mrr",
    "monthly_recurring_revenue",
    "amount",
    "contract_value",
    "acv",
    "tcv",
    "deal_size",
    "revenue",
)


def load_csv(path: str | Path) -> pd.DataFrame:
    """Load a CSV into a DataFrame. Lets pandas auto-infer types."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {p}")
    df = pd.read_csv(p)
    return df


def _json_safe(value):
    """Convert pandas/numpy scalars to JSON-serializable Python primitives."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError):
            pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def infer_schema(df: pd.DataFrame) -> list[dict]:
    """Infer a per-column schema summary. Returns one dict per column."""
    schema: list[dict] = []
    n = len(df)
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        # Pull up to 3 non-null sample values for human-readable orientation.
        sample_values = [_json_safe(v) for v in non_null.head(3).tolist()]
        try:
            unique_count = int(series.nunique(dropna=True))
        except TypeError:
            # Unhashable types (lists, dicts) — fall back to string-cast counts.
            unique_count = int(series.dropna().astype(str).nunique())
        null_rate = float((n - len(non_null)) / n) if n else 0.0
        schema.append(
            {
                "column": str(col),
                "dtype": str(series.dtype),
                "null_rate": round(null_rate, 4),
                "sample_values": sample_values,
                "unique_count": unique_count,
            }
        )
    return schema


def _guess_arr_column(df: pd.DataFrame) -> str | None:
    """Heuristic: pick the column whose name most looks like ARR/revenue."""
    cols_lower = {c: str(c).lower() for c in df.columns}
    # Exact-name preference.
    for col, low in cols_lower.items():
        if low in ARR_COLUMN_HINTS:
            return col
    # Substring fallback.
    for col, low in cols_lower.items():
        for hint in ARR_COLUMN_HINTS:
            if hint in low:
                return col
    return None


def pick_high_arr_sample(
    df: pd.DataFrame, n: int = 5, arr_col: str | None = None
) -> tuple[list[dict], str | None]:
    """
    Pick `n` rows for the Phase 0 reality check.

    If `arr_col` is provided (or a confident guess succeeds) we sort descending
    and take the top-N. Otherwise we fall back to a random sample — Phase 0's
    job is exactly to surface which column ARR really lives in, so we don't
    pretend to know.

    Returns: (rows_as_records, arr_column_used_or_None).
    """
    chosen = arr_col or _guess_arr_column(df)

    if chosen and chosen in df.columns:
        # Coerce to numeric for sorting; non-numeric ARR columns fall through
        # to random sampling rather than crashing.
        numeric = pd.to_numeric(df[chosen], errors="coerce")
        if numeric.notna().any():
            top = df.loc[numeric.sort_values(ascending=False).index].head(n)
            return _records(top), chosen

    # Fallback: random sample. Seeded for reproducibility within a run.
    take = min(n, len(df))
    if take == 0:
        return [], None
    sample = df.sample(n=take, random_state=42)
    return _records(sample), None


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → list of JSON-safe dicts."""
    out: list[dict] = []
    for _, row in df.iterrows():
        out.append({str(k): _json_safe(v) for k, v in row.items()})
    return out


def build_dataset(
    path: str | Path, sample_n: int = 10, arr_col: str | None = None
) -> dict:
    """Top-level: load, infer, sample. Returns the Dataset dict."""
    p = Path(path).expanduser().resolve()
    df = load_csv(p)
    schema = infer_schema(df)

    take = min(sample_n, len(df))
    sample_rows = _records(df.sample(n=take, random_state=7)) if take else []

    high_arr_sample, arr_used = pick_high_arr_sample(df, n=5, arr_col=arr_col)

    return {
        "adapter": "csv",
        "source_path": str(p),
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "schema": schema,
        "sample_rows": sample_rows,
        "high_arr_sample": high_arr_sample,
        "arr_column_guess": arr_used,
        "customers": _records(df),
    }


def _print_schema_summary(dataset: dict) -> None:
    print(
        f"Loaded {dataset['row_count']} rows × {dataset['column_count']} cols "
        f"from {dataset['source_path']}"
    )
    print(f"ARR column guess: {dataset['arr_column_guess'] or '(none — random sample used)'}")
    print("\nSchema:")
    for col in dataset["schema"]:
        print(
            f"  {col['column']:<32} {col['dtype']:<12} "
            f"null={col['null_rate']*100:5.1f}%  unique={col['unique_count']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CSV adapter for Win-Loss Rewind Phase 0 (data archaeology).",
    )
    parser.add_argument("path", help="Path to the customer CSV.")
    parser.add_argument(
        "--sample", type=int, default=10, help="Random-sample row count (default 10)."
    )
    parser.add_argument(
        "--arr-col",
        default=None,
        help="Column name to use as ARR for the high-ARR sample. Defaults to heuristic guess.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Path to write the Dataset JSON. Defaults to stdout.",
    )
    args = parser.parse_args(argv)

    dataset = build_dataset(args.path, sample_n=args.sample, arr_col=args.arr_col)

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            json.dump(dataset, f, indent=2, default=str)
        _print_schema_summary(dataset)
        print(f"\nWrote Dataset to: {out_path}")
    else:
        json.dump(dataset, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
