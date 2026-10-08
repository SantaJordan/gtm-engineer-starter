#!/usr/bin/env python3
"""
contact_fill_audit.py — W5b: grade contact-level data quality. Accounts with
≥1 contact, accounts with verifiable email, accounts with LinkedIn URL,
contacts-per-account distribution, bulk-import-tenure cluster on contacts.

Usage:
    uv run contact_fill_audit.py --data-dir <path> --audit-slug <slug>
        [--contacts-file <relpath>] [--accounts-file <relpath>]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from scan import read_table


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
LINKEDIN_RE = re.compile(r"linkedin\.com/(in|company)/")


def find_table(data_dir: Path, keyword: str) -> Path | None:
    for p in data_dir.rglob("*"):
        if p.is_file() and keyword in p.name.lower() and p.suffix.lower() in (".csv", ".xlsx", ".parquet"):
            return p
    return None


def find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--contacts-file", default=None)
    parser.add_argument("--accounts-file", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    audit_dir = out_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    contacts_path = (args.data_dir / args.contacts_file) if args.contacts_file else find_table(args.data_dir, "contact")
    accounts_path = (args.data_dir / args.accounts_file) if args.accounts_file else find_table(args.data_dir, "account")

    if not contacts_path:
        (audit_dir / "contact_fill_audit.json").write_text(json.dumps({
            "note": "no contacts table found; skipping W5b"
        }, indent=2))
        print(json.dumps({"contact_fill_audited": False, "reason": "no_contacts_table"}))
        return

    contacts_df = read_table(contacts_path)
    if contacts_df is None:
        print("ERROR could not read contacts table"); return

    accounts_df = read_table(accounts_path) if accounts_path else None

    # Identify columns
    account_id_col = find_col(contacts_df, ["account_id", "AccountId", "company_id"])
    email_col = find_col(contacts_df, ["email", "Email", "primary_email"])
    linkedin_col = find_col(contacts_df, ["linkedin_url", "LinkedIn_URL", "linkedin"])
    phone_col = find_col(contacts_df, ["phone", "Phone", "telephone"])
    name_col = find_col(contacts_df, ["name", "Name", "full_name", "contact_name"])
    title_col = find_col(contacts_df, ["title", "Title", "job_title"])
    created_col = find_col(contacts_df, ["created_date", "CreatedDate", "created_at"])

    total_contacts = len(contacts_df)

    # Email validity
    if email_col:
        emails = contacts_df[email_col].dropna().astype(str)
        valid_email_count = int(sum(1 for e in emails if EMAIL_RE.match(e)))
    else:
        valid_email_count = 0

    # LinkedIn validity
    if linkedin_col:
        urls = contacts_df[linkedin_col].dropna().astype(str)
        valid_li = int(sum(1 for u in urls if LINKEDIN_RE.search(u)))
    else:
        valid_li = 0

    # Per-account aggregation
    accounts_with_contacts = set()
    accounts_with_verifiable_email = set()
    accounts_with_linkedin = set()
    accounts_with_phone = set()
    if account_id_col:
        per_acct = contacts_df.groupby(account_id_col)
        accounts_with_contacts = set(per_acct.groups.keys())
        if email_col:
            for acct_id, group in per_acct:
                emails = group[email_col].dropna().astype(str)
                if any(EMAIL_RE.match(e) for e in emails):
                    accounts_with_verifiable_email.add(acct_id)
        if linkedin_col:
            for acct_id, group in per_acct:
                urls = group[linkedin_col].dropna().astype(str)
                if any(LINKEDIN_RE.search(u) for u in urls):
                    accounts_with_linkedin.add(acct_id)
        if phone_col:
            for acct_id, group in per_acct:
                if group[phone_col].dropna().shape[0] > 0:
                    accounts_with_phone.add(acct_id)

        contacts_per_account = per_acct.size()
        p50 = int(contacts_per_account.median()) if len(contacts_per_account) else 0
        p90 = int(contacts_per_account.quantile(0.9)) if len(contacts_per_account) else 0
    else:
        contacts_per_account = pd.Series(dtype=int)
        p50 = p90 = 0

    total_accounts = int(accounts_df.shape[0]) if accounts_df is not None else len(accounts_with_contacts)
    accounts_with_zero = []
    if accounts_df is not None and account_id_col:
        accounts_id_col = find_col(accounts_df, ["Id", "id", "account_id", "AccountId"])
        if accounts_id_col:
            all_account_ids = set(accounts_df[accounts_id_col].dropna().astype(str))
            with_contacts = {str(x) for x in accounts_with_contacts}
            accounts_with_zero = list(all_account_ids - with_contacts)[:200]

    # Bulk-import detection on created_date
    bulk_cluster = None
    if created_col:
        try:
            dts = pd.to_datetime(contacts_df[created_col], errors="coerce").dropna()
            if len(dts):
                counts = dts.dt.strftime("%Y-%m-%d").value_counts()
                modal_date, modal_count = counts.index[0], int(counts.iloc[0])
                if modal_count / len(dts) > 0.05:
                    bulk_cluster = {"date": modal_date, "count": modal_count, "pct": round(modal_count / len(dts), 4)}
        except Exception:
            pass

    out = {
        "total_contacts": total_contacts,
        "total_accounts": total_accounts,
        "accounts_with_contacts_count": len(accounts_with_contacts),
        "accounts_with_contacts_pct": round(len(accounts_with_contacts) / max(total_accounts, 1), 4),
        "accounts_with_verifiable_email_pct": round(len(accounts_with_verifiable_email) / max(total_accounts, 1), 4),
        "accounts_with_linkedin_pct": round(len(accounts_with_linkedin) / max(total_accounts, 1), 4),
        "accounts_with_phone_pct": round(len(accounts_with_phone) / max(total_accounts, 1), 4),
        "contacts_per_account_p50": p50,
        "contacts_per_account_p90": p90,
        "valid_email_count": valid_email_count,
        "valid_email_rate": round(valid_email_count / max(total_contacts, 1), 4) if email_col else None,
        "valid_linkedin_count": valid_li,
        "bulk_import_cluster": bulk_cluster,
        "accounts_with_zero_contacts_sample": accounts_with_zero,
        "recommend_contact_finder_run": (len(accounts_with_zero) / max(total_accounts, 1)) > 0.20 if accounts_with_zero else False,
    }

    (audit_dir / "contact_fill_audit.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({k: v for k, v in out.items() if not isinstance(v, list)}, indent=2, default=str))


if __name__ == "__main__":
    main()
