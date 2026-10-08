#!/usr/bin/env python3
"""
Phase 5 — Hypothesis Generation.

For each archetype, generate 10–30 candidate "what would have preceded this?"
hypotheses by calling the configured Claude model (env WLR_HYPOTHESIS_MODEL,
    default current Sonnet) with adaptive thinking, then run each
candidate through the five hard gates from `prompts/hard-gates.md`.

Survivors → outputs/<run-id>/hypotheses.jsonl
Rejects   → outputs/<run-id>/hypotheses-rejected.jsonl  (with `failed_gate`)

Usage:
    python generate_hypotheses.py --run-id <id>
    python generate_hypotheses.py --run-id <id> --archetype-id 2
    python generate_hypotheses.py --run-id <id> --n-per-archetype 25

Inputs read from outputs/<run-id>/:
    archetypes.jsonl
    themes.jsonl
    customer_panels.jsonl

Env:
    ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"
PROMPTS_ROOT = SKILL_ROOT / "prompts"
REFERENCES_ROOT = SKILL_ROOT / "references"

VALID_VISIBILITY_TAGS = {"ARCHIVED_BEFORE", "RECORD_DATED_BEFORE", "RETRO_ASSUMED"}


def load_source_manifest() -> dict:
    path = REFERENCES_ROOT / "source-temporal-classification.json"
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sources": {}, "default_unclassified": {"class": "current_state_only",
                                                        "wayback_friendly": "unknown"}}


def load_vertical_sources(run_dir: Path) -> list[dict]:
    """Phase 4.5 scout output — per-archetype vertical source atlas."""
    path = run_dir / "vertical_sources.jsonl"
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def merge_vertical_sources_into_manifest(manifest: dict, vs_rows: list[dict]) -> dict:
    """Scout-discovered sources join the temporal manifest so Gate 4b can
    honestly evaluate hypotheses that cite them."""
    merged = {"sources": dict(manifest.get("sources") or {}),
              "default_unclassified": manifest.get("default_unclassified", {})}
    for r in vs_rows:
        key = (r.get("source_key") or "").strip()
        if not key:
            continue
        merged["sources"][key] = {
            "class": r.get("class", "current_state_only"),
            "wayback_friendly": r.get("wayback_friendly", "unknown"),
            "publication_lag_days": r.get("publication_lag_days"),
            "record_date_field": r.get("record_date_field"),
            "notes": f"vertical scout (archetype {r.get('archetype_id')}, "
                     f"confidence {r.get('confidence','?')}): {r.get('name','')}",
        }
    return merged

# House cost rule (model-costs.md Avoid list): no frontier model as a scripted
# default. Env-overridable — set WLR_HYPOTHESIS_MODEL to deliberately elevate.
from wlr_config import SONNET_MODEL  # noqa: E402
import wlr_schema  # noqa: E402
import signal_presence  # noqa: E402
HYPOTHESIS_MODEL = os.environ.get("WLR_HYPOTHESIS_MODEL", SONNET_MODEL)
DEFAULT_N_PER_ARCHETYPE = 20
MAX_TOKENS = 8000

# ──────────────────────────────────────────────────────────────
# Gate matchers
# ──────────────────────────────────────────────────────────────

# Gate 1 — banned generic descriptors (regex, case-insensitive)
GATE1_BANNED_REGEXES = [
    r"\bany\s+(b2b\s+)?compan(y|ies)\b",
    r"\btech(nology)?\s+compan(y|ies)\b",
    r"\bgrowing\b",
    r"\bgrowth-?stage\b",
    r"\bmodern\b",
    r"\binnovative\b",
    r"\btech-?forward\b",
    r"\bscaling\b",
    r"\b\d+\+?\s*employees?\b",         # "50 employees" alone
    r"\b\d+[-–]\d+\s*employees?\b",      # "50-1000 employees"
]

# Gate 2 — banned signal types (substring keyword match)
GATE2_BANNED_KEYWORDS = [
    "funding round",
    "raised funding",
    "series a",
    "series b",
    "series c",
    "series d",
    "hiring activity",
    "hiring for",
    "headcount growth",
    "growing headcount",
    "growing the team",
    "expanding to new market",
    "m&a activity",
    "merger",
    "acquisition",
    "tech stack includes",
    "tech stack contains",
    "uses [tool",
    "company size alone",
    "industry alone",
    "is a saas company",
    "is a b2b company",
    "job postings",
]

# Gate 3 — aggregate language patterns
GATE3_AGGREGATE_PATTERNS = [
    r"industry average",
    r"average\s+\w+\s+(rate|margin|cost|spend|score)",
    r"compan(y|ies)\s+typically",
    r"compan(y|ies)\s+like\s+yours",
    r"\d{1,3}\s*%\s+of\s+\w+",
    r"the typical\s+\w+",
    r"research shows",
    r"on average",
]

# Gate 4 — empty / placeholder queryable_field markers
GATE4_BAD_VALUES = {
    "",
    "various sources",
    "will figure out",
    "tbd",
    "unknown",
    "depends",
    "various",
    "n/a",
    "various apis",
}


# ──────────────────────────────────────────────────────────────
# I/O helpers
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


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def load_prompt(name: str) -> str:
    p = PROMPTS_ROOT / name
    if not p.exists():
        raise SystemExit(f"Error: prompt {p} missing")
    return p.read_text()


# ──────────────────────────────────────────────────────────────
# Gate checks
# ──────────────────────────────────────────────────────────────


def check_gate1(hypothesis: dict) -> str | None:
    """Reject if signal description matches banned generic patterns."""
    sig = (hypothesis.get("signal") or "").lower()
    for pat in GATE1_BANNED_REGEXES:
        if re.search(pat, sig, re.IGNORECASE):
            return f"gate1_horizontal_disqualification: matched /{pat}/"
    return None


def check_gate2(hypothesis: dict) -> str | None:
    """Reject if signal is in banned signal types."""
    sig = (hypothesis.get("signal") or "").lower()
    for kw in GATE2_BANNED_KEYWORDS:
        if kw in sig:
            return f"gate2_causal_link: banned signal keyword '{kw}'"
    return None


def check_gate3(hypothesis: dict) -> str | None:
    """Reject aggregate / industry-average phrasing."""
    sig = (hypothesis.get("signal") or "").lower()
    for pat in GATE3_AGGREGATE_PATTERNS:
        if re.search(pat, sig, re.IGNORECASE):
            return f"gate3_aggregates_ban: matched /{pat}/"
    return None


def check_gate4(hypothesis: dict, source_manifest: dict | None = None) -> str | None:
    """Reject if queryable_field is empty/placeholder, or (Gate 4b) if the
    temporal-provability declaration is missing or lies about the source."""
    qf = hypothesis.get("queryable_field")
    if qf is None:
        return "gate4_technical_feasibility: queryable_field is null"
    if not isinstance(qf, str):
        return "gate4_technical_feasibility: queryable_field not a string"
    if qf.strip().lower() in GATE4_BAD_VALUES:
        return f"gate4_technical_feasibility: placeholder value '{qf.strip()}'"
    # The field must be one the Phase 2 parser emits, with a predicate that fits
    # its type (a numeric field needs a threshold). See signal_presence.py.
    typed = signal_presence.validate_signal(hypothesis)
    if typed:
        return f"gate4_technical_feasibility: {typed}"

    # Gate 4b — Temporal Provability.
    tp = hypothesis.get("temporal_provability")
    if not isinstance(tp, dict):
        return "gate4_temporal_provability: temporal_provability missing"
    tag = (tp.get("best_case_tag") or "").strip().upper()
    if tag not in VALID_VISIBILITY_TAGS:
        return f"gate4_temporal_provability: invalid best_case_tag {tag!r}"
    if not (tp.get("how") or "").strip():
        return "gate4_temporal_provability: 'how' mechanism missing"
    manifest = source_manifest or {}
    source = (hypothesis.get("observability_source") or "").strip()
    cls = (manifest.get("sources") or {}).get(source,
                                              manifest.get("default_unclassified", {}))
    # "opportunistic" sources (rare captures, e.g. LinkedIn) may CLAIM
    # ARCHIVED_BEFORE as best case — Phase 6 only honors found captures.
    wayback_ok = cls.get("wayback_friendly") in (True, "opportunistic")
    dated = cls.get("class") == "point_in_time_record"
    if tag == "ARCHIVED_BEFORE":
        if not wayback_ok:
            return (f"gate4_temporal_provability: source '{source}' is not "
                    f"wayback-friendly — best_case_tag cannot be ARCHIVED_BEFORE")
        if not (hypothesis.get("archive_search_pattern") or "").strip():
            return "gate4_temporal_provability: ARCHIVED_BEFORE claim without archive_search_pattern"
    if tag == "RECORD_DATED_BEFORE":
        if not dated:
            return (f"gate4_temporal_provability: source '{source}' is not a "
                    f"point-in-time-record source — cannot claim RECORD_DATED_BEFORE")
        if not (hypothesis.get("record_date_field") or cls.get("record_date_field")):
            return "gate4_temporal_provability: RECORD_DATED_BEFORE claim without record_date_field"
    return None


def check_gate5(hypothesis: dict, themes_for_archetype: list[dict]) -> str | None:
    """
    Reject if rationale_from_phase_4_themes is empty OR if any phase_4_phrase
    does not appear verbatim in any theme for this archetype.
    """
    rationales = hypothesis.get("rationale_from_phase_4_themes")
    if not rationales:
        return "gate5_pain_connection: rationale_from_phase_4_themes empty"
    if not isinstance(rationales, list):
        return "gate5_pain_connection: rationale_from_phase_4_themes not a list"

    # Build a single haystack of all theme text for this archetype.
    haystack_parts: list[str] = []
    for t in themes_for_archetype:
        for v in _walk_strings(t):
            haystack_parts.append(v)
    haystack = "\n".join(haystack_parts).lower()

    for r in rationales:
        if not isinstance(r, dict):
            return "gate5_pain_connection: rationale entry not a dict"
        phrase = (r.get("phase_4_phrase") or "").strip().lower()
        if not phrase:
            return "gate5_pain_connection: empty phase_4_phrase"
        if phrase not in haystack:
            return (
                f"gate5_pain_connection: phrase not found verbatim in archetype themes: "
                f"{phrase[:80]!r}"
            )
    return None


def _walk_strings(obj):
    """Yield every string anywhere inside a nested dict/list."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


