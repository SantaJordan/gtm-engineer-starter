#!/usr/bin/env python3
"""
Phase 1.2 — Fuzzy-match Gong calls to customer rows.

Two match strategies:
  1. Title parse: regex `Call with (.+?)(?:\s+-\s+|$)` on call title, then
     normalize-and-Jaccard against every customer name. Score = Jaccard
     overlap of normalized tokens.
  2. Participant email domain: any external party email whose domain matches
     a customer's known domain → match (score 1.0, method=`participant_email`).

Each Gong call may produce 0, 1, or many proposed matches. Each match is one
JSONL row in `outputs/<run-id>/proposed_matches.jsonl`. Phase 1.3 audits each.

Usage:
    python match_gong.py --run-id <id>
    python match_gong.py --run-id <id> --min-score 0.7
    python match_gong.py --run-id <id> --top-k 3

Inputs:
    outputs/<run-id>/raw_dataset.json     # customer roster from Phase 0 adapter
    outputs/<run-id>/gong_calls_raw.jsonl # from pull_gong.py

Output:
    outputs/<run-id>/proposed_matches.jsonl  — one row per (call, candidate match)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"

# Customer-roster column candidates, in priority order
NAME_COL_CANDIDATES = ["company_name", "company", "account_name", "account", "name"]
DOMAIN_COL_CANDIDATES = ["domain", "company_domain", "website"]
ID_COL_CANDIDATES = ["customer_id", "account_id", "id", "sf_account_id"]

DEFAULT_MIN_SCORE = 0.5
DEFAULT_TOP_K = 5


# ──────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────


def load_customers(path: Path) -> list[dict]:
    """Load customer roster from raw_dataset.json. Accepts list[dict] or
    {"customers": [...]} or {"rows": [...]} or {"data": [...]}."""
    if not path.exists():
        raise SystemExit(f"Error: customer roster not found at {path}")
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key in ("customers", "rows", "data"):
            if key in data and isinstance(data[key], list):
                return data[key]
    if isinstance(data, list):
        return data
    raise SystemExit(
        f"Error: {path} must be a JSON array or {{\"customers\": [...]}}/{{\"rows\": [...]}}; got {type(data).__name__}"
    )


def load_calls_jsonl(path: Path):
    """Stream Gong calls JSONL (one call per line)."""
    if not path.exists():
        raise SystemExit(f"Error: Gong calls file not found at {path}")
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


# ──────────────────────────────────────────────────────────────
# Column resolution + normalization
# ──────────────────────────────────────────────────────────────


def pick_first_present(row: dict, candidates: list[str]) -> str | None:
    """Return the first candidate column whose key exists with a truthy value."""
    for col in candidates:
        if col in row and row[col] not in (None, ""):
            return col
    return None


def detect_columns(customers: list[dict]) -> tuple[str | None, str | None, str | None]:
    """Detect (name_col, domain_col, id_col) from the first non-empty row."""
    if not customers:
        return None, None, None
    sample = customers[0]
    name_col = pick_first_present(sample, NAME_COL_CANDIDATES)
    domain_col = pick_first_present(sample, DOMAIN_COL_CANDIDATES)
    id_col = pick_first_present(sample, ID_COL_CANDIDATES)
    return name_col, domain_col, id_col


def normalize_name(name: str | None) -> str:
    """Normalize a company name for fuzzy matching (lowercase, strip suffixes/punct)."""
    if not name:
        return ""
    name = name.lower()
    for suffix in [
        " inc",
        " inc.",
        " llc",
        " llp",
        " corp",
        " corporation",
        " co",
        " co.",
        " ltd",
        " limited",
        " pllc",
        " pc",
        " pa",
        " gmbh",
        " sa",
        " ag",
    ]:
        name = name.replace(suffix, "")
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def normalize_domain(domain: str | None) -> str:
    """Normalize a domain (lowercase, strip protocol/www/path)."""
    if not domain:
        return ""
    d = domain.lower().strip()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    d = d.split("/")[0].split("?")[0]
    return d.strip()


def name_tokens(name: str) -> set[str]:
    """Tokenize a normalized name into a set of meaningful tokens (>=2 chars)."""
    return {t for t in normalize_name(name).split() if len(t) >= 2}


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity over two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ──────────────────────────────────────────────────────────────
# Title parsing (Gong "Call with X - Y" convention)
# ──────────────────────────────────────────────────────────────


def extract_account_from_title(title: str | None) -> str | None:
    """Extract the account-name token from a Gong call title."""
    if not title:
        return None
    match = re.match(r"\s*Call with\s+(.+?)(?:\s+-\s+.+)?$", title, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


# ──────────────────────────────────────────────────────────────
# Customer index
# ──────────────────────────────────────────────────────────────


def build_customer_index(
    customers: list[dict],
    name_col: str,
    domain_col: str | None,
    id_col: str | None,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """
    Build:
      - canonical roster (each entry: {customer_id, customer_name, name_norm, name_tokens, domain})
      - domain_lookup: normalized_domain -> [matching customer entries]
    """
    roster: list[dict] = []
    domain_lookup: dict[str, list[dict]] = defaultdict(list)

    for i, c in enumerate(customers):
        name = c.get(name_col, "") if name_col else ""
        if not name:
            continue
        cust = {
            "customer_id": str(c.get(id_col, f"row_{i}")) if id_col else f"row_{i}",
            "customer_name": name,
            "name_norm": normalize_name(name),
            "name_tokens": name_tokens(name),
            "domain": normalize_domain(c.get(domain_col, "")) if domain_col else "",
            "_raw": c,
        }
        roster.append(cust)
        if cust["domain"]:
            domain_lookup[cust["domain"]].append(cust)

    return roster, domain_lookup


# ──────────────────────────────────────────────────────────────
# Match strategies
# ──────────────────────────────────────────────────────────────


def match_by_email_domain(
    call: dict, domain_lookup: dict[str, list[dict]]
) -> list[dict]:
    """Return matches keyed off external participant email domains."""
    matches: list[dict] = []
    seen: set[str] = set()

    for party in call.get("parties", []) or []:
        email = (party.get("emailAddress") or party.get("email") or "").strip()
        affiliation = (party.get("affiliation") or "").lower()
        if not email or "@" not in email:
            continue
        if affiliation and affiliation == "internal":
            continue
        domain = normalize_domain(email.split("@", 1)[1])
        if not domain or domain in seen:
            continue
        seen.add(domain)

        for cust in domain_lookup.get(domain, []):
            matches.append(
                {
                    "customer": cust,
                    "fuzzy_match_score": 1.0,
                    "fuzzy_match_method": "participant_email",
                    "fuzzy_match_evidence": f"external party email domain {domain} == customer.domain",
                }
            )
    return matches


def match_by_title(
    call: dict, roster: list[dict], min_score: float, top_k: int
) -> list[dict]:
    """Return up to top_k roster matches by title-Jaccard, above min_score."""
    title = call.get("title") or ""
    extracted = extract_account_from_title(title)
    if not extracted:
        return []

    extracted_norm = normalize_name(extracted)
    extracted_tokens = name_tokens(extracted)
    if not extracted_tokens:
        return []

    scored: list[tuple[float, dict, str]] = []
    for cust in roster:
        if not cust["name_tokens"]:
            continue
        # Direct normalized substring boost
        if (
            extracted_norm
            and cust["name_norm"]
            and (extracted_norm == cust["name_norm"]
                 or extracted_norm in cust["name_norm"]
                 or cust["name_norm"] in extracted_norm)
        ):
            scored.append((1.0, cust, f"title '{extracted}' normalized-substring of '{cust['customer_name']}'"))
            continue

        score = jaccard(extracted_tokens, cust["name_tokens"])
        if score >= min_score:
            evidence = (
                f"title '{extracted}' tokens {sorted(extracted_tokens)} ∩ "
                f"customer '{cust['customer_name']}' tokens {sorted(cust['name_tokens'])} "
                f"= jaccard {score:.2f}"
            )
            scored.append((score, cust, evidence))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict] = []
    for score, cust, evidence in scored[:top_k]:
        out.append(
            {
                "customer": cust,
                "fuzzy_match_score": round(score, 4),
                "fuzzy_match_method": "title_parse_jaccard",
                "fuzzy_match_evidence": evidence,
            }
        )
    return out


def merge_match_lists(
    by_email: list[dict], by_title: list[dict]
) -> list[dict]:
    """Merge two match lists, dedup by customer_id, preferring higher score."""
    by_id: dict[str, dict] = {}
    for m in by_email + by_title:
        cid = m["customer"]["customer_id"]
        existing = by_id.get(cid)
        if existing is None or m["fuzzy_match_score"] > existing["fuzzy_match_score"]:
            by_id[cid] = m
    return sorted(by_id.values(), key=lambda x: x["fuzzy_match_score"], reverse=True)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 1.2 — fuzzy-match Gong calls to customer roster"
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help=f"Minimum Jaccard score for title-parse matches (default: {DEFAULT_MIN_SCORE})",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Max number of title-parse candidates per call (default: {DEFAULT_TOP_K})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    run_dir = OUTPUTS_ROOT / args.run_id
    customer_path = run_dir / "raw_dataset.json"
    calls_path = run_dir / "gong_calls_raw.jsonl"
    out_path = run_dir / "proposed_matches.jsonl"

    if not run_dir.exists():
        print(f"Error: run directory not found: {run_dir}", flush=True)
        sys.exit(1)

    print(f"Loading customer roster: {customer_path}", flush=True)
    customers = load_customers(customer_path)
    print(f"  {len(customers)} customers", flush=True)

    name_col, domain_col, id_col = detect_columns(customers)
    if not name_col:
        print(
            f"Error: could not detect a customer-name column. Tried: {NAME_COL_CANDIDATES}",
            flush=True,
        )
        sys.exit(1)
    print(
        f"  Using columns — name: {name_col!r} | domain: {domain_col!r} | id: {id_col!r}",
        flush=True,
    )

    roster, domain_lookup = build_customer_index(customers, name_col, domain_col, id_col)
    print(
        f"  Indexed {len(roster)} customers ({len(domain_lookup)} unique domains)",
        flush=True,
    )

    # Stats
    total = 0
    matched = 0
    unmatched = 0
    multi = 0
    rows_written = 0

    print(f"\nMatching calls from: {calls_path}", flush=True)
    with open(out_path, "w") as out_f:
        for call in load_calls_jsonl(calls_path):
            total += 1
            email_matches = match_by_email_domain(call, domain_lookup)
            title_matches = match_by_title(
                call, roster, args.min_score, args.top_k
            )
            merged = merge_match_lists(email_matches, title_matches)

            if not merged:
                unmatched += 1
                continue

            matched += 1
            if len(merged) > 1:
                multi += 1

            for m in merged:
                cust = m["customer"]
                row = {
                    "call_id": call.get("call_id"),
                    "customer_id": cust["customer_id"],
                    "customer_name": cust["customer_name"],
                    "fuzzy_match_score": m["fuzzy_match_score"],
                    "fuzzy_match_method": m["fuzzy_match_method"],
                    "fuzzy_match_evidence": m["fuzzy_match_evidence"],
                }
                out_f.write(json.dumps(row, default=str) + "\n")
                rows_written += 1

    print(
        f"\nDone. Wrote {rows_written} proposed matches to: {out_path}",
        flush=True,
    )
    print(f"  Total calls scanned: {total}", flush=True)
    print(f"  Matched (≥1 candidate): {matched}", flush=True)
    print(f"  Unmatched: {unmatched}", flush=True)
    print(f"  Multi-match (≥2 candidates): {multi}", flush=True)


if __name__ == "__main__":
    main()
