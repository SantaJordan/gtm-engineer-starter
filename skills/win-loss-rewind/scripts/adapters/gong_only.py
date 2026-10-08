#!/usr/bin/env python3
"""
Gong-only adapter for Win-Loss Rewind Phase 0.

Fallback when the user has no CRM. Pulls Gong call titles for the last N
days, parses "Call with [Account Name]" patterns, and emits a customer
roster as the same Dataset shape produced by csv.py.

Honest limitation: without a CRM, we don't know outcomes (won / lost /
healthy / churned). Every row is emitted with `outcome: "unknown"` and the
data_confidence.md will surface this loudly. The purpose of this adapter is
ONLY to seed the customer roster — Phase 2 enrichment + manual labeling
have to fill in the outcome ground truth before Phase 3 can segment.

Env vars (read via python-dotenv if a .env exists in cwd):
    GONG_BASE_URL          # REQUIRED: your Gong API host, e.g. https://us-XXXXX.api.gong.io
    GONG_ACCESS_KEY
    GONG_SECRET_KEY

Usage:
    python adapters/gong_only.py [--days 365] [--out outputs/<run-id>/raw_dataset.json]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Guard: when executed as `python adapters/gong_only.py`, sys.path[0] is
# `scripts/adapters/`, which causes pandas's internal `from csv import
# QUOTE_NONNUMERIC` to find our sibling adapters/csv.py first. Strip it.
if __name__ == "__main__":
    _here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p not in ("", _here)]

import pandas as pd  # noqa: E402
import requests  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]


# -- Title parsing -----------------------------------------------------------

_TITLE_RE = re.compile(r"Call with (.+?)(?:\s+-\s+(.+))?$")

# Suffixes stripped during normalization so "Acme Inc." and "Acme LLC" match.
_NAME_SUFFIXES = (
    " inc", " inc.", " llc", " llp", " corp", " corporation",
    " co", " co.", " ltd", " limited", " pllc", " pc", " pa",
)


def extract_account_from_title(title: str) -> tuple[str | None, str | None]:
    """
    Extract (account_name, contact_name) from a Gong call title.

    Format: "Call with [Account Name] - [Contact Name]"
    Returns (None, None) if the title doesn't match.
    """
    if not title:
        return None, None
    m = _TITLE_RE.match(title)
    if not m:
        return None, None
    account = m.group(1).strip()
    contact = m.group(2).strip() if m.group(2) else None
    return account, contact


def normalize_name(name: str | None) -> str:
    """Lowercase + strip common corp suffixes + strip punctuation."""
    if not name:
        return ""
    n = name.lower()
    for suffix in _NAME_SUFFIXES:
        n = n.replace(suffix, "")
    n = re.sub(r"[^\w\s]", "", n)
    return n.strip()


# -- Gong API ----------------------------------------------------------------

def _gong_headers() -> dict[str, str]:
    access = os.environ.get("GONG_ACCESS_KEY")
    secret = os.environ.get("GONG_SECRET_KEY")
    if not access or not secret:
        raise RuntimeError(
            "GONG_ACCESS_KEY and GONG_SECRET_KEY must be set in the environment."
        )
    auth = base64.b64encode(f"{access}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}


def list_calls(base_url: str, from_iso: str, to_iso: str, cursor: str | None = None) -> dict:
    url = f"{base_url}/v2/calls"
    params: dict[str, str] = {"fromDateTime": from_iso, "toDateTime": to_iso}
    if cursor:
        params["cursor"] = cursor
    resp = requests.get(url, headers=_gong_headers(), params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def pull_call_titles(days: int = 365) -> list[dict]:
    """
    Pull every call (id + title + started) in the last `days` days.

    We deliberately don't pull transcripts here — Phase 0 only needs titles
    to seed the customer roster. Phase 1 does the heavy transcript lift.
    """
    base_url = os.environ.get("GONG_BASE_URL", "").rstrip("/")
    if not base_url:
        raise SystemExit("GONG_BASE_URL is not set: use your own Gong API host, "
                         "e.g. https://us-XXXXX.api.gong.io")
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)
    from_iso = from_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_iso = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Pulling Gong calls {from_iso[:10]} → {to_iso[:10]}", file=sys.stderr)

    all_calls: list[dict] = []
    cursor: str | None = None
    page = 1
    while True:
        print(f"  page {page}...", file=sys.stderr)
        body = list_calls(base_url, from_iso, to_iso, cursor)
        for c in body.get("calls", []):
            all_calls.append(
                {
                    "call_id": c.get("id"),
                    "title": c.get("title"),
                    "started": c.get("started"),
                    "duration_seconds": c.get("duration"),
                }
            )
        cursor = (body.get("records") or {}).get("cursor")
        if not cursor:
            break
        page += 1

    print(f"  pulled {len(all_calls)} calls", file=sys.stderr)
    return all_calls


# -- Roster construction -----------------------------------------------------

def build_customer_roster(calls: list[dict]) -> pd.DataFrame:
    """
    Collapse the call list into one row per inferred account.

    Each row has:
        company         normalized account name we matched on
        company_raw     first raw spelling we saw
        call_count      how many calls reference this account
        first_seen      earliest call started
        last_seen       latest call started
        outcome         "unknown"   (TODO: see below)

    TODO: infer outcome from call cadence.
    Heuristics under consideration (none implemented yet — too easy to get
    wrong without ground truth):
        - last_seen < (now - 90d) AND call_count >= 3 → likely "lost" or "churned"
        - call_count == 1 → likely "lost prospect" (single discovery call)
        - last_seen >= (now - 30d) AND call_count >= 5 → likely "active"
    These are guesses. Without CRM stage data we have no way to distinguish
    "lost deal" from "churned customer", and conflating them poisons every
    downstream phase. So default = "unknown" until the operator labels by hand.
    """
    by_account: dict[str, dict] = {}
    for c in calls:
        account, _ = extract_account_from_title(c.get("title") or "")
        if not account:
            continue
        norm = normalize_name(account)
        if not norm:
            continue
        bucket = by_account.setdefault(
            norm,
            {
                "company": norm,
                "company_raw": account,
                "call_count": 0,
                "first_seen": None,
                "last_seen": None,
                "call_ids": [],
            },
        )
        bucket["call_count"] += 1
        bucket["call_ids"].append(c.get("call_id"))
        started = c.get("started")
        if started:
            if bucket["first_seen"] is None or started < bucket["first_seen"]:
                bucket["first_seen"] = started
            if bucket["last_seen"] is None or started > bucket["last_seen"]:
                bucket["last_seen"] = started

    rows: list[dict] = []
    for norm, bucket in by_account.items():
        rows.append(
            {
                "company": bucket["company"],
                "company_raw": bucket["company_raw"],
                "call_count": bucket["call_count"],
                "first_seen": bucket["first_seen"],
                "last_seen": bucket["last_seen"],
                "outcome": "unknown",  # see TODO in docstring
                "call_ids": ",".join(str(x) for x in bucket["call_ids"] if x is not None),
            }
        )
    return pd.DataFrame(rows)


def build_dataset(days: int = 365, sample_n: int = 10) -> dict:
    """
    Live entry point. Mirrors csv.build_dataset shape.
    """
    if load_dotenv is not None:
        load_dotenv()

    # Lazy import so this module is importable without csv.py adapter being installed.
    from . import csv as csv_adapter  # type: ignore

    calls = pull_call_titles(days=days)
    df = build_customer_roster(calls)

    if df.empty:
        print(
            "[gong_only-adapter] No accounts inferred from call titles. "
            "Either there are no calls in the window, or titles don't match "
            "the 'Call with X' pattern. Inspect a few raw titles before retrying.",
            file=sys.stderr,
        )

    schema = csv_adapter.infer_schema(df)
    take = min(sample_n, len(df))
    sample_rows = csv_adapter._records(df.sample(n=take, random_state=7)) if take else []
    # No real ARR column here — pick_high_arr_sample will random-sample.
    high_arr_sample, arr_used = csv_adapter.pick_high_arr_sample(df, n=5)

    return {
        "adapter": "gong_only",
        "source_path": f"gong://{os.environ.get('GONG_BASE_URL', '')}?days={days}",
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "schema": schema,
        "sample_rows": sample_rows,
        "high_arr_sample": high_arr_sample,
        "arr_column_guess": arr_used,
        "customers": csv_adapter._records(df),
        "notes": [
            "outcome is 'unknown' for every row — without CRM we can't label "
            "won/lost/healthy/churned. Operator must label before Phase 3.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Gong-only adapter for Win-Loss Rewind Phase 0. Seeds the "
            "customer roster from call titles. Outcome is always 'unknown' "
            "— operator must label before Phase 3."
        ),
    )
    parser.add_argument(
        "--days", type=int, default=365, help="Lookback window in days (default 365)."
    )
    parser.add_argument(
        "--sample", type=int, default=10, help="Random-sample row count (default 10)."
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Path to write the Dataset JSON. Defaults to stdout.",
    )
    args = parser.parse_args(argv)

    dataset = build_dataset(days=args.days, sample_n=args.sample)

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            json.dump(dataset, f, indent=2, default=str)
        print(f"Wrote Dataset ({dataset['row_count']} rows) to: {out_path}")
    else:
        json.dump(dataset, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
