#!/usr/bin/env python3
"""
Phase 1.4 — Speaker Attribution.

Runs AFTER opus_audit_matches.py (which writes matched_calls.jsonl, the verified
call->customer pairs) and BEFORE Phase 2. Implements the canonical Speaker
Attribution Protocol — local inline copy at
`references/speaker-attribution-protocol.md`.

The protocol's core rule: the unit of trust in customer-voice analysis is the
UNPROMPTED CUSTOMER STATEMENT. Everything the seller said is context, never
counted as customer signal. We never silently promote an unknown speaker to
customer.

What this script does (Steps 1-4 of the protocol, the free Gong path):

  1. ROSTER (Step 1) — for each verified customer, enumerate every distinct
     party across all their matched calls, deduped to people.
  2. ROLE (Step 2) — label each party company | customer | unknown, cheapest
     first: Gong `parties[].affiliation` metadata (free, preferred) -> email
     domain compare -> UNKNOWN. No paid enrichment is needed for Gong because
     the call object already tags internal vs. external. (When a transcript has
     NO party metadata, the protocol's paid enrichment fallback applies — that
     is out of scope here and left to a future enrichment pass; we degrade to
     UNKNOWN rather than guess.)
  3. TURNS (Step 3) — tag every transcript turn company | customer | unknown by
     joining its speakerId back to the roster. Verbatim text + original
     speaker label are preserved alongside the role tag; nothing is rewritten
     or deleted. Seller turns are KEPT as context.
  4. ELICITATION (Step 4) — for every CUSTOMER turn, grade how it arose:
       - led         : the immediately preceding SELLER turn introduced the
                       same topic OR asked a leading / closed question.
       - seller_echo : the line is spoken by an internal (company) party that
                       slipped through OR it matches a high-frequency seller
                       phrase observed across many calls in this run.
       - unprompted  : neither of the above. (When in doubt between unprompted
                       and led, the protocol says choose led — conservative.)

Data shapes (why we read TWO files):
  - matched_calls.jsonl carries the VERIFIED (call_id -> customer) pairs and the
    audit verdict, but NOT the transcript or parties (see opus_audit_matches.py
    `cmd_parse`).
  - gong_calls_raw.jsonl carries the raw `parties[]` (with affiliation + email +
    speakerId) and the `transcript` (segments keyed by speakerId). We join on
    call_id to recover both.

Output (per run-id):
  outputs/<run-id>/calls_with_speaker_attribution.jsonl
    One row per verified (call, customer) pair:
    {
      "call_id": str,
      "customer_id": str,
      "customer_name": str,
      "roster": [ {name, speaker_label, role, confidence, source, email_domain} ],
      "turns": [ {speaker_label, role, text, start} ],
      "customer_statements": [
        {text, speaker, role, elicitation, call_id,
         elicitation_reason, preceding_seller_text}
      ],
      "stats": {n_turns, n_company, n_customer, n_unknown,
                n_unprompted, n_led, n_seller_echo}
    }

Usage:
    python label_speakers.py --run-id <id>
    python label_speakers.py --run-id <id> --seller-domain acme.example
    python label_speakers.py --run-id <id> --echo-min-calls 5

Env (read via python-dotenv from .env):
    SELLER_DOMAINS   # optional, comma-separated; the analyzing company's own
                     # email domain(s). Used as a fallback when a party has an
                     # email but no affiliation. Affiliation metadata is always
                     # preferred when present.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"

# A customer line counts as seller_echo if the same normalized phrase is spoken
# (by anyone) on at least this many distinct calls in the run — a constant the
# seller injects across the dataset discriminates nothing (protocol Step 4).
DEFAULT_ECHO_MIN_CALLS = 4
# Only phrases of at least this many words are eligible to be flagged as a
# high-frequency seller echo (avoids flagging "yeah", "right", "exactly").
ECHO_MIN_WORDS = 4

# Leading / closed-question cues in the immediately preceding SELLER turn that
# mark the customer's reply as LED rather than unprompted (protocol Step 4).
LEADING_CUES = [
    r"wouldn'?t it ",
    r"don'?t you ",
    r"doesn'?t it ",
    r"isn'?t it ",
    r"right\?\s*$",
    r"correct\?\s*$",
    r"\bso you'?d want\b",
    r"\bso you would want\b",
    r"would you say\b",
    r"\bwould that\b",
    r"\bdoes that (make sense|resonate|sound right)\b",
    r"\byou'?d agree\b",
    r"\bsound(s)? (good|right|fair)\b",
    r"\bmakes sense\?\s*$",
]
LEADING_RE = re.compile("|".join(LEADING_CUES), re.IGNORECASE)

# Short agreement-only customer replies (after a seller turn) are treated as LED
# regardless of topic overlap — agreement-after-framing is not independent demand.
AGREEMENT_ONLY_RE = re.compile(
    r"^\s*(yeah|yep|yes|right|exactly|totally|for sure|absolutely|sure|"
    r"correct|definitely|100%?|mm-?hm+|uh-?huh|true|agreed|that'?s right|makes sense)"
    r"[\s.,!]*$",
    re.IGNORECASE,
)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with",
    "is", "it", "that", "this", "we", "you", "i", "they", "our", "your", "their",
    "be", "are", "was", "were", "so", "do", "does", "did", "at", "as", "by",
    "if", "then", "than", "have", "has", "had", "would", "could", "should",
    "can", "will", "just", "about", "what", "how", "from", "not", "no", "yes",
}


# ──────────────────────────────────────────────────────────────
# IO helpers (style mirrors pull_gong.py / match_gong.py)
# ──────────────────────────────────────────────────────────────


def load_jsonl(path: Path):
    """Stream a JSONL file (one object per line)."""
    if not path.exists():
        raise SystemExit(f"Error: required file not found: {path}")
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def normalize_domain(domain: str | None) -> str:
    """Normalize a domain (lowercase, strip protocol/www/path). Mirrors match_gong.py."""
    if not domain:
        return ""
    d = domain.lower().strip()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    return d.split("/")[0].split("?")[0].strip()


def email_domain(email: str | None) -> str:
    """Extract a normalized domain from an email address."""
    if not email or "@" not in email:
        return ""
    return normalize_domain(email.rsplit("@", 1)[1])


# ──────────────────────────────────────────────────────────────
# Transcript flattening (Gong segments -> ordered turns)
# ──────────────────────────────────────────────────────────────


def flatten_turns(transcript: list) -> list[dict]:
    """
    Flatten a Gong transcript (list of segments) into ordered speaker turns.

    Gong segment shape: {speakerId, topic?, sentences: [{start, end, text}]}.
    We keep one turn per segment, joining its sentences, preserving speakerId
    (the audit-trail label) and the earliest start time (for ordering).
    """
    turns: list[dict] = []
    for seg in transcript or []:
        if not isinstance(seg, dict):
            # Defensive: a plain-string segment has no speaker; mark unknown.
            if isinstance(seg, str) and seg.strip():
                turns.append({"speaker_label": "", "text": seg.strip(), "start": None})
            continue
        speaker_id = seg.get("speakerId") or seg.get("speaker_id") or ""
        sentences = seg.get("sentences") or []
        texts: list[str] = []
        starts: list = []
        for s in sentences:
            if isinstance(s, dict):
                t = s.get("text")
                if t:
                    texts.append(t)
                if s.get("start") is not None:
                    starts.append(s.get("start"))
            elif isinstance(s, str) and s.strip():
                texts.append(s.strip())
        text = " ".join(texts).strip()
        if not text:
            continue
        turns.append(
            {
                "speaker_label": str(speaker_id),
                "text": text,
                "start": min(starts) if starts else None,
            }
        )
    # Order by start time when available; segments are already in order otherwise.
    turns.sort(key=lambda t: (t["start"] is None, t["start"] if t["start"] is not None else 0))
    return turns


# ──────────────────────────────────────────────────────────────
# Step 1+2 — roster + role from Gong party metadata (FREE)
# ──────────────────────────────────────────────────────────────


def build_roster(parties: list[dict], seller_domains: set[str]) -> dict[str, dict]:
    """
    Build a per-call roster keyed by speakerId. Role resolution is cheapest-first
    (protocol Step 2):
      1. metadata  — Gong parties[].affiliation (internal=company / external=customer)
      2. domain    — email domain vs. known seller domain(s)
      3. unknown   — never default to customer

    Returns: { speaker_id: {name, speaker_label, role, confidence, source, email_domain} }
    """
    roster: dict[str, dict] = {}
    for p in parties or []:
        sid = str(p.get("speakerId") or p.get("speaker_id") or "")
        if not sid:
            # No speakerId means we cannot bind this party to a transcript turn.
            continue
        name = p.get("name") or ""
        email = (p.get("emailAddress") or p.get("email") or "").strip()
        dom = email_domain(email)
        affiliation = (p.get("affiliation") or "").strip().lower()

        role = "unknown"
        confidence = 0.5
        source = "heuristic"

        if affiliation == "internal":
            role, confidence, source = "company", 0.99, "metadata"
        elif affiliation == "external":
            role, confidence, source = "customer", 0.99, "metadata"
        elif dom and seller_domains:
            if dom in seller_domains:
                role, confidence, source = "company", 0.9, "domain"
            else:
                role, confidence, source = "customer", 0.85, "domain"
        # else: stays unknown (Step 2.4 — unknown is never silently customer)

        roster[sid] = {
            "name": name,
            "speaker_label": sid,
            "role": role,
            "confidence": confidence,
            "source": source,
            "email_domain": dom,
        }
    return roster


# ──────────────────────────────────────────────────────────────
# Step 4 — elicitation grading
# ──────────────────────────────────────────────────────────────


def content_tokens(text: str) -> set[str]:
    """Lowercase content tokens (>=3 chars, non-stopword) for topic overlap."""
    toks = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


def normalize_phrase(text: str) -> str:
    """Normalize a line for cross-call echo frequency counting."""
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_led(customer_text: str, prev_seller_text: str | None) -> tuple[bool, str]:
    """
    LED if the immediately preceding SELLER turn introduced the same topic or
    asked a leading / closed question (protocol Step 4). Returns (is_led, reason).
    """
    if not prev_seller_text:
        return False, ""
    # Leading / closed question cue in the seller's preceding turn.
    if LEADING_RE.search(prev_seller_text):
        return True, "preceding seller turn asked a leading/closed question"
    # Short agreement-only reply right after a seller turn = agreement-after-framing.
    if AGREEMENT_ONLY_RE.match(customer_text or ""):
        return True, "agreement-only reply to preceding seller turn"
    # Strong topical overlap: the customer is largely echoing the seller's just-
    # introduced topic rather than raising something new.
    seller_toks = content_tokens(prev_seller_text)
    cust_toks = content_tokens(customer_text)
    if cust_toks and seller_toks:
        overlap = len(seller_toks & cust_toks) / len(cust_toks)
        if overlap >= 0.6:
            return True, f"topic introduced by preceding seller turn (overlap {overlap:.0%})"
    return False, ""


def grade_elicitation(
    customer_text: str,
    customer_role: str,
    prev_seller_text: str | None,
    echo_phrases: set[str],
) -> tuple[str, str]:
    """
    Grade one CUSTOMER turn -> unprompted | led | seller_echo (protocol Step 4).
    Returns (elicitation, reason).
    """
    # seller_echo (1): the turn is actually spoken by an internal party that
    # slipped into the "customer" path (defensive — should be rare after Step 3).
    if customer_role == "company":
        return "seller_echo", "spoken by an internal (company) party"
    # seller_echo (2): a high-frequency seller phrase seen across many calls.
    norm = normalize_phrase(customer_text)
    if norm and norm in echo_phrases:
        return "seller_echo", "matches a high-frequency phrase seen across many calls"
    # led
    led, reason = is_led(customer_text, prev_seller_text)
    if led:
        return "led", reason
    # unprompted — the gold signal
    return "unprompted", "customer raised it without seller teeing it up"


# ──────────────────────────────────────────────────────────────
# Cross-call seller-echo phrase frequency
# ──────────────────────────────────────────────────────────────


def compute_echo_phrases(
    rows: list[dict], min_calls: int
) -> set[str]:
    """
    A normalized phrase spoken on >= min_calls distinct calls is a "seller echo"
    candidate — a constant injected across the dataset (protocol Step 4). We
    count over ALL turns (company + customer + unknown), so a stock pitch line
    the rep repeats everywhere is caught even if a customer parrots it.
    Only phrases with >= ECHO_MIN_WORDS words are eligible.
    """
    phrase_calls: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        call_id = r["call_id"]
        for turn in r["_turns_flat"]:
            norm = normalize_phrase(turn["text"])
            if len(norm.split()) < ECHO_MIN_WORDS:
                continue
            phrase_calls[norm].add(call_id)
    return {p for p, calls in phrase_calls.items() if len(calls) >= min_calls}


# ──────────────────────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────────────────────


def resolve_seller_domains(arg_domains: list[str] | None) -> set[str]:
    """Collect seller domains from --seller-domain flags + SELLER_DOMAINS env."""
    out: set[str] = set()
    for d in arg_domains or []:
        nd = normalize_domain(d)
        if nd:
            out.add(nd)
    env = os.getenv("SELLER_DOMAINS", "")
    for d in env.split(","):
        nd = normalize_domain(d)
        if nd:
            out.add(nd)
    return out


def build_call_index(gong_calls_path: Path) -> dict[str, dict]:
    """Index raw Gong calls by call_id (recovers parties[] + transcript)."""
    by_id: dict[str, dict] = {}
    for call in load_jsonl(gong_calls_path):
        cid = str(call.get("call_id") or call.get("id") or "")
        if cid:
            by_id[cid] = call
    return by_id


def attribute_one(
    matched: dict,
    call: dict,
    seller_domains: set[str],
) -> dict:
    """Build the attributed record for one verified (call, customer) pair."""
    parties = call.get("parties", []) or []
    roster = build_roster(parties, seller_domains)
    turns_flat = flatten_turns(call.get("transcript") or [])

    # Step 3 — tag every turn by joining speakerId back to the roster.
    tagged_turns: list[dict] = []
    for t in turns_flat:
        sid = t["speaker_label"]
        person = roster.get(sid)
        role = person["role"] if person else "unknown"
        tagged_turns.append(
            {
                "speaker_label": sid,
                "role": role,
                "text": t["text"],
                "start": t["start"],
            }
        )
    return {
        "call_id": str(matched.get("call_id") or call.get("call_id") or ""),
        "customer_id": matched.get("customer_id"),
        "customer_name": matched.get("customer_name"),
        "roster": list(roster.values()),
        "_turns_flat": tagged_turns,  # internal; dropped before write
    }


def finalize_elicitation(rows: list[dict], echo_phrases: set[str]) -> None:
    """
    Second pass (needs the cross-call echo set first): grade every CUSTOMER turn
    and assemble customer_statements + stats. Mutates rows in place; replaces
    the internal `_turns_flat` with the public `turns` + derived fields.
    """
    for r in rows:
        tagged_turns = r.pop("_turns_flat")
        customer_statements: list[dict] = []
        prev_seller_text: str | None = None

        n_company = n_customer = n_unknown = 0
        n_unprompted = n_led = n_seller_echo = 0

        for turn in tagged_turns:
            role = turn["role"]
            if role == "company":
                n_company += 1
                # Track the most recent seller turn for LED detection.
                prev_seller_text = turn["text"]
                continue
            if role == "unknown":
                n_unknown += 1
                # Unknown is NEVER promoted to customer; it also does not become
                # the "preceding seller turn". Carry the uncertainty forward.
                continue
            # role == customer
            n_customer += 1
            elicitation, reason = grade_elicitation(
                turn["text"], role, prev_seller_text, echo_phrases
            )
            if elicitation == "unprompted":
                n_unprompted += 1
            elif elicitation == "led":
                n_led += 1
            else:
                n_seller_echo += 1
            person = next(
                (p for p in r["roster"] if p["speaker_label"] == turn["speaker_label"]),
                None,
            )
            customer_statements.append(
                {
                    "text": turn["text"],
                    "speaker": (person or {}).get("name") or turn["speaker_label"],
                    "role": role,
                    "elicitation": elicitation,
                    "call_id": r["call_id"],
                    "elicitation_reason": reason,
                    "preceding_seller_text": prev_seller_text,
                }
            )
            # A customer turn does not reset the "preceding seller" anchor here;
            # LED is judged against the IMMEDIATELY preceding SELLER turn, so we
            # leave prev_seller_text until the next company turn overwrites it.

        # Public turns (drop the internal start-ordering detail is kept; it's useful)
        r["turns"] = [
            {"speaker_label": t["speaker_label"], "role": t["role"], "text": t["text"], "start": t["start"]}
            for t in tagged_turns
        ]
        r["customer_statements"] = customer_statements
        r["stats"] = {
            "n_turns": len(tagged_turns),
            "n_company": n_company,
            "n_customer": n_customer,
            "n_unknown": n_unknown,
            "n_unprompted": n_unprompted,
            "n_led": n_led,
            "n_seller_echo": n_seller_echo,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 1.4 — tag transcript turns company|customer|unknown and grade customer statements unprompted|led|seller_echo (Speaker Attribution Protocol)."
    )
    parser.add_argument("--run-id", required=True, help="Run identifier; reads/writes outputs/<run-id>/")
    parser.add_argument(
        "--seller-domain",
        action="append",
        default=None,
        help="The analyzing company's own email domain (repeatable). Fallback only — Gong affiliation metadata is preferred. Also reads SELLER_DOMAINS env.",
    )
    parser.add_argument(
        "--echo-min-calls",
        type=int,
        default=DEFAULT_ECHO_MIN_CALLS,
        help=f"A phrase on >= N distinct calls is flagged seller_echo (default: {DEFAULT_ECHO_MIN_CALLS}).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = OUTPUTS_ROOT / args.run_id
    matched_path = run_dir / "matched_calls.jsonl"
    gong_path = run_dir / "gong_calls_raw.jsonl"
    out_path = run_dir / "calls_with_speaker_attribution.jsonl"

    if not run_dir.exists():
        print(f"Error: run directory not found: {run_dir}", flush=True)
        return 1

    seller_domains = resolve_seller_domains(args.seller_domain)
    if seller_domains:
        print(f"Seller domains (fallback): {sorted(seller_domains)}", flush=True)
    else:
        print("No seller domains provided; relying on Gong affiliation metadata only.", flush=True)

    print(f"Indexing raw Gong calls: {gong_path}", flush=True)
    calls_by_id = build_call_index(gong_path)
    print(f"  Indexed {len(calls_by_id)} calls", flush=True)

    print(f"Loading verified matches: {matched_path}", flush=True)
    matched = list(load_jsonl(matched_path))
    print(f"  {len(matched)} verified (call, customer) pairs", flush=True)

    rows: list[dict] = []
    missing_call = 0
    for m in matched:
        cid = str(m.get("call_id") or "")
        call = calls_by_id.get(cid)
        if not call:
            missing_call += 1
            continue
        rows.append(attribute_one(m, call, seller_domains))

    if missing_call:
        print(f"  WARNING: {missing_call} verified matches had no raw call in {gong_path.name}", flush=True)

    # Cross-call seller-echo set (needs all turns first), then grade.
    echo_phrases = compute_echo_phrases(rows, args.echo_min_calls)
    print(f"  Flagged {len(echo_phrases)} high-frequency phrases as seller_echo candidates (>= {args.echo_min_calls} calls)", flush=True)
    finalize_elicitation(rows, echo_phrases)

    # Aggregate stats for the operator.
    role_totals: Counter = Counter()
    elic_totals: Counter = Counter()
    for r in rows:
        s = r["stats"]
        role_totals["company"] += s["n_company"]
        role_totals["customer"] += s["n_customer"]
        role_totals["unknown"] += s["n_unknown"]
        elic_totals["unprompted"] += s["n_unprompted"]
        elic_totals["led"] += s["n_led"]
        elic_totals["seller_echo"] += s["n_seller_echo"]

    with open(out_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")

    print(f"\nWrote {len(rows)} attributed call records to: {out_path}", flush=True)
    print(f"  Turns  — company: {role_totals['company']} | customer: {role_totals['customer']} | unknown: {role_totals['unknown']}", flush=True)
    print(f"  Customer statements — unprompted: {elic_totals['unprompted']} | led: {elic_totals['led']} | seller_echo: {elic_totals['seller_echo']}", flush=True)
    total_cust = sum(elic_totals.values())
    if total_cust:
        print(f"  Unprompted share of customer voice: {elic_totals['unprompted'] / total_cust:.1%}", flush=True)
    print("\nDownstream (Phase 2/4/5) MUST consume these tags: customer-only by default, unprompted weighted highest, led discounted, seller_echo excluded from demand.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
