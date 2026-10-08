#!/usr/bin/env python3
"""
Phase 7 — Compose Discriminator + Cynical Buyer.

For each archetype with surviving signals, build a weighted scoring rubric,
score a held-out worked example, run the Cynical Buyer evaluator, and emit
the deliverable: discriminator.md + discriminator.jsonl.

Usage:
    python compose_discriminator.py --run-id <id>
    python compose_discriminator.py --run-id <id> --archetype-id 2

Inputs:
    outputs/<run-id>/validated_signals.jsonl
    outputs/<run-id>/archetypes.jsonl
    outputs/<run-id>/customer_panels.jsonl
    outputs/<run-id>/wayback_evidence.jsonl   (optional — strike windows)
    outputs/<run-id>/knowability.jsonl        (optional — Phase 6.7 verdicts)

Time-travel gate: only signals with status=="firm" (survived on firm-tagged
evidence — [ARCHIVED-BEFORE]/[RECORD-DATED-BEFORE]) enter the rubric.
`--allow-provisional` is the loud escape valve that also admits
provisional_only signals, labeled as such in the deliverable.

Outputs:
    outputs/<run-id>/discriminator.jsonl   (machine-readable rubric per archetype)
    outputs/<run-id>/discriminator.md      (human-readable per architecture.md spec)
    outputs/<run-id>/cost_summary.json     (aggregated phase costs)

Env: ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from signal_presence import (  # shared presence brain (Phases 6/7/8)
    PRESENT,
    UNKNOWN,
    signal_present,  # noqa: F401 — kept for callers/tests
    signal_state,
)

load_dotenv()

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"
PROMPTS_ROOT = SKILL_ROOT / "prompts"

# House cost rule (model-costs.md Avoid list): no frontier model as a scripted
# default. Env-overridable — set WLR_CYNICAL_MODEL to deliberately elevate.
from wlr_config import SONNET_MODEL  # noqa: E402
CYNICAL_MODEL = os.environ.get("WLR_CYNICAL_MODEL", SONNET_MODEL)
TPR_TARGET = 0.70

import wlr_schema  # noqa: E402
import rubric_score  # noqa: E402
GOOD_FIT = wlr_schema.GOOD_FIT_OUTCOMES
NOT_GOOD_FIT = wlr_schema.NOT_GOOD_FIT_OUTCOMES


# ──────────────────────────────────────────────────────────────
# I/O
# ──────────────────────────────────────────────────────────────


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Error: required file missing — {path}")
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_prompt(name: str) -> str:
    p = PROMPTS_ROOT / name
    if not p.exists():
        raise SystemExit(f"Error: prompt {p} missing")
    return p.read_text()


# ──────────────────────────────────────────────────────────────
# Rubric composition
# ──────────────────────────────────────────────────────────────


def signal_weight_from_lift(lift: float | None) -> float:
    """Weight ∝ holdout lift, normalized later. Floor at 0.5."""
    if lift is None or math.isinf(lift) or lift <= 1.0:
        return 0.5
    return max(0.5, lift - 1.0)


def compose_rubric_for_archetype(survivors_for_arc: list[dict]) -> dict:
    """
    Given the surviving signals for ONE archetype, produce a weighted rubric:
        each signal gets a weight ∝ its holdout lift (fallback to train if no holdout)
        normalize weights so they sum to 1.0
        threshold = the value that yields TPR ≥ 70% on the training cohort
    """
    items = []
    for s in survivors_for_arc:
        hold = (s.get("holdout") or {}).get("lift")
        train = (s.get("train") or {}).get("lift")
        lift_for_weight = hold if hold else train
        firm = s.get("firm") or {}
        items.append({
            "hypothesis_id": s.get("hypothesis_id"),
            "signal": s.get("signal"),
            "observability_source": s.get("observability_source"),
            "queryable_field": s.get("queryable_field"),
            # The typed test travels with the signal, so Phase 8 asks exactly
            # what Phases 6 and 7 asked (signal_presence.py).
            "predicate": s.get("predicate"),
            "hypothesized_lead_time_days": s.get("hypothesized_lead_time_days"),
            "train_lift": train,
            "holdout_lift": hold,
            "evidence_status": s.get("status", "firm"),
            "firm_coverage": firm.get("coverage"),
            "firm_counts": firm.get("counts"),
            "raw_weight": signal_weight_from_lift(lift_for_weight),
            "anti_match_examples": s.get("anti_match_examples", []),
        })
    total = sum(i["raw_weight"] for i in items) or 1.0
    for i in items:
        i["weight"] = round(i["raw_weight"] / total, 4)
    return {"signals": items}


def score_panel(rubric: dict, panel: dict) -> tuple[float, list[dict]]:
    """(score, diff) from the ONE shared scorer (`rubric_score.score`), which
    Phase 8 also uses, so calibration and prospect scoring cannot disagree."""
    res = rubric_score.score(rubric, panel)
    return res["score"], res["diff"]


def calibrate_threshold(rubric: dict, train_panels: list[dict]) -> float:
    """Pick threshold T so that ≥70% of good_fit train panels have score ≥ T."""
    good_scores = []
    for p in train_panels:
        outcome = wlr_schema.verified_outcome(p)
        if outcome in GOOD_FIT:
            s, _ = score_panel(rubric, p)
            good_scores.append(s)
    if not good_scores:
        return 0.5
    good_scores.sort()
    # 70% TPR → at least 70% of good scores ≥ T → T = 30th percentile.
    idx = max(0, int(len(good_scores) * (1 - TPR_TARGET)))
    return round(good_scores[idx], 4)


def split_train_holdout(archetype: dict, panels_by_id: dict) -> tuple[list[dict], list[dict]]:
    train_ids, hold_ids = wlr_schema.split_ids(archetype)
    return ([panels_by_id[c] for c in train_ids if c in panels_by_id],
            [panels_by_id[c] for c in hold_ids if c in panels_by_id])


# ──────────────────────────────────────────────────────────────
# Cynical Buyer call
# ──────────────────────────────────────────────────────────────


def call_cynical_buyer(archetype: dict, rubric: dict, threshold: float,
                       worked_example: dict, system_prompt: str) -> tuple[dict, dict]:
    try:
        from anthropic import Anthropic
    except ImportError:
        raise SystemExit("Error: `anthropic` package required")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Error: ANTHROPIC_API_KEY not set")
    client = Anthropic(api_key=api_key)

    user_msg = json.dumps({
        "archetype": {
            "id": archetype.get("id"),
            "name": archetype.get("name"),
            "one_liner": archetype.get("one_liner"),
            "member_count": len(archetype.get("members") or []),
        },
        "proposed_discriminator": {
            "signals_in_recipe": [
                {
                    "signal": s["signal"],
                    "observability_source": s["observability_source"],
                    "queryable_field": s["queryable_field"],
                    "train_lift": s.get("train_lift"),
                    "holdout_lift": s.get("holdout_lift"),
                    "weight": s["weight"],
                    "threshold": threshold,
                    "evidence_status": s.get("evidence_status"),
                    "firm_coverage": s.get("firm_coverage"),
                    "firm_counts": s.get("firm_counts"),
                    "strike_window": s.get("strike_window"),
                    "knowability": s.get("knowability"),
                }
                for s in rubric["signals"]
            ],
            "scoring_formula": ("fit_score = sum(weight_i * I(signal_i present)) / "
                                "sum(weight_i over OBSERVED signals) — a signal whose "
                                "source never ran is excluded from both sums"),
            "threshold": threshold,
            "worked_example_held_out_customer": worked_example,
        },
        "instruction": "Score on 8 axes + 3 litmus tests. Return JSON ONLY.",
    }, indent=2, default=str)

    resp = client.messages.create(
        model=CYNICAL_MODEL,
        max_tokens=3000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    text_parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", "") or "")
    text = "\n".join(text_parts)

    verdict_obj = _parse_json_object(text)
    usage = {
        "input_tokens": getattr(resp.usage, "input_tokens", 0),
        "output_tokens": getattr(resp.usage, "output_tokens", 0),
    }
    return verdict_obj, usage


def _parse_json_object(text: str) -> dict:
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    body = fence.group(1) if fence else text
    # Find first {...} block.
    m = re.search(r"\{.*\}", body, re.DOTALL)
    if not m:
        return {"verdict": "destroy", "rationale": "could not parse Cynical Buyer response",
                "raw": text[:500]}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"verdict": "destroy", "rationale": "JSON decode failed", "raw": m.group(0)[:500]}


# ──────────────────────────────────────────────────────────────
# Markdown rendering (mirrors architecture.md § "What the deliverable looks like")
# ──────────────────────────────────────────────────────────────


#: What to do next for each Phase 6 cause (START-HERE.md, step 6).
NEXT_ACTION = {
    "insufficient_evidence": "Too few labelled customers per split. Add history, merge "
                             "archetypes, or wait for more outcomes; do not lower the bar.",
    "signal_not_observed": "The enrichment never saw the signal's field. Fix or replace the "
                           "source, then re-run Phase 2 onward.",
    "no_detectable_relationship": "The hypotheses did not separate winners from losers. Write "
                                  "new hypotheses from the dossiers' unprompted customer lines, "
                                  "or proceed to the ICP with qualitative evidence and say so.",
    "not_firm_checked": "The signal cleared today's-web filter but was never proven visible "
                        "before the close date (no archive check ran). Run the firm pass "
                        "(network access to the Wayback Machine) before relying on it.",
    "no_hypotheses": "No hypothesis reached Phase 6 (all rejected at the gates). Read "
                     "hypotheses-rejected.jsonl.",
}


def no_discriminator_section(arc: dict, flag: dict | None) -> tuple[str, dict]:
    """discriminator.md section + .jsonl record for an archetype with no survivor."""
    causes = (flag or {}).get("causes") or {}
    lines = [f"## Archetype: {arc.get('name','(unnamed)')} — no reliable discriminator found\n",
             "No signal survived Phase 6 for this archetype. That is a result, not an error.\n"]
    if causes:
        lines.append("| Cause | Hypotheses | Next action |")
        lines.append("|---|---|---|")
        for cause, n in sorted(causes.items()):
            lines.append(f"| {cause} | {n} | {NEXT_ACTION.get(cause, '')} |")
        lines.append("")
    lines.append("Details: `validated_signals.md` (every hypothesis, its tables and why it died).\n")
    rec = {"archetype_id": arc.get("id"), "archetype_name": arc.get("name"),
           "no_discriminator_shipped": True, "result": "no reliable discriminator found",
           "causes": causes,
           "next_actions": {c: NEXT_ACTION.get(c) for c in causes},
           "details": "validated_signals.md"}
    return "\n".join(lines), rec


def render_archetype_md(arc: dict, rubric: dict, threshold: float,
                        worked: dict, verdict: dict | None,
                        ship_status: str) -> str:
    members = arc.get("members") or []
    counts = {o: 0 for o in wlr_schema.OUTCOMES}
    for m in members:
        if isinstance(m, dict):
            o = (m.get("outcome") or "").lower()
            if o in counts:
                counts[o] += 1

    lines: list[str] = []
    lines.append(f"## Archetype: {arc.get('name','(unnamed)')}\n")
    lines.append(f"**One-liner:** {arc.get('one_liner','')}\n")
    lines.append(
        f"**Members:** {len(members)} customers (won: {counts['won']} / 10x: {counts['10x']} / "
        f"lost: {counts['lost']} / healthy: {counts['healthy']} / churned: {counts['churned']})\n"
    )
    score = (verdict or {}).get("weighted_average")
    score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"
    lines.append(f"**Discriminator (Cynical Buyer score {score_str} → {ship_status}):**\n")
    lines.append("To identify a good-fit prospect like this archetype, score on:\n")
    lines.append("| Signal | Source | Field | Weight | Lift (train / holdout) | Evidence | Strike window | Knowability |")
    lines.append("|--------|--------|-------|--------|------------------------|----------|---------------|-------------|")
    for s in rubric["signals"]:
        tl = s.get("train_lift"); hl = s.get("holdout_lift")
        tl_s = f"{tl:.2f}" if isinstance(tl, (int, float)) else "—"
        hl_s = f"{hl:.2f}" if isinstance(hl, (int, float)) else "—"
        status = s.get("evidence_status", "firm")
        cov = s.get("firm_coverage")
        ev_s = ("⚠️ PROVISIONAL (never firm-checked)" if status == "provisional_only"
                else f"FIRM ({cov:.0%} coverage)" if isinstance(cov, (int, float))
                else "FIRM")
        sw = s.get("strike_window") or {}
        sw_s = (f"~{sw['median_strike_days']}d before close "
                f"(range {sw['min_strike_days']}–{sw['max_strike_days']}d, "
                f"n={sw['n_firm_cells']})") if sw else "—"
        kn = s.get("knowability") or {}
        kn_s = {"direct_evidence": "direct evidence",
                "proxy": "via proxy",
                "cant_know": "⚠️ can't know ahead"}.get(kn.get("verdict"), "—")
        lines.append(
            f"| {s['signal']} | {s['observability_source']} | "
            f"`{s['queryable_field']}` | {s['weight']:.2f} | {tl_s} / {hl_s} | "
            f"{ev_s} | {sw_s} | {kn_s} |"
        )
    lines.append("")
    kn_blocks = [s for s in rubric["signals"] if s.get("knowability")]
    if kn_blocks:
        lines.append("**How to catch each signal ahead of time (Phase 6.7 knowability panel):**\n")
        for s in kn_blocks:
            kn = s["knowability"]
            lines.append(f"- **{s['signal']}** — verdict: `{kn.get('verdict')}`. "
                         f"{kn.get('detection_recipe') or ''}")
            proxy = kn.get("proxy")
            if proxy and isinstance(proxy, dict) and proxy.get("description"):
                lines.append(f"  - Proxy/dataset: {proxy.get('description')} "
                             f"(source: {proxy.get('dataset','?')}, best tag: "
                             f"{proxy.get('best_case_tag','?')})")
        lines.append("")
    lines.append("**Combination scoring formula:**\n")
    lines.append("`fit_score = sum(w_i * I(signal_i present)) / sum(w_i over observed signals)`\n")
    lines.append("Signals whose source never ran (`state: unknown`) are dropped from "
                 "both sums — an unenriched source is not evidence against the "
                 "company.\n")
    lines.append(f"Threshold for \"looks-like-archetype\": `fit_score >= {threshold}`\n")
    lines.append("**Worked example (held-out customer):**\n")
    lines.append("```json")
    lines.append(json.dumps(worked, indent=2, default=str))
    lines.append("```\n")
    lines.append("**Anti-matches to watch for:**\n")
    seen = set()
    for s in rubric["signals"]:
        for a in s.get("anti_match_examples") or []:
            if a in seen:
                continue
            seen.add(a)
            lines.append(f"- {a}")
    if not seen:
        lines.append("- (none recorded — flag for review)")
    lines.append("")
    lines.append("**How to query this on a cold prospect:**\n")
    lines.append("```python")
    lines.append("# Use scripts/score_prospect.py — it pulls the same Phase-2 panel")
    lines.append("# for <domain> and applies this rubric to score against this archetype.")
    lines.append(f"# python score_prospect.py <domain> --run-id <run-id>")
    lines.append("```\n")
    if verdict:
        lines.append("**Cynical Buyer verdict:**\n")
        lines.append(f"- Verdict: `{verdict.get('verdict','')}`")
        rationale = verdict.get("rationale") or ""
        lines.append(f"- Rationale: {rationale}")
        litmus = verdict.get("litmus_tests") or {}
        if litmus:
            lines.append(f"- Litmus: so_what={litmus.get('so_what','?')}, "
                         f"wikipedia={litmus.get('wikipedia','?')}, "
                         f"competitor={litmus.get('competitor','?')}")
        lines.append("")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--archetype-id", type=int, default=None)
    ap.add_argument("--allow-provisional", action="store_true",
                    help="ALSO admit provisional_only signals (never firm-verified) "
                         "into the rubric — labeled loudly in the deliverable")
    args = ap.parse_args()

    run_dir = OUTPUTS_ROOT / args.run_id
    if not run_dir.exists():
        raise SystemExit(f"Error: run dir {run_dir} does not exist")

    validated = load_jsonl(run_dir / "validated_signals.jsonl")
    archetypes = wlr_schema.load_archetypes(run_dir / "archetypes.jsonl")
    panels = load_jsonl(run_dir / "customer_panels.jsonl")
    panels_by_id = {str(p.get("customer_id")): p for p in panels}

    if args.archetype_id is not None:
        archetypes = [a for a in archetypes if a.get("id") == args.archetype_id]
        if not archetypes:
            raise SystemExit(f"Error: archetype id {args.archetype_id} not found")

    cynical_prompt = load_prompt("cynical-buyer.md")

    # Optional time-travel artifacts (absent on legacy / --skip-firm-pass runs).
    strike_by_hyp: dict = {}
    ev_path = run_dir / "wayback_evidence.jsonl"
    if ev_path.exists():
        by_hyp: dict = {}
        for row in load_jsonl(ev_path):
            sw = row.get("strike_window_days")
            if row.get("firm_present") is True and isinstance(sw, int):
                by_hyp.setdefault(row.get("hypothesis_id"), []).append(sw)
        for hid, sws in by_hyp.items():
            sws.sort()
            strike_by_hyp[hid] = {
                "median_strike_days": sws[len(sws) // 2],
                "min_strike_days": sws[0],
                "max_strike_days": sws[-1],
                "n_firm_cells": len(sws),
            }

    knowability_by_hyp: dict = {}
    kn_path = run_dir / "knowability.jsonl"
    if kn_path.exists():
        for row in load_jsonl(kn_path):
            knowability_by_hyp[row.get("hypothesis_id")] = row

    # Group shippable signals by archetype. `survived` == FIRM (backcast
    # Phase 6.5); --allow-provisional loudly admits the never-firm-checked.
    survivors_by_arc: dict = {}
    for r in validated:
        admit = r.get("survived") or (
            args.allow_provisional and r.get("status") == "provisional_only")
        if admit:
            survivors_by_arc.setdefault(r.get("archetype_id"), []).append(r)

    # Phase 6 writes one "no reliable discriminator found" flag (with cause
    # counts) per archetype that kept nothing; carry it into the deliverable.
    no_disc_by_arc = {r.get("archetype_id"): r for r in validated if r.get("no_discriminator_found")}
    validated = [r for r in validated if not r.get("no_discriminator_found")]

    md_parts: list[str] = ["# Win-Loss Rewind — Discriminator\n"]
    discriminator_jsonl: list[dict] = []
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    for arc in archetypes:
        aid = arc.get("id")
        survivors = survivors_by_arc.get(aid, [])
        if not survivors:
            md, rec = no_discriminator_section(arc, no_disc_by_arc.get(aid))
            md_parts.append(md)
            discriminator_jsonl.append(rec)
            continue

        rubric = compose_rubric_for_archetype(survivors)
        for item in rubric["signals"]:
            hid = item.get("hypothesis_id")
            item["strike_window"] = strike_by_hyp.get(hid)
            kn = knowability_by_hyp.get(hid)
            item["knowability"] = ({"verdict": kn.get("verdict"),
                                    "detection_recipe": kn.get("detection_recipe"),
                                    "proxy": kn.get("proxy")}
                                   if kn else None)
        train_panels, hold_panels = split_train_holdout(arc, panels_by_id)
        threshold = calibrate_threshold(rubric, train_panels)

        # Worked example: pick first holdout panel (or first train if no holdout).
        example_pool = hold_panels or train_panels
        if example_pool:
            ex_panel = example_pool[0]
            ex_score, ex_diff = score_panel(rubric, ex_panel)
            worked = {
                "customer_id": ex_panel.get("customer_id"),
                "actual_outcome": ex_panel.get("outcome") or ex_panel.get("deal_outcome"),
                "fit_score": round(ex_score, 4),
                "threshold": threshold,
                "looks_like": ex_score >= threshold,
                "signal_diff": ex_diff,
            }
        else:
            worked = {"note": "no panels available for worked example"}

        # Cynical Buyer pass.
        try:
            verdict, usage = call_cynical_buyer(arc, rubric, threshold, worked, cynical_prompt)
            total_usage["input_tokens"] += usage["input_tokens"]
            total_usage["output_tokens"] += usage["output_tokens"]
        except Exception as e:
            verdict = {"verdict": "destroy", "rationale": f"Cynical Buyer call failed: {e}"}

        decision = (verdict.get("verdict") or "destroy").lower()
        revised = False

        if decision == "revise_once":
            fix = verdict.get("if_revise_once_what_to_fix") or ""
            # Naive revision: bump threshold to 90th percentile if Cynical Buyer mentions
            # over-permissive threshold; otherwise drop the lowest-weight signal.
            if "threshold" in fix.lower() or "permissive" in fix.lower():
                threshold = round(threshold * 1.2, 4)
            else:
                if len(rubric["signals"]) > 1:
                    rubric["signals"].sort(key=lambda s: s["weight"], reverse=True)
                    rubric["signals"].pop()  # drop lowest weight
                    # Renormalize.
                    total = sum(s["raw_weight"] for s in rubric["signals"]) or 1.0
                    for s in rubric["signals"]:
                        s["weight"] = round(s["raw_weight"] / total, 4)
            revised = True
            try:
                verdict, usage = call_cynical_buyer(arc, rubric, threshold, worked, cynical_prompt)
                total_usage["input_tokens"] += usage["input_tokens"]
                total_usage["output_tokens"] += usage["output_tokens"]
                decision = (verdict.get("verdict") or "destroy").lower()
            except Exception as e:
                verdict["revision_error"] = str(e)
                decision = "destroy"

        if decision == "ship":
            status = "ship"
        elif decision == "destroy":
            status = "no_discriminator_shipped"
        else:
            # Anything weird — be conservative.
            status = decision

        md_parts.append(render_archetype_md(arc, rubric, threshold, worked, verdict, status))

        rec = {
            "archetype_id": aid,
            "archetype_name": arc.get("name"),
            "ship_status": status,
            "rubric": rubric,
            "threshold": threshold,
            "worked_example": worked,
            "cynical_buyer": verdict,
            "revised": revised,
        }
        if status != "ship":
            rec["no_discriminator_shipped"] = True
        discriminator_jsonl.append(rec)

    out_md = run_dir / "discriminator.md"
    out_md.write_text("\n".join(md_parts))
    out_jsonl = run_dir / "discriminator.jsonl"
    with open(out_jsonl, "w") as f:
        for r in discriminator_jsonl:
            f.write(json.dumps(r, default=str) + "\n")

    # Aggregate cost summary across all phases that recorded into _phase_costs/*.json.
    cost_dir = run_dir / "_phase_costs"
    cost_summary: dict = {"phases": {}, "total_usage": {"input_tokens": 0, "output_tokens": 0}}
    if cost_dir.exists():
        for f in sorted(cost_dir.glob("phase*.json")):
            try:
                data = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            phase_label = f.stem
            cost_summary["phases"][phase_label] = data
            usage = data.get("usage") or {}
            cost_summary["total_usage"]["input_tokens"] += usage.get("input_tokens", 0)
            cost_summary["total_usage"]["output_tokens"] += usage.get("output_tokens", 0)

    # Add this phase's usage.
    cost_summary["phases"]["phase7"] = {
        "phase": 7,
        "model": CYNICAL_MODEL,
        "usage": total_usage,
    }
    cost_summary["total_usage"]["input_tokens"] += total_usage["input_tokens"]
    cost_summary["total_usage"]["output_tokens"] += total_usage["output_tokens"]

    (run_dir / "cost_summary.json").write_text(json.dumps(cost_summary, indent=2))

    print(f"discriminators={sum(1 for r in discriminator_jsonl if r.get('ship_status')=='ship')} "
          f"archetypes={len(discriminator_jsonl)}",
          file=sys.stderr)
    print(f"→ {out_md}", file=sys.stderr)
    print(f"→ {out_jsonl}", file=sys.stderr)
    print(f"→ {run_dir / 'cost_summary.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
