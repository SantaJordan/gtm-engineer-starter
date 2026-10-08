#!/usr/bin/env python3
"""Build THE customer CSV from dossiers: one row per account, with dates and labels.

    python3 skills/win-loss-rewind/scripts/customers_from_dossiers.py \
        --dossiers outputs/dossiers/acme/dossiers --out private/customers.csv

This is the one customer file every later step uses (START-HERE steps 5, 6, 7
and 10). Columns (the canonical contract; see wlr_schema.CUSTOMER_COLUMNS):

  customer_id       the CRM account id
  company_name      account name
  domain            company domain, if a dossier record carries one
  outcome           CRM view: won, lost, or the dossier's unresolved state
                    (open, prospect, unknown), kept as is
  health            healthy (active / paying_silent) or churned (cancelled)
  label             won / lost / 10X, the VERIFIED outcome, or BLANK when unresolved:
                      lost = churned or never closed;
                      10X  = lifetime PAID revenue >= 10x the median paying account;
                      won  = every other resolved customer
                    Blank-label rows are left out of training and of the median.
  close_date        first successful payment (customers) or the deal's anchor date (lost deals)
  first_call_date   earliest call in the dossier
  churn_date        last billing date of a cancelled account
  lifetime_revenue  successful payments minus refunds (billing.py: explicit event types)
  label_evidence    the numbers behind the label, in words; add quotes by hand

Dates are what Phase 6 anchors on: a row with no close_date and no
first_call_date cannot be tested against history ("unanchorable").
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wlr_schema  # noqa: E402
import billing  # noqa: E402

TEN_X = 10


def _events(d, source):
    return [e for e in d.get("timeline") or [] if e.get("source") == source]


def _domain(d: dict, domains: list) -> str:
    prov = ((d.get("provenance_index") or {}).get("domain") or {}).get("value")
    return prov or (domains[0] if domains else "")


#: Dossier outcome -> (CRM outcome, health). Anything not listed is unresolved:
#: kept in the file with a blank label and left out of training and the median.
CUSTOMER_STATES = {"active": ("won", "healthy"), "paying_silent": ("won", "healthy"),
                   "cancelled": ("won", "churned")}
PROSPECT_STATES = {"lost": ("lost", ""), "won_recently_flipped": ("won", "healthy")}


def row_from_dossier(d: dict) -> dict:
    bill = billing.summarize(_events(d, "billing_event"))
    calls = sorted(_events(d, "gong_call"), key=lambda e: e["date"])
    domains = [p.get("email_domain") for c in calls for p in (c.get("fields") or {}).get("participants") or []
               if p.get("affiliation") == "External"]
    state = d.get("outcome") or "unknown"
    table = PROSPECT_STATES if d.get("mode") == "prospect" else CUSTOMER_STATES
    crm, health = table.get(state, (state if state in ("open", "prospect") else "unknown", ""))
    anchor = ((d.get("structured_signals") or {}).get("t_anchor") or {}).get("date", "")
    row = {"customer_id": d["account_id"], "company_name": d["account_name"],
           "domain": _domain(d, domains), "outcome": crm, "health": health,
           "first_call_date": calls[0]["date"][:10] if calls else "",
           "close_date": bill["first_paid"] or anchor[:10],
           "churn_date": bill["last_paid"] if health == "churned" else "",
           "lifetime_revenue": bill["revenue"], "_dossier_state": state,
           "_billing": bill}
    return row


def build(dossier_dirs: list[Path]) -> list[dict]:
    rows = []
    for d_dir in dossier_dirs:
        for f in sorted(Path(d_dir).glob("*.json")):
            rows.append(row_from_dossier(json.loads(f.read_text(encoding="utf-8"))))
    resolved = lambda r: r["outcome"] in ("won", "lost")  # noqa: E731
    paying = [r["lifetime_revenue"] for r in rows if resolved(r) and r["lifetime_revenue"] > 0]
    median = statistics.median(paying) if paying else 0
    for r in rows:
        bill = r.pop("_billing")
        state = r.pop("_dossier_state")
        if not resolved(r):
            r["label"] = ""
            why = f"unresolved (dossier outcome {state!r}): left out of labels, training and the median"
        elif r["outcome"] == "lost" or r["health"] == "churned":
            r["label"] = "lost"
            why = "never closed" if r["outcome"] == "lost" else f"churned; last payment {r['churn_date'] or 'unknown'}"
        elif bill["n_paid"] == 0:
            r["label"] = "won"
            why = "customer with no successful payment in the dossier; not counted in the median"
        elif median and r["lifetime_revenue"] >= TEN_X * median:
            r["label"] = "10X"
            why = (f"lifetime paid ${r['lifetime_revenue']:,.0f} = {r['lifetime_revenue'] / median:.1f}x "
                   f"the median paying account (${median:,.0f})")
        else:
            r["label"] = "won"
            why = f"paying; lifetime paid ${r['lifetime_revenue']:,.0f} over {bill['n_paid']} payment(s)"
        if bill["skipped_types"]:
            why += "; not counted: " + ", ".join(f"{k} x{v}" for k, v in sorted(bill["skipped_types"].items()))
        if bill["non_usd"]:
            why += f"; {bill['non_usd']} payment(s) not in USD, not summed"
        r["label_evidence"] = why
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dossiers", type=Path, action="append", required=True,
                    help="a folder of dossier JSONs (repeat for customers and lost deals)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    dirs = list(a.dossiers)
    for d in list(dirs):
        if (d / "prospects").is_dir() and (d / "prospects") not in dirs:
            dirs.append(d / "prospects")
    rows = build(dirs)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=wlr_schema.CUSTOMER_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in wlr_schema.CUSTOMER_COLUMNS})
    undated = [r["customer_id"] for r in rows if not (r["close_date"] or r["first_call_date"])]
    print(f"{len(rows)} accounts -> {a.out}" + (f"; {len(undated)} with no date (unanchorable)" if undated else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
