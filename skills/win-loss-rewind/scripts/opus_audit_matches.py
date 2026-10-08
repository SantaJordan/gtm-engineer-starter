#!/usr/bin/env python3
"""
Phase 1.3 — Audit every fuzzy-matched (call, customer) pair via Claude Opus
            with adaptive thinking via the Anthropic Batch API.

Known failure mode: a large share of unaudited LLM matches can be false
positives. This script runs every proposed match through Opus and writes only
verified matches into matched_calls.jsonl. If audit precision <90%, the run
halts (per architecture.md).

Six sub-commands:

    python opus_audit_matches.py create   --run-id <id>   # Build batch requests JSONL
    python opus_audit_matches.py submit   --run-id <id>   # Submit to Anthropic Batch API
    python opus_audit_matches.py status   --run-id <id>   # Check progress
    python opus_audit_matches.py download --run-id <id>   # Stream results to disk
    python opus_audit_matches.py parse    --run-id <id>   # Write matched_calls.jsonl + audit_report.md
    python opus_audit_matches.py run-all  --run-id <id>   # All steps; polls every 60s

Inputs (per run-id):
    outputs/<run-id>/proposed_matches.jsonl
    outputs/<run-id>/gong_calls_raw.jsonl

Outputs:
    outputs/<run-id>/.audit_batch_id          # batch ID + metadata
    outputs/<run-id>/.audit_requests.jsonl    # built request payloads (audit-trail)
    outputs/<run-id>/.audit_raw_results.jsonl # streamed batch responses
    outputs/<run-id>/audit_parse_errors.jsonl # rows where JSON parse failed
    outputs/<run-id>/matched_calls.jsonl      # FINAL: verified matches only
    outputs/<run-id>/audit_report.md          # FINAL: counts + precision + halt diagnostic

Env (read via python-dotenv from .env):
    ANTHROPIC_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"
PROMPT_PATH = SKILL_ROOT / "prompts" / "opus-match-audit.md"

from wlr_config import OPUS_MODEL as DEFAULT_MODEL  # noqa: E402
MAX_TOKENS = 1024
POLL_INTERVAL = 60  # seconds

TRANSCRIPT_HEAD = 1500
TRANSCRIPT_TAIL = 500

# Phase-1 hard gate from architecture.md
PRECISION_GATE = 0.90
CONFIDENCE_THRESHOLD = 0.80


# ──────────────────────────────────────────────────────────────
# Path helpers
# ──────────────────────────────────────────────────────────────


def run_paths(run_id: str) -> dict:
    run_dir = OUTPUTS_ROOT / run_id
    return {
        "run_dir": run_dir,
        "proposed_matches": run_dir / "proposed_matches.jsonl",
        "gong_calls": run_dir / "gong_calls_raw.jsonl",
        "batch_id_file": run_dir / ".audit_batch_id",
        "requests_file": run_dir / ".audit_requests.jsonl",
        "raw_results": run_dir / ".audit_raw_results.jsonl",
        "parse_errors": run_dir / "audit_parse_errors.jsonl",
        "matched_calls": run_dir / "matched_calls.jsonl",
        "audit_report": run_dir / "audit_report.md",
    }


def load_jsonl(path: Path):
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


# ──────────────────────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────────────────────


def load_system_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise SystemExit(f"Error: prompt not found at {PROMPT_PATH}")
    return PROMPT_PATH.read_text()


def transcript_excerpt(transcript: list) -> str:
    """Render Gong transcript (list of segments) to head+tail text excerpt."""
    if not transcript:
        return ""
    parts: list[str] = []
    for seg in transcript:
        # Gong transcript segments: {speakerId, sentences: [{start, end, text}]}
        if isinstance(seg, dict):
            sentences = seg.get("sentences") or []
            for s in sentences:
                txt = s.get("text") if isinstance(s, dict) else None
                if txt:
                    parts.append(txt)
        elif isinstance(seg, str):
            parts.append(seg)
    full = " ".join(parts).strip()
    if not full:
        return ""
    if len(full) <= TRANSCRIPT_HEAD + TRANSCRIPT_TAIL:
        return full
    return full[:TRANSCRIPT_HEAD] + " […truncated…] " + full[-TRANSCRIPT_TAIL:]


def build_user_message(call: dict, match: dict) -> str:
    """Build the per-pair user message (call + proposed_match + fuzzy signal)."""
    parties_summary = []
    for p in call.get("parties", []) or []:
        parties_summary.append(
            {
                "name": p.get("name"),
                "email": p.get("emailAddress") or p.get("email"),
                "affiliation": p.get("affiliation"),
            }
        )

    payload = {
        "gong_call": {
            "call_id": call.get("call_id"),
            "title": call.get("title"),
            "started": call.get("started"),
            "duration_seconds": call.get("duration_seconds"),
            "parties": parties_summary,
            "transcript_excerpt": transcript_excerpt(call.get("transcript") or []),
        },
        "proposed_match": {
            "customer_id": match.get("customer_id"),
            "company_name": match.get("customer_name"),
            "company_domain": match.get("company_domain"),
            "outcome": match.get("outcome"),
            "close_date": match.get("close_date"),
        },
        "fuzzy_match_signal": {
            "method": match.get("fuzzy_match_method"),
            "score": match.get("fuzzy_match_score"),
            "evidence": match.get("fuzzy_match_evidence"),
        },
    }
    return (
        "Audit this proposed (Gong call → customer) match. Return ONLY the JSON "
        "verdict object specified in the system prompt — no markdown fences, no "
        "preamble.\n\n```json\n"
        + json.dumps(payload, indent=2, default=str)
        + "\n```"
    )


# ──────────────────────────────────────────────────────────────
# Sub-command: create
# ──────────────────────────────────────────────────────────────


def cmd_create(run_id: str) -> int:
    paths = run_paths(run_id)
    if not paths["proposed_matches"].exists():
        print(f"Error: missing {paths['proposed_matches']}", flush=True)
        return 1
    if not paths["gong_calls"].exists():
        print(f"Error: missing {paths['gong_calls']}", flush=True)
        return 1

    paths["run_dir"].mkdir(parents=True, exist_ok=True)
    system_prompt = load_system_prompt()

    # Index calls by id (calls are smaller than matches because matches can repeat)
    print(f"Loading calls from {paths['gong_calls']}", flush=True)
    calls_by_id: dict = {}
    for call in load_jsonl(paths["gong_calls"]):
        calls_by_id[call.get("call_id")] = call
    print(f"  Indexed {len(calls_by_id)} calls", flush=True)

    print(f"Loading matches from {paths['proposed_matches']}", flush=True)
    matches = list(load_jsonl(paths["proposed_matches"]))
    print(f"  {len(matches)} proposed matches", flush=True)

    written = 0
    skipped_no_call = 0
    with open(paths["requests_file"], "w") as f:
        for i, m in enumerate(matches):
            call_id = m.get("call_id")
            call = calls_by_id.get(call_id)
            if not call:
                skipped_no_call += 1
                continue

            user_msg = build_user_message(call, m)
            req = {
                "custom_id": f"match-{i}",
                "match_index": i,
                "call_id": call_id,
                "customer_id": m.get("customer_id"),
                "system": system_prompt,
                "user": user_msg,
                # Keep originals so downstream `parse` can write matched_calls without re-loading
                "original_match": m,
                "call_meta": {
                    "title": call.get("title"),
                    "started": call.get("started"),
                    "gong_url": call.get("gong_url"),
                    "duration_seconds": call.get("duration_seconds"),
                },
            }
            f.write(json.dumps(req, default=str) + "\n")
            written += 1

    print(
        f"\nWrote {written} batch-request rows to {paths['requests_file']} "
        f"(skipped {skipped_no_call} matches with missing call data)",
        flush=True,
    )
    return 0


# ──────────────────────────────────────────────────────────────
# Sub-command: submit
# ──────────────────────────────────────────────────────────────


def cmd_submit(run_id: str) -> int:
    paths = run_paths(run_id)
    if not paths["requests_file"].exists():
        print(
            f"Error: {paths['requests_file']} not found. Run `create` first.",
            flush=True,
        )
        return 1
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not set in env / .env", flush=True)
        return 1

    client = Anthropic()
    requests_payload: list[Request] = []
    for row in load_jsonl(paths["requests_file"]):
        params: MessageCreateParamsNonStreaming = {
            "model": DEFAULT_MODEL,
            "max_tokens": MAX_TOKENS,
            "thinking": {"type": "adaptive"},
            "system": row["system"],
            "messages": [{"role": "user", "content": row["user"]}],
        }
        requests_payload.append(
            Request(custom_id=row["custom_id"], params=params)
        )

    print(f"Submitting {len(requests_payload)} requests to Anthropic Batch API...", flush=True)
    batch = client.messages.batches.create(requests=requests_payload)
    meta = {
        "batch_id": batch.id,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "request_count": len(requests_payload),
        "model": DEFAULT_MODEL,
    }
    paths["batch_id_file"].write_text(json.dumps(meta, indent=2))
    print(f"  Batch ID: {batch.id}", flush=True)
    print(f"  Status: {batch.processing_status}", flush=True)
    print(f"  Saved metadata to: {paths['batch_id_file']}", flush=True)
    return 0


# ──────────────────────────────────────────────────────────────
# Sub-command: status
# ──────────────────────────────────────────────────────────────


def _read_batch_id(paths: dict) -> str:
    if not paths["batch_id_file"].exists():
        raise SystemExit(
            f"Error: {paths['batch_id_file']} not found. Run `submit` first."
        )
    meta = json.loads(paths["batch_id_file"].read_text())
    return meta["batch_id"]


def cmd_status(run_id: str) -> int:
    paths = run_paths(run_id)
    batch_id = _read_batch_id(paths)
    client = Anthropic()
    batch = client.messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    total = counts.processing + counts.succeeded + counts.errored + counts.canceled + counts.expired
    print(f"Batch: {batch_id}", flush=True)
    print(f"  Status: {batch.processing_status}", flush=True)
    print(f"  Succeeded:  {counts.succeeded}/{total}", flush=True)
    print(f"  Errored:    {counts.errored}", flush=True)
    print(f"  Processing: {counts.processing}", flush=True)
    print(f"  Expired:    {counts.expired}", flush=True)
    print(f"  Canceled:   {counts.canceled}", flush=True)
    return 0


# ──────────────────────────────────────────────────────────────
# Sub-command: download
# ──────────────────────────────────────────────────────────────


def cmd_download(run_id: str) -> int:
    paths = run_paths(run_id)
    batch_id = _read_batch_id(paths)
    client = Anthropic()

    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(
            f"Batch {batch_id} not yet ended (status={batch.processing_status}). "
            f"Run `status` until it ends, then re-run `download`.",
            flush=True,
        )
        return 1

    print(f"Streaming results for batch {batch_id} → {paths['raw_results']}", flush=True)
    succeeded = 0
    errored = 0
    with open(paths["raw_results"], "w") as f:
        for result in client.messages.batches.results(batch_id):
            row = {
                "custom_id": result.custom_id,
                "result_type": result.result.type,
            }
            if result.result.type == "succeeded":
                text_chunks: list[str] = []
                for block in result.result.message.content:
                    if block.type == "text":
                        text_chunks.append(block.text)
                row["text"] = "\n".join(text_chunks).strip()
                succeeded += 1
            else:
                err = getattr(result.result, "error", None)
                row["error"] = str(err) if err else result.result.type
                errored += 1
            f.write(json.dumps(row, default=str) + "\n")

    print(f"  Wrote {succeeded + errored} rows ({succeeded} ok, {errored} errored)", flush=True)
    return 0


# ──────────────────────────────────────────────────────────────
# Sub-command: parse
# ──────────────────────────────────────────────────────────────


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_audit_response(text: str) -> dict:
    """Parse the Opus verdict JSON; tolerate ```json fences and stray prose."""
    text = (text or "").strip()
    if not text:
        return {
            "verdict": "unclear",
            "confidence": 0.0,
            "evidence_for": [],
            "evidence_against": [],
            "failure_mode": "other",
            "rationale": "empty response",
            "_parse_error": "empty",
        }
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError as e:
                return {
                    "verdict": "unclear",
                    "confidence": 0.0,
                    "evidence_for": [],
                    "evidence_against": [],
                    "failure_mode": "other",
                    "rationale": f"json parse error: {e}",
                    "_parse_error": str(e),
                    "_raw_text_head": text[:300],
                }
    return {
        "verdict": "unclear",
        "confidence": 0.0,
        "evidence_for": [],
        "evidence_against": [],
        "failure_mode": "other",
        "rationale": "no JSON object found",
        "_parse_error": "no_json_found",
        "_raw_text_head": text[:300],
    }


def cmd_parse(run_id: str) -> int:
    paths = run_paths(run_id)
    if not paths["raw_results"].exists():
        print(f"Error: {paths['raw_results']} not found. Run `download` first.", flush=True)
        return 1
    if not paths["requests_file"].exists():
        print(f"Error: {paths['requests_file']} not found. Run `create` first.", flush=True)
        return 1

    # Index requests by custom_id for original_match recovery
    req_by_id: dict[str, dict] = {}
    for r in load_jsonl(paths["requests_file"]):
        req_by_id[r["custom_id"]] = r

    # Parse every result
    verdicts: list[dict] = []
    parse_errors: list[dict] = []

    for raw in load_jsonl(paths["raw_results"]):
        cid = raw.get("custom_id")
        req = req_by_id.get(cid, {})
        original = req.get("original_match", {})
        call_meta = req.get("call_meta", {})

        if raw.get("result_type") != "succeeded":
            verdict_obj = {
                "verdict": "unclear",
                "confidence": 0.0,
                "evidence_for": [],
                "evidence_against": [],
                "failure_mode": "other",
                "rationale": f"batch_{raw.get('result_type')}: {raw.get('error', '')}",
            }
        else:
            verdict_obj = parse_audit_response(raw.get("text", ""))
            if "_parse_error" in verdict_obj:
                parse_errors.append(
                    {
                        "custom_id": cid,
                        "call_id": original.get("call_id"),
                        "customer_id": original.get("customer_id"),
                        "parse_error": verdict_obj["_parse_error"],
                        "raw_text_head": verdict_obj.get("_raw_text_head", ""),
                    }
                )

        try:
            confidence = float(verdict_obj.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        verdicts.append(
            {
                "custom_id": cid,
                "call_id": original.get("call_id"),
                "customer_id": original.get("customer_id"),
                "customer_name": original.get("customer_name"),
                "fuzzy_match_score": original.get("fuzzy_match_score"),
                "fuzzy_match_method": original.get("fuzzy_match_method"),
                "fuzzy_match_evidence": original.get("fuzzy_match_evidence"),
                "call_title": call_meta.get("title"),
                "call_started": call_meta.get("started"),
                "gong_url": call_meta.get("gong_url"),
                "audit_verdict": verdict_obj.get("verdict", "unclear"),
                "audit_confidence": confidence,
                "audit_evidence_for": verdict_obj.get("evidence_for") or [],
                "audit_evidence_against": verdict_obj.get("evidence_against") or [],
                "audit_failure_mode": verdict_obj.get("failure_mode"),
                "audit_rationale": verdict_obj.get("rationale", ""),
            }
        )

    # Write parse errors
    if parse_errors:
        with open(paths["parse_errors"], "w") as f:
            for e in parse_errors:
                f.write(json.dumps(e, default=str) + "\n")
        print(
            f"  Wrote {len(parse_errors)} parse errors to {paths['parse_errors']}",
            flush=True,
        )

    # Write matched_calls.jsonl: verdict==match AND confidence>=0.80
    verified: list[dict] = [
        v
        for v in verdicts
        if v["audit_verdict"] == "match"
        and v["audit_confidence"] >= CONFIDENCE_THRESHOLD
    ]
    with open(paths["matched_calls"], "w") as f:
        for v in verified:
            f.write(json.dumps(v, default=str) + "\n")
    print(
        f"\nWrote {len(verified)} verified matches to {paths['matched_calls']}",
        flush=True,
    )

    # Tallies
    total = len(verdicts)
    n_match = sum(1 for v in verdicts if v["audit_verdict"] == "match")
    n_no_match = sum(1 for v in verdicts if v["audit_verdict"] == "no_match")
    n_unclear = sum(1 for v in verdicts if v["audit_verdict"] == "unclear")
    decided = n_match + n_no_match
    precision = (n_match / decided) if decided else 0.0

    failure_modes = Counter(
        v["audit_failure_mode"]
        for v in verdicts
        if v["audit_verdict"] == "no_match" and v["audit_failure_mode"]
    )

    # Write audit_report.md
    halt = precision < PRECISION_GATE and total > 0
    lines: list[str] = []
    lines.append("# Phase 1 Audit Report")
    lines.append("")
    lines.append(f"- **Run ID:** `{run_id}`")
    lines.append(f"- **Generated:** {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- **Model:** `{DEFAULT_MODEL}` (adaptive thinking, Batch API)")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append(f"- Total proposed matches audited: **{total}**")
    lines.append(f"- Verified `match` (any confidence): **{n_match}**")
    lines.append(f"- Verified `match` AND confidence ≥ {CONFIDENCE_THRESHOLD:.2f}: **{len(verified)}**")
    lines.append(f"- `no_match`: **{n_no_match}**")
    lines.append(f"- `unclear`: **{n_unclear}**")
    lines.append(f"- Parse errors: **{len(parse_errors)}**")
    lines.append("")
    lines.append("## Precision")
    lines.append("")
    lines.append(
        f"- Precision (match / (match + no_match)) = **{precision:.2%}** "
        f"({n_match} / {decided})"
    )
    lines.append(f"- Phase-1 gate: ≥ {PRECISION_GATE:.0%}")
    if halt:
        lines.append("")
        lines.append("> **HALT — precision below gate.** Do not proceed to Phase 2.")
        lines.append("> Review the failure-mode breakdown below; the fuzzy matcher is")
        lines.append("> producing too many false positives. Tighten title/email rules,")
        lines.append("> raise `--min-score`, or invest in a better-name index, then re-run")
        lines.append("> `match_gong.py` and this audit.")
    else:
        lines.append("")
        lines.append("> Gate passed. Proceed to Phase 2.")
    lines.append("")
    lines.append("## Failure-mode breakdown (no_match verdicts)")
    lines.append("")
    if failure_modes:
        for mode, count in failure_modes.most_common():
            lines.append(f"- `{mode}`: {count}")
    else:
        lines.append("- (none)")
    lines.append("")
    if parse_errors:
        lines.append("## Parse errors")
        lines.append("")
        lines.append(
            f"{len(parse_errors)} responses could not be parsed as JSON; see "
            f"`audit_parse_errors.jsonl`. These are counted as `unclear`."
        )
        lines.append("")

    paths["audit_report"].write_text("\n".join(lines))
    print(f"Wrote audit report to {paths['audit_report']}", flush=True)
    print(f"  Precision: {precision:.2%}  (gate: {PRECISION_GATE:.0%})", flush=True)
    if halt:
        print(
            "\n  HALT: precision below gate — see audit_report.md for diagnostic.",
            flush=True,
        )
        return 2
    return 0


# ──────────────────────────────────────────────────────────────
# Sub-command: run-all
# ──────────────────────────────────────────────────────────────


def cmd_run_all(run_id: str) -> int:
    paths = run_paths(run_id)

    rc = cmd_create(run_id)
    if rc:
        return rc
    rc = cmd_submit(run_id)
    if rc:
        return rc

    batch_id = _read_batch_id(paths)
    client = Anthropic()
    print(f"\nPolling batch {batch_id} every {POLL_INTERVAL}s...", flush=True)
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        total = counts.processing + counts.succeeded + counts.errored + counts.canceled + counts.expired
        print(
            f"  [{datetime.now().strftime('%H:%M:%S')}] "
            f"status={batch.processing_status} | "
            f"succeeded={counts.succeeded}/{total} | "
            f"errored={counts.errored} | "
            f"processing={counts.processing}",
            flush=True,
        )
        if batch.processing_status == "ended":
            print("  Batch ended.", flush=True)
            break
        time.sleep(POLL_INTERVAL)

    rc = cmd_download(run_id)
    if rc:
        return rc
    return cmd_parse(run_id)


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 1.3 — audit fuzzy-matched (Gong call → customer) pairs via Claude Opus + adaptive thinking."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, helptext in [
        ("create", "Build batch-request payloads from proposed_matches.jsonl"),
        ("submit", "Submit the prepared requests to the Anthropic Batch API"),
        ("status", "Print current batch progress"),
        ("download", "Stream completed results to .audit_raw_results.jsonl"),
        ("parse", "Parse results, write matched_calls.jsonl + audit_report.md"),
        ("run-all", "create → submit → poll every 60s → download → parse"),
    ]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--run-id", required=True, help="Run identifier")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cmd = args.cmd
    rc = 0
    if cmd == "create":
        rc = cmd_create(args.run_id)
    elif cmd == "submit":
        rc = cmd_submit(args.run_id)
    elif cmd == "status":
        rc = cmd_status(args.run_id)
    elif cmd == "download":
        rc = cmd_download(args.run_id)
    elif cmd == "parse":
        rc = cmd_parse(args.run_id)
    elif cmd == "run-all":
        rc = cmd_run_all(args.run_id)
    else:
        print(f"Unknown command: {cmd}", flush=True)
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
