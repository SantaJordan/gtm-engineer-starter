#!/usr/bin/env python3
"""Score a folder of evidence packets with Jev. The "score my list" command.

    python3 skills/calibrated-judge/scripts/judge_packets.py \
        --packets out/northwind-run/packets \
        --icp examples/northwind-demo/icp.json \
        --out out/northwind-scores

What it does, in order:

  1. Reads every packet that `skills/crawford/scripts/build_packets.py` wrote
     (`<packets>/companies/*.json` for `icp_fit`, `<packets>/people/*.json`
     for `persona_fit`).
  2. Asks the judge one question per packet: does this row fit the ICP (or
     persona)? The judge is Jev, reached only through `jev_client.ask()`.
  3. Writes three files to `--out`:
       scores.csv          one line per row: action, verdict, probability, why
       review_queue.csv    only the rows the judge was unsure about
       review_cards.md     the same rows as readable cards for a human
     plus the append-only decision log under `<out>/judge/decisions/`.

The four actions:
  accept        the judge is confident the row fits
  reject        the judge is confident the row does not fit
  review        the judge is unsure: a human should look (review_queue.csv)
  insufficient  no answer: no API key, the judge is switched off, or the
                packet had too little evidence. These rows are NOT a "no".

Cost: Jev is billed per input token (about $0.042 per million). A packet is a
few thousand tokens, so a row costs a fraction of a cent. The script prints a
ceiling before it starts and stops when the metered spend passes --max-usd.

With no Jev key set, every row comes back `insufficient` with
`error=judge_unavailable` and nothing is spent. That is the offline path the
repo's acceptance check runs.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

#: A deliberately high per-row ceiling for the pre-run estimate. The real cost
#: is metered per row from the backend's usage numbers.
CEILING_USD_PER_ROW = 0.002

KIND_FOR = {"icp_fit": "companies", "persona_fit": "people"}

SCORE_HEADERS = ["entity_key", "name", "domain", "decision", "action", "verdict",
                 "final_probability", "decision_score", "review_reasons",
                 "packet_tier", "backend", "model", "cost_usd", "cached", "error", "why"]


def _value(packet: dict, section: str, field: str):
    cell = (packet.get(section) or {}).get(field)
    if isinstance(cell, dict):
        return cell.get("value")
    return cell


def load_packets(packets_dir: Path, decision: str, limit: int = 0) -> list[tuple[str, dict, dict]]:
    """[(key, packet, whole_doc)] sorted by key. A file that is not a packet is skipped."""
    folder = packets_dir / KIND_FOR[decision]
    if not folder.is_dir():
        raise SystemExit(f"no {KIND_FOR[decision]} packets at {folder} "
                         f"(run skills/crawford/scripts/build_packets.py first)")
    out = []
    for f in sorted(folder.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"  skipped unreadable packet {f.name}", file=sys.stderr)
            continue
        packet = doc.get("packet") if isinstance(doc, dict) else None
        if not isinstance(packet, dict):
            continue
        out.append((str(doc.get("key") or f.stem), packet, doc))
        if limit and len(out) >= limit:
            break
    return out


def load_criteria(path: Path | None) -> dict | None:
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml  # noqa: PLC0415 — optional dependency
        return yaml.safe_load(text)
    return json.loads(text)


def score_row(result, key: str, packet: dict, doc: dict, decision: str) -> dict:
    d = result.as_dict()
    tier = (doc.get("richness") or {}).get("tier") or (d.get("packet_richness") or {}).get("tier")
    return {
        "entity_key": key,
        "name": _value(packet, "record", "name") or "",
        "domain": _value(packet, "record", "domain") or _value(packet, "site", "domain") or "",
        "decision": decision,
        "action": d.get("action"),
        "verdict": "" if d.get("verdict") is None else d.get("verdict"),
        "final_probability": "" if d.get("final_probability") is None else d.get("final_probability"),
        "decision_score": "" if d.get("decision_score") is None else d.get("decision_score"),
        "review_reasons": "; ".join(str(r) for r in d.get("review_reasons") or []),
        "packet_tier": tier or "",
        "backend": d.get("backend") or "",
        "model": d.get("model") or "",
        "cost_usd": 0.0 if d.get("cached") else (d.get("cost_usd") or 0.0),
        "cached": bool(d.get("cached")),
        "error": d.get("error") or "",
        "why": d.get("why") or "",
    }


def run(packets_dir: Path, out_dir: Path, *, decision: str = "icp_fit", criteria=None,
        mode: str = "shadow", limit: int = 0, max_usd: float = 1.0,
        judge_fn=None, run_id: str | None = None) -> dict:
    """Score every packet. `judge_fn` is injectable so tests never touch a network."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Keep the judge's decision log beside this run's outputs unless told otherwise.
    os.environ.setdefault("CALIBRATED_JUDGE_OUT", str(out_dir / "judge"))

    from judge import log, review_queue  # noqa: PLC0415
    if judge_fn is None:
        from judge import judge as judge_fn  # noqa: PLC0415

    run_id = run_id or f"judge-packets-{uuid.uuid4().hex[:8]}"
    rows = load_packets(packets_dir, decision, limit)
    ceiling = round(len(rows) * CEILING_USD_PER_ROW, 4)
    print(f"{len(rows)} packet(s) to score for {decision}. "
          f"Cost ceiling ${ceiling:.4f} (metered per row; stop at ${max_usd:.2f}).")

    kw = {"icp": criteria} if decision == "icp_fit" else {"persona": criteria}
    scored, logged, spent = [], [], 0.0
    for key, packet, doc in rows:
        if spent >= max_usd:
            print(f"stopped: metered spend ${spent:.4f} reached --max-usd {max_usd}")
            break
        result = judge_fn(decision, packet, mode=mode, entity_key=key, **kw)
        # A cached answer was paid for when it was first asked; it costs nothing now.
        if not getattr(result, "cached", False):
            spent += float(getattr(result, "cost_usd", 0.0) or 0.0)
        scored.append(score_row(result, key, packet, doc, decision))
        logged.append(log.row_for(result, skill="judge_packets", hook_point="score_list",
                                  decision=decision, entity_key=key, run_id=run_id, mode=mode))
        try:
            log.append(result, skill="judge_packets", hook_point="score_list",
                       decision=decision, entity_key=key, run_id=run_id, mode=mode)
        except OSError as exc:
            print(f"  could not write the decision log: {exc}", file=sys.stderr)

    scores_csv = out_dir / "scores.csv"
    with scores_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SCORE_HEADERS)
        w.writeheader()
        w.writerows(scored)
    queue_csv = review_queue.write_csv(run_id, rows=logged, path=out_dir / "review_queue.csv")
    cards_md = review_queue.write_cards(run_id, rows=logged, path=out_dir / "review_cards.md")

    counts: dict[str, int] = {}
    for r in scored:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    unavailable = sum(1 for r in scored if r["error"] == "judge_unavailable")
    summary = {"run_id": run_id, "rows": len(scored), "actions": counts,
               "judge_unavailable": unavailable, "spent_usd": round(spent, 8),
               "scores_csv": str(scores_csv), "review_queue_csv": str(queue_csv),
               "review_cards_md": str(cards_md)}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"actions: {counts}. spent ${spent:.6f}.")
    print(f"scores : {scores_csv}\nreview : {queue_csv}\ncards  : {cards_md}")
    if scored and unavailable == len(scored):
        print("\nEvery row came back judge_unavailable: no Jev key was found. Set "
              "TYPESAFE_API_KEY (console.typesafe.ai) or OPENROUTER_API_KEY in .env "
              "and run again. See START-HERE.md, step 8.")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score evidence packets with Jev (icp_fit / persona_fit).")
    ap.add_argument("--packets", type=Path, required=True,
                    help="the packets/ folder build_packets.py wrote")
    ap.add_argument("--icp", type=Path, help="ICP rules (JSON or YAML) for icp_fit")
    ap.add_argument("--persona", type=Path, help="persona rules (JSON or YAML) for persona_fit")
    ap.add_argument("--decision", choices=sorted(KIND_FOR), default="icp_fit")
    ap.add_argument("--out", type=Path, required=True, help="folder for scores.csv + review queue")
    ap.add_argument("--mode", choices=["shadow", "off"], default="shadow",
                    help="shadow (default) asks Jev; off makes no call at all")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N packets")
    ap.add_argument("--max-usd", type=float, default=1.0, help="stop once metered spend passes this")
    a = ap.parse_args(argv)
    criteria_path = a.icp if a.decision == "icp_fit" else a.persona
    if criteria_path is None:
        print(f"warning: no {'--icp' if a.decision == 'icp_fit' else '--persona'} rules given; "
              "the judge will read evidence against nothing", file=sys.stderr)
    run(a.packets, a.out, decision=a.decision, criteria=load_criteria(criteria_path),
        mode=a.mode, limit=a.limit, max_usd=a.max_usd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
