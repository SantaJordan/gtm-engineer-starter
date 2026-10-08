#!/usr/bin/env python3
"""Build customer dossiers from the fictional Northwind corpus. No API keys.

This is a small, deterministic STAND-IN for the dossier-builder skill. The real
skill runs inside Claude Code: it interviews you, sends one agent per data
source, links identities, picks the revenue source, and tags every transcript
line with the Speaker Attribution Protocol. This script does a plain join of
the four demo files so you can see the OUTPUT SHAPE the real skill produces,
and so the repo's acceptance check can prove the schema and renderer work.

    python3 examples/northwind-demo/build_demo_dossiers.py out/dossiers

Inputs (examples/northwind-demo/corpus/):
    accounts.csv   one row per customer account
    contacts.csv   people at each account (and the seller's own rep)
    invoices.csv   monthly invoices, with billed location counts
    calls/*.md     call transcripts with a participant roster in the header

Output: one <account_id>.json per account that has invoices, valid against
skills/dossier-builder/schemas/dossier.customer.schema.json, and one per
closed-lost deal under <out>/prospects/, valid against dossier.prospect.schema.json.
Lost deals matter as much as customers: they are where the exclusions come from.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "skills" / "win-loss-rewind" / "scripts" / "adapters"))
import transcripts_md as _tm  # noqa: E402
CORPUS = HERE / "corpus"
TENANT_DOMAIN = "northwind.example"        # the seller's own domain: never a customer
REBUILT_AT = "2026-10-01T00:00:00Z"        # fixed so the output is reproducible
AS_OF = date(2025, 9, 1)



def read_csv(name):
    with (CORPUS / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_call(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    _, header, body = text.split("---", 2)
    meta = {}
    for key in ("call_id", "account_id", "date", "title"):
        m = re.search(rf'^{key}:\s*"(.*)"\s*$', header, re.M)
        meta[key] = m.group(1) if m else ""
    parts = []
    for m in re.finditer(r'\{party_id: "(\d+)", name: "([^"]+)", email_domain: "([^"]+)", '
                         r'affiliation: "(\w+)"\}', header):
        parts.append({"party_id": m.group(1), "name": m.group(2),
                      "email_domain": m.group(3), "affiliation": m.group(4)})
    turns = [{"label": m.group(1), "text": m.group(2).strip()}
             for m in re.finditer(r"^\[(\d+)\]:\s*(.+)$", body, re.M)]
    meta.update(participants=parts, turns=turns, file=path.name)
    return meta


def role_of(party: dict | None) -> str:
    """company | customer | unknown. Unknown is never promoted to customer."""
    if not party:
        return "unknown"
    if party["email_domain"] == TENANT_DOMAIN:
        return "company"
    if party["affiliation"] == "External":
        return "customer"
    return "unknown"


def elicitation(turn_text: str, prev: dict | None) -> str:
    """The same provisional grading rule the transcript importer uses."""
    return _tm.elicitation(turn_text, prev)


def months_between(a: date, b: date) -> float:
    return round((b - a).days / 30.44, 1)


def build(account, contacts, invoices, calls) -> dict:
    acct_id = account["account_id"]
    inv = sorted((i for i in invoices if i["account_id"] == acct_id), key=lambda i: i["invoice_date"])
    paid = [i for i in inv if i["status"] == "paid"]
    timeline, voice = [], []
    counts = {k: 0 for k in ("n_turns", "n_company", "n_customer", "n_unknown",
                             "n_unprompted", "n_led", "n_seller_echo")}
    for i in inv:
        timeline.append({
            "account_local_id": acct_id, "date": f"{i['invoice_date']}T00:00:00Z",
            "source": "billing_event", "type": f"invoice_{i['status']}",
            "subject": f"Invoice {i['invoice_id']}: ${i['amount_usd']} for {i['billed_locations']} location(s)",
            "raw_ref": {"system": "billing", "id": i["invoice_id"]},
            "fields": dict(i),
            "provenance": {"rule": "invoices_csv_by_account_id", "source": "corpus/invoices.csv",
                           "source_value": json.dumps(i)[:240]}})
    for c in calls:
        if c["account_id"] != acct_id:
            continue
        by_label = {p["party_id"]: p for p in c["participants"]}
        tagged, prev = [], None
        for t in c["turns"]:
            role = role_of(by_label.get(t["label"]))
            entry = {"speaker_label": f"[{t['label']}]", "role": role, "text": t["text"]}
            counts["n_turns"] += 1
            counts[f"n_{role}"] += 1
            if role == "customer":
                e = elicitation(t["text"], prev)
                entry["elicitation"] = e
                counts[f"n_{e}"] += 1
                voice.append({"call_id": c["call_id"], "date": c["date"],
                              "speaker_label": entry["speaker_label"], "elicitation": e,
                              "text": t["text"]})
            tagged.append(entry)
            prev = entry
        timeline.append({
            "account_local_id": acct_id, "date": c["date"], "source": "gong_call",
            "type": "call", "subject": c["title"],
            "raw_ref": {"system": "calls", "id": c["call_id"]},
            "fields": {"participants": c["participants"], "transcript_tagged": tagged},
            "provenance": {"rule": "call_header_account_id", "source": f"corpus/calls/{c['file']}"}})
    timeline.sort(key=lambda e: e["date"])

    mrr = float(paid[-1]["amount_usd"]) if paid else 0.0
    first = date.fromisoformat(account["first_won_date"]) if account["first_won_date"] else None
    end = date.fromisoformat(account["churn_date"]) if account["churn_date"] else AS_OF
    tenure_days = (end - first).days if first else None
    stage = account["stage"]
    outcome = {"Customer": "active", "Churned": "cancelled"}.get(stage, "unknown")
    n_calls = sum(1 for e in timeline if e["source"] == "gong_call")
    csm = account["csm"] or None
    status_word = "Customer" if outcome == "active" else "Cancelled"
    shown_mrr = mrr if outcome == "active" else 0
    headline = (f"{status_word} · ${shown_mrr:,.0f}/mo MRR (billing) · "
                f"{months_between(first, end) if first else 'unknown'}mo tenure (first-won) · "
                f"{n_calls} calls linked · CSM: {csm or 'none recorded'}")
    flags = []
    if outcome == "cancelled":
        flags.append({"id": "customer_but_cancelled", "severity": "note",
                      "explanation": f"Stage is {stage}; churn_date {account['churn_date']}."})
    if n_calls == 0:
        flags.append({"id": "no_calls_linked", "severity": "warn",
                      "explanation": "No call transcript links to this account."})
    locs = [int(i["billed_locations"]) for i in paid]
    critical = {"billing": bool(paid), "crm_account": True}
    important = {"calls": n_calls > 0, "csm": csm is not None}
    score = round((sum(critical.values()) + sum(important.values())) / 4, 2)
    return {
        "account_id": acct_id, "schema_version": "dossier-builder/1.0.0",
        "rebuilt_at": REBUILT_AT, "mode": "customer",
        "account_name": account["account_name"],
        "account_name_provenance": {"value": account["account_name"], "rule": "accounts_csv",
                                    "source": "corpus/accounts.csv"},
        "outcome": outcome, "headline": headline,
        "summary": {"one_liner": f"{account['account_name']}: {account['segment']}, "
                                 f"billed locations {locs[0] if locs else 0} to {locs[-1] if locs else 0}."},
        "timeline": timeline,
        "customer_voice": voice,
        "speaker_attribution_summary": counts,
        "structured_signals": {
            "mrr_snapshots": [{"as_of": paid[-1]["invoice_date"], "amount": mrr, "source": "billing"}] if paid else [],
            "arr_now": round(mrr * 12, 2) if outcome == "active" else 0.0,
            "tenure_days": tenure_days, "tenure_source": "opportunity_won",
            "subscription_state": stage, "csm": csm, "plan_tier": account["plan"] or None,
            "churn_signals": [],
        },
        "linking_trust": {"verdict": "high",
                          "basis": "Every record carries the same CRM account_id; the seller's own "
                                   f"domain ({TENANT_DOMAIN}) was stripped before any email-domain join."},
        "data_completeness": {"critical": critical, "important": important, "score": score},
        "flags": flags,
        "provenance_index": {
            "account_name": {"value": account["account_name"], "rule": "accounts_csv", "source": "corpus/accounts.csv"},
            "domain": {"value": account["website"], "rule": "accounts_csv", "source": "corpus/accounts.csv"},
            "structured_signals.mrr_snapshots": {"value": mrr, "rule": "latest_paid_invoice", "source": "corpus/invoices.csv"},
        },
        "notes_from_revops": [],
    }


def build_lost_deal(account, calls) -> dict:
    """A prospect-mode dossier for a deal that never closed."""
    acct_id = account["account_id"]
    timeline, voice = [], []
    for c in calls:
        if c["account_id"] != acct_id:
            continue
        by_label = {p["party_id"]: p for p in c["participants"]}
        tagged, prev = [], None
        for t in c["turns"]:
            role = role_of(by_label.get(t["label"]))
            entry = {"speaker_label": f"[{t['label']}]", "role": role, "text": t["text"]}
            if role == "customer":
                e = elicitation(t["text"], prev)
                entry["elicitation"] = e
                voice.append({"call_id": c["call_id"], "date": c["date"],
                              "speaker_label": entry["speaker_label"], "elicitation": e,
                              "text": t["text"]})
            tagged.append(entry)
            prev = entry
        timeline.append({
            "account_local_id": acct_id, "date": c["date"], "source": "gong_call",
            "type": "call", "subject": c["title"],
            "raw_ref": {"system": "calls", "id": c["call_id"]},
            "fields": {"participants": c["participants"], "transcript_tagged": tagged},
            "provenance": {"rule": "call_header_account_id", "source": f"corpus/calls/{c['file']}"}})
    timeline.sort(key=lambda e: e["date"])
    first_call = timeline[0]["date"] if timeline else REBUILT_AT
    return {
        "account_id": acct_id, "schema_version": "dossier-builder/1.0.0",
        "rebuilt_at": REBUILT_AT, "mode": "prospect",
        "account_name": account["account_name"], "outcome": "lost",
        "headline": f"Lost · {account['segment']} · {len(timeline)} calls linked · stage {account['stage']}",
        "timeline": timeline, "customer_voice": voice,
        "structured_signals": {"t_anchor": {"date": first_call, "rule": "first_call_date"},
                               "deal_shape": {"stage": account["stage"]}},
        "linking_trust": {"verdict": "high", "basis": "Calls carry the CRM account_id."},
        "data_completeness": {"score": 0.5, "critical": {"crm_account": True, "calls": bool(timeline)}},
        "flags": [{"id": "closed_lost", "severity": "note",
                   "explanation": "Never became a customer. Read for exclusions."}],
        "provenance_index": {"account_name": {"value": account["account_name"],
                                              "rule": "accounts_csv", "source": "corpus/accounts.csv"},
                             "domain": {"value": account["website"], "rule": "accounts_csv",
                                        "source": "corpus/accounts.csv"}},
        "notes_from_revops": [],
    }


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    accounts, contacts, invoices = read_csv("accounts.csv"), read_csv("contacts.csv"), read_csv("invoices.csv")
    calls = [parse_call(p) for p in sorted((CORPUS / "calls").glob("*.md"))]
    billed = {i["account_id"] for i in invoices}
    n = 0
    lost = 0
    for a in accounts:
        if a["account_id"] not in billed:
            # A closed-lost deal never billed: a prospect-mode dossier instead.
            (out / "prospects").mkdir(exist_ok=True)
            (out / "prospects" / f"{a['account_id']}.json").write_text(
                json.dumps(build_lost_deal(a, calls), indent=2) + "\n", encoding="utf-8")
            lost += 1
            continue
        (out / f"{a['account_id']}.json").write_text(
            json.dumps(build(a, contacts, invoices, calls), indent=2) + "\n", encoding="utf-8")
        n += 1
    print(f"wrote {n} customer dossier(s) to {out} and {lost} lost-deal dossier(s) to {out / 'prospects'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
