#!/usr/bin/env python3
"""Quantify: rank the rows the judge ACCEPTED by one number read from each packet.

Qualify first (the judge's accept / reject), then quantify: among qualified rows,
which are biggest or most urgent? This script does the second half.

    python3 skills/calibrated-judge/scripts/quantify.py \
        --scores out/run1-scores/scores.csv \
        --packets out/run1/packets \
        --field site.homepage_text \
        --pattern "(\\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\\s+(?:\\w+\\s+){0,3}?(?:offices|locations|clinics|practices)" \
        --metric-name offices \
        --out out/run1-scores/shortlist.csv

  --field    a dotted packet field (default site.homepage_text). Its value is searched.
  --pattern  a regular expression whose FIRST group captures the number, as
             digits or a number word (one .. twelve). Case is ignored.

Output columns: rank, entity_key, name, domain, final_probability, <metric>,
<metric>_source, <metric>_quote. Rows where the number is not found are kept,
ranked last, with the metric left blank: missing is not zero.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
DEFAULT_PATTERN = (r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(?:\w+\s+){0,3}?"
                   r"(?:offices|locations|clinics|practices)\b")


def field_value(packet: dict, dotted: str):
    section, _, name = dotted.partition(".")
    cell = (packet.get(section) or {}).get(name)
    if isinstance(cell, dict):
        return cell.get("value"), cell.get("source")
    return cell, None


def extract(text: str, pattern: str):
    """(number, quote) or (None, None)."""
    m = re.search(pattern, str(text or ""), re.I)
    if not m:
        return None, None
    raw = m.group(1).lower()
    n = int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw)
    return n, m.group(0)


def rank(scores_csv: Path, packets_dir: Path, *, field: str = "site.homepage_text",
         pattern: str = DEFAULT_PATTERN, metric: str = "metric") -> list[dict]:
    with open(scores_csv, newline="", encoding="utf-8") as fh:
        scores = list(csv.DictReader(fh))
    out = []
    for r in scores:
        if r.get("action") != "accept":
            continue
        kind = "people" if r.get("decision") == "persona_fit" else "companies"
        path = packets_dir / kind / f"{r['entity_key']}.json"
        packet = json.loads(path.read_text(encoding="utf-8"))["packet"] if path.exists() else {}
        value, source = field_value(packet, field)
        n, quote = extract(value, pattern)
        out.append({"entity_key": r["entity_key"], "name": r.get("name", ""),
                    "domain": r.get("domain", ""), "final_probability": r.get("final_probability", ""),
                    metric: "" if n is None else n,
                    f"{metric}_source": f"packet {field}" + (f" ({source})" if source else ""),
                    f"{metric}_quote": quote or ""})
    out.sort(key=lambda r: (r[metric] == "", -(r[metric] if r[metric] != "" else 0), r["name"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rank accepted rows by a number read from each packet.")
    ap.add_argument("--scores", type=Path, required=True, help="scores.csv from judge_packets.py")
    ap.add_argument("--packets", type=Path, required=True, help="the packets/ folder")
    ap.add_argument("--field", default="site.homepage_text")
    ap.add_argument("--pattern", default=DEFAULT_PATTERN)
    ap.add_argument("--metric-name", default="metric")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    rows = rank(a.scores, a.packets, field=a.field, pattern=a.pattern, metric=a.metric_name)
    cols = ["rank", "entity_key", "name", "domain", "final_probability", a.metric_name,
            f"{a.metric_name}_source", f"{a.metric_name}_quote"]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    missing = sum(1 for r in rows if r[a.metric_name] == "")
    print(f"{len(rows)} accepted row(s) ranked by {a.metric_name}; {missing} without a value -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