GATE_PIPELINE = [
    ("gate1", check_gate1),
    ("gate2", check_gate2),
    ("gate3", check_gate3),
    # gate4 (needs the source manifest) and gate5 (needs archetype themes)
    # have different signatures — handled inline in run_gates.
]


def run_gates(hypothesis: dict, themes_for_archetype: list[dict],
              source_manifest: dict | None = None) -> tuple[bool, str | None, str | None]:
    """
    Returns (survived, failed_gate_name, failure_reason).
    """
    for name, fn in GATE_PIPELINE:
        reason = fn(hypothesis)
        if reason:
            return False, name, reason
    reason = check_gate4(hypothesis, source_manifest)
    if reason:
        return False, "gate4", reason
    reason = check_gate5(hypothesis, themes_for_archetype)
    if reason:
        return False, "gate5", reason
    return True, None, None


# ──────────────────────────────────────────────────────────────
# Claude Opus call
# ──────────────────────────────────────────────────────────────


def build_user_message(
    archetype: dict,
    themes_for_archetype: list[dict],
    panels_sample: list[dict],
    available_sources: list[str],
    n_per_archetype: int,
    source_manifest: dict | None = None,
) -> str:
    """Compose the user message paired with prompts/hypothesis-generator.md as system."""
    return json.dumps(
        {
            "instruction": (
                f"Generate {n_per_archetype} candidate hypotheses for this archetype. "
                "Output JSONL — one hypothesis per line — and NOTHING else outside the "
                "fenced ```jsonl block. Each line is a JSON object matching the schema in "
                "the system prompt. Do not commentary, do not explain."
            ),
            "archetype": archetype,
            "themes_from_phase_4": themes_for_archetype,
            "phase_2_signal_panels_sample": panels_sample,
            "available_external_sources": available_sources,
            "source_temporal_classification": (source_manifest or {}).get("sources", {}),
            # The ONLY fields a hypothesis may test, with their types (Gate 4
            # rejects anything else). Numeric fields need a threshold predicate.
            "testable_fields": {"fields": signal_presence.FIELD_TYPES,
                                "record_fields": {f"{k[0]}.{k[1]}": v for k, v in
                                                  signal_presence.RECORD_FIELDS.items()}},
            "n_to_generate": n_per_archetype,
        },
        indent=2,
        default=str,
    )


