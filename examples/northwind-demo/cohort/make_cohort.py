#!/usr/bin/env python3
"""Generate the fictional Northwind cohort the offline replay runs on. Deterministic.

    python3 examples/northwind-demo/cohort/make_cohort.py      # rewrites the files below

The 8-account corpus is enough to read a dossier, but far too small for a
holdout test. The cohort is 300 fictional accounts (the 8 corpus accounts plus
292 generated ones). What ships is RAW: what each vendor API would have
returned, so the replay runs the live Phase 2 parsers (`panel_parsers.py`) on it.

    customers.csv            the canonical customer CSV (wlr_schema.CUSTOMER_COLUMNS)
    recorded_raw.jsonl       raw Firecrawl / OpenWebNinja / Apify responses per account
    recorded_themes.jsonl    what Phase 4 theme extraction would have returned
    recorded_hypotheses.json what Phase 5 would have returned (typed predicates)

What is planted, so you can check the pipeline finds it and nothing else:
  * TRUE, current state: the homepage says the practice runs 2+ offices
    ("our five offices"). The live scrape parser reads this as `location_count`.
  * TRUE, dated: a news item "<Name> opened its second office ..." dated
    BEFORE the account's close date, for most multi-office accounts. Some
    single-office accounts get the same kind of article dated AFTER their close
    date (they expanded later): provably not visible at the reference date.
  * NEGATIVE: everyone else gets an old, unrelated article ("sponsors a 5K").
    An old record that does not match the signal must never count as evidence.
  * SPURIOUS: "Book an appointment online" on the homepage, by coin flip.
  * Two site-size groups (large group sites vs small sites), unrelated to
    outcome, so segmentation has real structure to find.
  * 10X: lifetime revenue >= 10x the median paying account (the START-HERE rule).

Every name and domain is invented; domains end in .example.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import random
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = int(os.environ.get("COHORT_SEED", "11"))
N_GENERATED = int(os.environ.get("COHORT_N", "292"))

CORPUS = [
    # id, name, domain, big_site, multi_office, offices, kind, lifetime_revenue, close
    ("001XX0000000101AAA", "Rivermark Dental Group", "rivermarkdental.example", True, True, 4, "good", 84240, "2024-03-01"),
    ("001XX0000000106AAA", "Copperline Dental Partners", "copperlinedental.example", True, True, 6, "good", 118920, "2024-02-01"),
    ("001XX0000000102AAA", "Birchwood Family Dentistry", "birchwooddental.example", False, True, 2, "good", 6840, "2024-06-01"),
    ("001XX0000000103AAA", "Lakeside Smiles", "lakesidesmiles.example", False, True, 3, "good", 7020, "2024-09-01"),
    ("001XX0000000105AAA", "Harbor Pediatric Dental", "harborpeds.example", False, True, 2, "good", 3240, "2025-01-01"),
    ("001XX0000000104AAA", "Summit Orthodontics", "summitortho.example", False, False, 1, "churned", 1320, "2024-05-01"),
    ("001XX0000000107AAA", "Quarry Hill Dental", "quarryhilldental.example", False, False, 1, "churned", 1100, "2024-10-01"),
    ("001XX0000000108AAA", "Elmstead Oral Surgery", "elmsteadoralsurgery.example", False, False, 1, "lost", 0, "2024-08-14"),
]
NUM = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
       "eleven", "twelve"]
ORDINAL = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}
PAIN_MULTI = "nobody at the other offices can see the open chairs"
PAIN_SOLO = "one front desk person already knows the whole schedule"
PAIN_BOOKING = "patients keep asking to book online"
FIRST = ["Alder", "Bluff", "Cedar", "Dover", "Elm", "Fern", "Glen", "Hawk", "Iris", "Juniper",
         "Kestrel", "Linden", "Maple", "North", "Oak", "Pine", "Quail", "Ridge", "Sage", "Tamarack"]
MIDDLE = ["Creek", "Hill", "Park", "Valley", "Point", "Grove", "Harbor", "Mesa", "Brook", "Field",
          "Bay", "Ford", "Glade", "Heath", "Isle"]
LAST = ["Dental Care", "Family Dental", "Smiles", "Dental Group", "Dentistry"]


def raw_responses(rng, r) -> dict:
    """What each vendor API would have returned for this account."""
    name, dom, big = r["company_name"], r["domain"], r["big"]
    if r["multi"]:
        offices = r["offices"]
        where = f"Family and general dentistry at our {NUM[offices] if offices <= 12 else offices} offices."
    else:
        where = "Family and general dentistry at our office on Main Street."
    booking = " Book an appointment online any time." if r["booking"] else " Call the front desk to book."
    md = f"# {name}\n\n{where}{booking}\n\n© 2025 {name}"
    n_links = rng.randint(50, 75) if big else rng.randint(6, 14)
    links = [f"https://{dom}/page-{i}" for i in range(n_links)]
    if big:
        links.append(f"https://{dom}/careers")
    close = dt.date.fromisoformat(r["close_date"])
    if r["news"] == "before":
        nth = ORDINAL.get(max(2, min(r["offices"], 6)), "second")
        d = close - dt.timedelta(days=rng.randint(60, 400))
        item = {"title": f"{name} expands", "link": f"https://news.example/{dom}/expansion",
                "date": d.isoformat(), "snippet": f"{name} opened its {nth} office this month.",
                "source": "Local Business Journal"}
    elif r["news"] == "after":
        d = close + dt.timedelta(days=rng.randint(90, 500))
        item = {"title": f"{name} grows", "link": f"https://news.example/{dom}/growth",
                "date": d.isoformat(), "snippet": f"{name} opened its second office downtown.",
                "source": "Local Business Journal"}
    else:
        d = dt.date(2019, rng.randint(1, 12), rng.randint(1, 28))
        item = {"title": f"{name} sponsors 5K", "link": f"https://news.example/{dom}/5k",
                "date": d.isoformat(), "snippet": f"{name} sponsored the town's spring 5K run.",
                "source": "Town Gazette"}
    return {
        "firecrawl_scrape": {"success": True, "data": {"markdown": md,
                                                       "metadata": {"title": name, "description": where}}},
        "firecrawl_map": {"success": True, "links": links},
        "openweb_ninja": {"status": "OK", "data": [{"name": name, "rating": round(rng.uniform(4.1, 4.9), 1),
                                                    "review_count": rng.randint(300, 600) if big else rng.randint(30, 90),
                                                    "phone_number": "+1 555 0100"}]},
        "apify_linkedin": [{"employeesCount": rng.randint(60, 120) if big else rng.randint(4, 15)}],
        "serper_news": {"status": "OK", "data": [item]},
    }


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    for cid, name, dom, big, multi, offices, kind, rev, close in CORPUS:
        rows.append({"customer_id": cid, "company_name": name, "domain": dom, "big": big, "multi": multi,
                     "offices": offices, "kind": kind, "lifetime_revenue": rev, "close_date": close,
                     "booking": rng.random() < 0.5})
    start = dt.date(2023, 1, 1)
    for i in range(N_GENERATED):
        big = i % 2 == 0
        multi = rng.random() < 0.5
        good = (rng.random() < 0.92) if multi else (rng.random() < 0.2)
        kind = "good" if good else ("churned" if rng.random() < 0.5 else "lost")
        rev = rng.randint(3000, 9000) if kind == "good" else (rng.randint(800, 3000) if kind == "churned" else 0)
        name = f"{FIRST[i % 20]} {MIDDLE[(i // 20) % len(MIDDLE)]} {LAST[i % 5]}"
        rows.append({"customer_id": f"NW-{i + 1:03d}", "company_name": name,
                     "domain": name.lower().replace(" ", "") + ".example", "big": big, "multi": multi,
                     "offices": rng.randint(2, 12) if multi else 1, "kind": kind, "lifetime_revenue": rev,
                     "close_date": (start + dt.timedelta(days=rng.randint(0, 700))).isoformat(),
                     "booking": rng.random() < 0.5})
    for r in rows[len(CORPUS):]:
        if r["kind"] == "good" and r["multi"] and r["big"] and rng.random() < 0.25:
            r["lifetime_revenue"] = rng.randint(80000, 140000)
    for r in rows:
        if r["customer_id"].startswith("001XX"):
            r["news"] = "before" if r["multi"] else "none"
        elif r["multi"]:
            x = rng.random()
            r["news"] = "before" if x < 0.8 else ("after" if x < 0.9 else "none")
        else:
            r["news"] = "after" if rng.random() < 0.35 else "none"

    paying = [r["lifetime_revenue"] for r in rows if r["lifetime_revenue"] > 0]
    median = statistics.median(paying)
    cols = ["customer_id", "company_name", "domain", "outcome", "health", "label", "close_date",
            "first_call_date", "churn_date", "lifetime_revenue", "label_evidence"]
    with (HERE / "customers.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            base = {k: r.get(k, "") for k in ("customer_id", "company_name", "domain", "close_date",
                                              "lifetime_revenue")}
            if r["kind"] == "good":
                lab = "10X" if r["lifetime_revenue"] >= 10 * median else "won"
                w.writerow({**base, "outcome": "won", "health": "healthy", "label": lab,
                            "label_evidence": f"lifetime ${r['lifetime_revenue']:,} vs median ${median:,.0f}"})
            elif r["kind"] == "churned":
                # The CRM column still says "won": only health shows the churn.
                w.writerow({**base, "outcome": "won", "health": "churned", "label": "lost",
                            "label_evidence": "churned"})
            else:
                w.writerow({**base, "outcome": "lost", "health": "", "label": "lost",
                            "label_evidence": "never closed"})

    prng = random.Random(SEED + 1)
    with (HERE / "recorded_raw.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({"customer_id": r["customer_id"], "raw": raw_responses(prng, r)}) + "\n")
    with (HERE / "recorded_themes.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            phrases = [PAIN_MULTI if r["multi"] else PAIN_SOLO] + ([PAIN_BOOKING] if r["booking"] else [])
            fh.write(json.dumps({"customer_id": r["customer_id"],
                                 "pain_themes": [{"theme": p, "elicitation": "unprompted"} for p in phrases]}) + "\n")

    hyps = [
        {"hypothesis_id": "H-multi-office", "signal": "the practice homepage says it runs two or more offices",
         "observability_source": "firecrawl_scrape", "queryable_field": "location_count",
         "predicate": {"op": "gte", "value": 2},
         "rationale_from_phase_4_themes": [{"phase_4_phrase": PAIN_MULTI}],
         "temporal_provability": {"best_case_tag": "ARCHIVED_BEFORE",
                                  "how": "an archived homepage dated before the close date listing the offices"},
         "archive_search_pattern": "offices"},
        {"hypothesis_id": "H-new-office-news", "signal": "a news item reports the practice opening another office",
         "observability_source": "serper_news", "queryable_field": "items.snippet",
         "predicate": {"op": "regex", "value": r"opened (its|a|their) (second|third|fourth|fifth|sixth|new) (office|location)"},
         "record_date_field": "items.date",
         "rationale_from_phase_4_themes": [{"phase_4_phrase": PAIN_MULTI}],
         "temporal_provability": {"best_case_tag": "RECORD_DATED_BEFORE",
                                  "how": "the news item's publication date, plus one day of lag"}},
        {"hypothesis_id": "H-online-booking", "signal": "the practice homepage offers online appointment booking",
         "observability_source": "firecrawl_scrape", "queryable_field": "mentions_online_booking",
         "rationale_from_phase_4_themes": [{"phase_4_phrase": PAIN_BOOKING}],
         "temporal_provability": {"best_case_tag": "ARCHIVED_BEFORE",
                                  "how": "an archived homepage dated before the close date with the booking link"},
         "archive_search_pattern": "book an appointment online"},
        {"hypothesis_id": "H-growing", "signal": "growing practices",
         "observability_source": "apify_linkedin", "queryable_field": "employees_count",
         "predicate": {"op": "gte", "value": 20},
         "rationale_from_phase_4_themes": [{"phase_4_phrase": PAIN_MULTI}],
         "temporal_provability": {"best_case_tag": "RETRO_ASSUMED", "how": "current headcount"}},
        {"hypothesis_id": "H-headcount", "signal": "practice headcount on LinkedIn",
         "observability_source": "apify_linkedin", "queryable_field": "employees_count",
         "rationale_from_phase_4_themes": [{"phase_4_phrase": PAIN_MULTI}],
         "temporal_provability": {"best_case_tag": "RETRO_ASSUMED", "how": "current headcount"}},
    ]
    (HERE / "recorded_hypotheses.json").write_text(json.dumps(
        {"_note": "Recorded phase-5 model output, applied to every archetype. H-growing must be "
                  "rejected at Gate 1 (generic). H-headcount must be rejected at Gate 4: "
                  "employees_count is numeric and the hypothesis gives no threshold.",
         "hypotheses": hyps}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} accounts; median paying lifetime revenue ${median:,.0f}; "
          f"10X = >= ${10 * median:,.0f}")


if __name__ == "__main__":
    main()
