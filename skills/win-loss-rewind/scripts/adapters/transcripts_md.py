#!/usr/bin/env python3
"""Import call transcripts from markdown files (the non-Gong path into Phase 1).

Writes the two files Phase 4 (batch_themes.py) reads, so a run without Gong
still has matched calls and speaker attribution:

    outputs/<run-id>/matched_calls.jsonl
    outputs/<run-id>/calls_with_speaker_attribution.jsonl

Each transcript is one markdown file with a small header and one line per turn:

    ---
    call_id: "CALL-0001"
    account_id: "001XX0000000101AAA"      # must equal the customer_id in your CSV
    date: "2024-02-20T15:00:00Z"
    title: "Intro call"
    participants:
      - {party_id: "1000001", name: "Jane Rep", email_domain: "yourco.com", affiliation: "Internal"}
      - {party_id: "1000002", name: "Dana W", email_domain: "customer.com", affiliation: "External"}
    ---
    [1000001]: What made you take the call?
    [1000002]: Honestly, the phones. ...

Roles follow the Speaker Attribution Protocol: anyone on `--seller-domain` is
`company`; an External participant on another domain is `customer`; anything
else is `unknown` (never promoted to customer). Customer lines get a
PROVISIONAL grade (`grade_basis: heuristic`, `provisional: true`): a reply to a
seller question that opens with yes/no, or echoes the question's own words, is
`led`; everything else is `unprompted`. The rule cannot read context, so a human
must confirm a line before it is quoted as unprompted.

Usage (from skills/win-loss-rewind/):
    python3 scripts/adapters/transcripts_md.py <transcripts_dir> --run-id <id> --seller-domain yourco.com
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]

LED_OPENERS = ("yes", "yeah", "yep", "no", "nope", "not really", "i guess", "sure",
               "right", "correct", "exactly")
_PART_RE = re.compile(r'\{party_id: "(?P<pid>[^"]+)", name: "(?P<name>[^"]+)", '
                      r'email_domain: "(?P<dom>[^"]+)", affiliation: "(?P<aff>\w+)"\}')


def parse_transcript(text: str, file_name: str = "") -> dict:
    try:
        _, header, body = text.split("---", 2)
    except ValueError as exc:
        raise ValueError(f"{file_name}: missing the --- header block") from exc
    meta = {}
    for key in ("call_id", "account_id", "date", "title"):
        m = re.search(rf'^{key}:\s*"(.*)"\s*$', header, re.M)
        meta[key] = m.group(1) if m else ""
    if not meta["call_id"] or not meta["account_id"]:
        raise ValueError(f"{file_name}: call_id and account_id are required")
    meta["participants"] = [{"party_id": m["pid"], "name": m["name"],
                             "email_domain": m["dom"], "affiliation": m["aff"]}
                            for m in _PART_RE.finditer(header)]
    meta["turns"] = [{"label": m.group(1), "text": m.group(2).strip()}
                     for m in re.finditer(r"^\[([^\]]+)\]:\s*(.+)$", body, re.M)]
    meta["file"] = file_name
    return meta


def role_of(party: dict | None, seller_domains: set) -> str:
    if not party:
        return "unknown"
    if party["email_domain"].lower() in seller_domains:
        return "company"
    if party["affiliation"].lower() == "external":
        return "customer"
    return "unknown"


STOP = {"that", "this", "with", "would", "could", "should", "about", "your", "have", "there",
        "what", "when", "which", "they", "them", "then", "than", "from", "were", "will", "just",
        "it's", "that's", "does", "really", "thing", "things", "like", "some", "more", "into"}


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 3 and w not in STOP}


def elicitation(text: str, prev: dict | None) -> str:
    """A PROVISIONAL grade. `led` when the customer answers a seller question by
    opening with yes/no, or by echoing the question's own words (a leading
    question puts the answer in the customer's mouth); else `unprompted`.
    A human must confirm any line before it is quoted as unprompted."""
    t = text.strip().lower()
    if not (prev and prev["role"] == "company" and prev["text"].rstrip().endswith("?")):
        return "unprompted"
    if t.startswith(LED_OPENERS):
        return "led"
    asked = _content_words(prev["text"])
    echoed = asked & _content_words(text)
    if len(echoed) >= 3 or (len(echoed) >= 2 and len(echoed) >= 0.5 * max(1, len(asked))):
        return "led"
    return "unprompted"


def attribute(call: dict, seller_domains: set) -> dict:
    by_label = {p["party_id"]: p for p in call["participants"]}
    roster = [{"speaker_label": f"[{p['party_id']}]", "name": p["name"],
               "role": role_of(p, seller_domains), "source": "transcript_header",
               "confidence": "high"} for p in call["participants"]]
    turns, statements, prev = [], [], None
    for t in call["turns"]:
        role = role_of(by_label.get(t["label"]), seller_domains)
        turn = {"speaker_label": f"[{t['label']}]", "role": role, "text": t["text"]}
        if role == "customer":
            e = elicitation(t["text"], prev)
            turn["elicitation"] = e
            statements.append({"text": t["text"], "elicitation": e,
                               "speaker_label": turn["speaker_label"],
                               "grade_basis": "heuristic", "provisional": True})
        turns.append(turn)
        prev = turn
    return {"call_id": call["call_id"], "customer_id": call["account_id"],
            "roster": roster, "turns": turns, "customer_statements": statements}


def import_dir(src: Path, seller_domains: set) -> tuple[list[dict], list[dict]]:
    matched, attributed = [], []
    for f in sorted(Path(src).glob("*.md")):
        call = parse_transcript(f.read_text(encoding="utf-8"), f.name)
        matched.append({"call_id": call["call_id"], "customer_id": call["account_id"],
                        "started": call["date"], "title": call["title"],
                        "transcript": "\n".join(f"[{t['label']}]: {t['text']}"
                                                for t in call["turns"]),
                        "match_method": "transcript_header_account_id"})
        attributed.append(attribute(call, seller_domains))
    return matched, attributed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Import markdown call transcripts for win-loss-rewind.")
    ap.add_argument("transcripts_dir", type=Path)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--seller-domain", action="append", required=True,
                    help="your own email domain (repeatable); never treated as a customer")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="default: skills/win-loss-rewind/outputs/<run-id>/")
    a = ap.parse_args(argv)
    out = a.out_dir or (SKILL_ROOT / "outputs" / a.run_id)
    out.mkdir(parents=True, exist_ok=True)
    matched, attributed = import_dir(a.transcripts_dir, {d.lower() for d in a.seller_domain})
    for name, rows in (("matched_calls.jsonl", matched),
                       ("calls_with_speaker_attribution.jsonl", attributed)):
        (out / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    n_led = sum(1 for a_ in attributed for s in a_["customer_statements"] if s["elicitation"] == "led")
    n_cust = sum(len(a_["customer_statements"]) for a_ in attributed)
    print(f"imported {len(matched)} call(s) -> {out}  ({n_cust} customer lines, {n_led} led)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
