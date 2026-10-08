#!/usr/bin/env python3
"""
Salesforce adapter for Win-Loss Rewind Phase 0.

Live adapter that pulls Account + Opportunity + Contact via SOQL through
`simple_salesforce`, then emits the same Dataset shape produced by csv.py
so downstream phases never branch on adapter.

STATUS: STUB. Auth wiring is in place. The exact SOQL queries are
intentionally NOT fabricated — the field set in any given Salesforce org is
unique (custom fields, custom objects, namespace prefixes). Each query
function raises NotImplementedError with a TODO showing the expected query
shape so the implementer fills in the org-specific fields rather than
shipping silently-wrong defaults.

Env vars (read via python-dotenv if a .env exists in cwd):
    SF_USERNAME
    SF_PASSWORD
    SF_SECURITY_TOKEN
    SF_DOMAIN          # optional, e.g. "login" (default) or "test" for sandbox

Usage:
    python adapters/salesforce.py [--out outputs/<run-id>/raw_dataset.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Guard: when executed as `python adapters/salesforce.py`, sys.path[0] is
# `scripts/adapters/`, which causes pandas's internal `from csv import
# QUOTE_NONNUMERIC` to find our sibling adapters/csv.py first. Strip it.
if __name__ == "__main__":
    _here = str(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p not in ("", _here)]

import pandas as pd  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional at import time
    load_dotenv = None  # type: ignore[assignment]


def _connect():
    """
    Establish a Salesforce connection via simple_salesforce.

    Imported lazily so `python adapters/salesforce.py --help` works without the
    SDK installed.
    """
    try:
        from simple_salesforce import Salesforce  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "simple_salesforce is required for the Salesforce adapter. "
            "Install with: pip install simple-salesforce"
        ) from e

    if load_dotenv is not None:
        load_dotenv()

    username = os.environ.get("SF_USERNAME")
    password = os.environ.get("SF_PASSWORD")
    token = os.environ.get("SF_SECURITY_TOKEN")
    domain = os.environ.get("SF_DOMAIN", "login")

    missing = [k for k, v in [
        ("SF_USERNAME", username),
        ("SF_PASSWORD", password),
        ("SF_SECURITY_TOKEN", token),
    ] if not v]
    if missing:
        raise RuntimeError(
            f"Missing Salesforce env vars: {', '.join(missing)}. "
            f"Set them in your environment or a .env file."
        )

    return Salesforce(
        username=username,
        password=password,
        security_token=token,
        domain=domain,
    )


def query_accounts(sf) -> list[dict]:
    """
    Pull Account rows.

    TODO: fill in the org-specific field list. Expected query is
    something like:

        SELECT Id, Name, Website, BillingState, Industry,
               Type, NumberOfEmployees, AnnualRevenue,
               Owner.Name,
               <custom_health_field__c>,
               <custom_arr_field__c>,
               <custom_churn_date__c>
        FROM Account
        WHERE <customer-vs-prospect filter, e.g. Type = 'Customer' or
               IsCustomer__c = true or RecordType.Name = 'Customer'>

    The "what counts as a customer" boundary is exactly the Phase 0 question
    we refuse to assume. Punt this to the operator until they tell us.
    """
    raise NotImplementedError(
        "salesforce.query_accounts: define the SOQL field list and the "
        "customer-boundary WHERE clause for this org before running. "
        "See docstring for the expected shape."
    )


def query_opportunities(sf) -> list[dict]:
    """
    Pull Opportunity rows.

    TODO: fill in. Expected SOQL:

        SELECT Id, AccountId, Name, StageName, Amount, CloseDate,
               IsClosed, IsWon, Owner.Name,
               <custom_loss_reason__c>,
               <custom_competitor__c>
        FROM Opportunity
        WHERE CloseDate >= LAST_N_MONTHS:24

    Stage labels (which stages count as Won / Lost / Open) are an org
    decision and live behind a Phase 0 question, not a default.
    """
    raise NotImplementedError(
        "salesforce.query_opportunities: define StageName mapping (won/lost) "
        "and lookback window before running. See docstring."
    )


def query_contacts(sf) -> list[dict]:
    """
    Pull Contact rows.

    TODO: fill in. Expected SOQL:

        SELECT Id, AccountId, FirstName, LastName, Title, Email, Phone,
               <custom_persona__c>
        FROM Contact
        WHERE AccountId IN (<account_ids_from_query_accounts>)
    """
    raise NotImplementedError(
        "salesforce.query_contacts: define field list and AccountId filter "
        "before running. See docstring."
    )


def _flatten_to_customer_rows(
    accounts: list[dict],
    opportunities: list[dict],
    contacts: list[dict],
) -> pd.DataFrame:
    """
    Join the three SOQL result sets into a one-row-per-customer DataFrame
    matching the shape csv.py would produce.

    TODO: how to collapse multiple opportunities per account is an
    org decision (most-recent-closed? sum of won ARR? latest stage?). Don't
    pick silently — surface the question to the operator and implement
    against their answer.
    """
    raise NotImplementedError(
        "salesforce._flatten_to_customer_rows: define the account ↔ opportunity "
        "collapse rule before running. See docstring."
    )


def build_dataset(sample_n: int = 10) -> dict:
    """
    Live entry point. Mirrors csv.build_dataset signature and Dataset shape.

    Will raise NotImplementedError until the SOQL stubs above are filled in.
    """
    # Lazy import so the module imports even when csv.py is the only adapter
    # actually being used.
    from . import csv as csv_adapter  # type: ignore

    sf = _connect()
    accounts = query_accounts(sf)
    opportunities = query_opportunities(sf)
    contacts = query_contacts(sf)
    df = _flatten_to_customer_rows(accounts, opportunities, contacts)

    schema = csv_adapter.infer_schema(df)
    take = min(sample_n, len(df))
    sample_rows = csv_adapter._records(
        df.sample(n=take, random_state=7)
    ) if take else []
    high_arr_sample, arr_used = csv_adapter.pick_high_arr_sample(df, n=5)

    return {
        "adapter": "salesforce",
        "source_path": "salesforce://" + (os.environ.get("SF_USERNAME") or "?"),
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "schema": schema,
        "sample_rows": sample_rows,
        "high_arr_sample": high_arr_sample,
        "arr_column_guess": arr_used,
        "customers": csv_adapter._records(df),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Salesforce adapter for Win-Loss Rewind Phase 0 (STUB).",
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

    try:
        dataset = build_dataset(sample_n=args.sample)
    except NotImplementedError as e:
        print(
            "[salesforce-adapter] STUB. The Salesforce SOQL queries have not "
            "been filled in for this org.\n"
            f"    {e}\n"
            "Open scripts/adapters/salesforce.py and implement the four "
            "TODO functions: query_accounts, query_opportunities, "
            "query_contacts, _flatten_to_customer_rows.",
            file=sys.stderr,
        )
        return 2

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            json.dump(dataset, f, indent=2, default=str)
        print(f"Wrote Dataset to: {out_path}")
    else:
        json.dump(dataset, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