def parse_jsonl_from_response(text: str) -> list[dict]:
    """
    Find a fenced ```jsonl block and parse its lines. Falls back to scanning every
    line of the response for valid JSON objects if no fence found.
    """
    out: list[dict] = []
    fence = re.search(r"```(?:jsonl|json)?\s*\n(.*?)```", text, re.DOTALL)
    body = fence.group(1) if fence else text
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def call_opus_for_archetype(
    archetype: dict,
    themes_for_archetype: list[dict],
    panels_sample: list[dict],
    available_sources: list[str],
    n_per_archetype: int,
    system_prompt: str,
    source_manifest: dict | None = None,
) -> tuple[list[dict], dict]:
    """
    Call Claude Opus with adaptive thinking enabled.
    Returns (parsed_hypotheses, usage_dict).
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        raise SystemExit("Error: `anthropic` package required. pip install anthropic")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Error: ANTHROPIC_API_KEY not set")

    client = Anthropic(api_key=api_key)

    user_msg = build_user_message(
        archetype, themes_for_archetype, panels_sample, available_sources,
        n_per_archetype, source_manifest,
    )

    # Adaptive thinking (current models reject a fixed budget_tokens).
    resp = client.messages.create(
        model=HYPOTHESIS_MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )

    # Concatenate all text blocks (skip thinking blocks).
    text_parts: list[str] = []
    for block in resp.content:
        # SDK returns objects with .type
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
    text = "\n".join(text_parts)

    hypotheses = parse_jsonl_from_response(text)
    usage = {
        "input_tokens": getattr(resp.usage, "input_tokens", 0),
        "output_tokens": getattr(resp.usage, "output_tokens", 0),
    }
    return hypotheses, usage


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────


def training_inputs(arc: dict, themes_by_customer: dict, panels_by_id: dict,
                    sample_size: int = 5) -> tuple[dict, list[dict], list[dict]]:
    """What hypothesis generation may see for one archetype: TRAINING data only.

    Holdout members, their themes, their panels and any outcome count that
    included them are left out, so Phase 6 can test the hypotheses on
    customers the generator never saw.
    """
    view = wlr_schema.training_view(arc)
    train_ids = [str(m.get("customer_id")) for m in view["members"]]
    themes: list[dict] = []
    for cid in train_ids:
        themes.extend(themes_by_customer.get(cid, []))
    panels = [panels_by_id[c] for c in train_ids if c in panels_by_id][:sample_size]
    return view, themes, panels


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True, help="Run identifier (matches outputs/<run-id>/)")
    ap.add_argument("--archetype-id", type=int, default=None,
                    help="Run for a single archetype only")
    ap.add_argument("--n-per-archetype", type=int, default=DEFAULT_N_PER_ARCHETYPE,
                    help=f"Target hypotheses per archetype (default {DEFAULT_N_PER_ARCHETYPE})")
    args = ap.parse_args()

    run_dir = OUTPUTS_ROOT / args.run_id
    if not run_dir.exists():
        raise SystemExit(f"Error: run dir {run_dir} does not exist")

    archetypes = wlr_schema.load_archetypes(run_dir / "archetypes.jsonl")
    themes = load_jsonl(run_dir / "themes.jsonl")
    panels = load_jsonl(run_dir / "customer_panels.jsonl")

    # Group themes & panels by archetype membership.
    panels_by_id = {str(p.get("customer_id")): p for p in panels}
    themes_by_customer = defaultdict(list)
    for t in themes:
        themes_by_customer[str(t.get("customer_id"))].append(t)

    # Determine available enrichment sources from any panel that has signals.
    available_sources: list[str] = []
    for p in panels:
        # Phase 2 writes "signal_panel"; "signals" kept for pre-fix runs.
        sigs = p.get("signal_panel") or p.get("signals") or {}
        if isinstance(sigs, dict):
            for k in sigs:
                # Source names live as top-level keys in the signal panel
                # (e.g. "firecrawl", "exa", "openweb_ninja", "apify_linkedin").
                if k not in available_sources:
                    available_sources.append(k)
        if available_sources:
            break

    if not available_sources:
        # Fallback to a defensive default list — Phase 5 prompt expects something.
        # Only built-in sources here; vertical-specific warehouses are surfaced
        # only when the operator's enrichment panel actually contains them.
        available_sources = [
            "firecrawl", "exa", "openweb_ninja", "apify_linkedin",
            "serper_news", "wayback",
        ]
    elif "wayback" not in available_sources:
        # The time-travel prover is always available (keyless, free).
        available_sources.append("wayback")

    source_manifest = load_source_manifest()

    # Phase 4.5 vertical scout: discovered sources extend BOTH the citable
    # source list and the temporal manifest, per archetype — so hypotheses can
    # cite the vertical's real registries, and Gate 4b can grade them.
    vertical_rows = load_vertical_sources(run_dir)
    if vertical_rows:
        source_manifest = merge_vertical_sources_into_manifest(source_manifest, vertical_rows)
        print(f"[phase5] vertical scout atlas loaded: {len(vertical_rows)} sources "
              f"across {len({r.get('archetype_id') for r in vertical_rows})} archetypes",
              file=sys.stderr)
    vs_by_arc: dict = defaultdict(list)
    for r in vertical_rows:
        vs_by_arc[r.get("archetype_id")].append(r)

    # Filter archetypes if --archetype-id given.
    target_archetypes = archetypes
    if args.archetype_id is not None:
        target_archetypes = [a for a in archetypes if a.get("id") == args.archetype_id]
        if not target_archetypes:
            raise SystemExit(f"Error: archetype id {args.archetype_id} not found")

    system_prompt = load_prompt("hypothesis-generator.md")

    survivors_path = run_dir / "hypotheses.jsonl"
    rejects_path = run_dir / "hypotheses-rejected.jsonl"

    # Append-friendly: clear if running full set; merge if single archetype.
    if args.archetype_id is None:
        survivors_path.write_text("")
        rejects_path.write_text("")

    summary: dict[int, dict[str, int]] = {}
    by_gate_failures: dict[str, int] = defaultdict(int)
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    for arc in target_archetypes:
        arc_id = arc.get("id")
        train_view, themes_for_arc, panels_sample = training_inputs(
            arc, themes_by_customer, panels_by_id)
        if not train_view["members"]:
            print(f"[archetype {arc_id}] no training members; skipping", file=sys.stderr)
            continue

        # This archetype's scout-discovered sources become citable, and the
        # full atlas rows ride along so the generator sees query_how +
        # verified examples, not just key names.
        arc_scout = vs_by_arc.get(arc_id, [])
        arc_sources = available_sources + [r["source_key"] for r in arc_scout
                                           if r.get("source_key")
                                           and r["source_key"] not in available_sources]
        arc_payload = dict(train_view)
        if arc_scout:
            arc_payload["vertical_source_atlas"] = arc_scout

        print(f"[archetype {arc_id}] calling model → target {args.n_per_archetype} "
              f"hypotheses ({len(arc_scout)} scout sources)", file=sys.stderr)
        try:
            hypotheses, usage = call_opus_for_archetype(
                arc_payload, themes_for_arc, panels_sample, arc_sources,
                args.n_per_archetype, system_prompt, source_manifest,
            )
        except Exception as e:
            print(f"[archetype {arc_id}] Opus call failed: {e}", file=sys.stderr)
            continue

        total_usage["input_tokens"] += usage["input_tokens"]
        total_usage["output_tokens"] += usage["output_tokens"]

        n_generated = len(hypotheses)
        n_survived = 0
        n_rejected = 0

        survivors_batch: list[dict] = []
        rejects_batch: list[dict] = []

        for h in hypotheses:
            # Guarantee archetype linkage.
            h.setdefault("archetype_id", arc_id)
            survived, gate, reason = run_gates(h, themes_for_arc, source_manifest)
            if survived:
                survivors_batch.append(h)
                n_survived += 1
            else:
                h_rejected = dict(h)
                h_rejected["failed_gate"] = gate
                h_rejected["failure_reason"] = reason
                rejects_batch.append(h_rejected)
                n_rejected += 1
                by_gate_failures[gate] += 1

        # Append (don't truncate) for resumability.
        with open(survivors_path, "a") as f:
            for row in survivors_batch:
                f.write(json.dumps(row, default=str) + "\n")
        with open(rejects_path, "a") as f:
            for row in rejects_batch:
                f.write(json.dumps(row, default=str) + "\n")

        summary[arc_id] = {
            "generated": n_generated,
            "survived": n_survived,
            "rejected": n_rejected,
        }
        print(
            f"[archetype {arc_id}] generated={n_generated} survived={n_survived} "
            f"rejected={n_rejected}",
            file=sys.stderr,
        )

    # ── Summary ──
    print("\n=== Phase 5 summary ===", file=sys.stderr)
    for arc_id, s in summary.items():
        print(f"  archetype {arc_id}: gen={s['generated']} surv={s['survived']} rej={s['rejected']}",
              file=sys.stderr)
    if by_gate_failures:
        print("\nFailure breakdown by gate:", file=sys.stderr)
        for gate, n in sorted(by_gate_failures.items()):
            print(f"  {gate}: {n}", file=sys.stderr)
    print(f"\nUsage: in={total_usage['input_tokens']} out={total_usage['output_tokens']}",
          file=sys.stderr)
    print(f"Survivors → {survivors_path}", file=sys.stderr)
    print(f"Rejects   → {rejects_path}", file=sys.stderr)

    # Append phase cost stub for orchestrate.
    cost_path = run_dir / "_phase_costs" / "phase5.json"
    cost_path.parent.mkdir(parents=True, exist_ok=True)
    cost_path.write_text(json.dumps({
        "phase": 5,
        "model": HYPOTHESIS_MODEL,
        "usage": total_usage,
        "summary": summary,
    }, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
