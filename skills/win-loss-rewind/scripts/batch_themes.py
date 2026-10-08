#!/usr/bin/env python3
"""
Phase 4 — Theme Extraction via Anthropic Message Batches API.

Standard six-step Batch API CLI (create / submit / status / download / parse / run-all).
For every customer we build a batch request whose system prompt is
`prompts/theme-extractor.md`, and whose user message bundles the customer
record + Phase 2 signal panel + Phase 3 archetype assignment + every matched
call transcript with role_in_lifecycle inferred from temporal position.

As of Phase 1.4 (Speaker Attribution), the transcript we pass is NOT raw text.
We read `calls_with_speaker_attribution.jsonl` (from label_speakers.py) and pass
the per-customer ROSTER (who is company vs. customer) + every transcript turn
TAGGED company|customer|unknown + the elicitation-graded CUSTOMER statements
(unprompted|led|seller_echo). The theme extractor extracts pain ONLY from
customer turns, excludes seller_echo from demand, and lowers confidence when the
pain is mostly led/echo. Raw, un-attributed transcript is a fallback only when
Phase 1.4 has not run.

Sub-commands:
    python batch_themes.py create     --run-id <id>
    python batch_themes.py submit     --run-id <id>
    python batch_themes.py status     --run-id <id>
    python batch_themes.py download   --run-id <id>
    python batch_themes.py parse      --run-id <id>
    python batch_themes.py run-all    --run-id <id>   (create+submit+poll+download+parse)

Outputs (per run-id):
    outputs/<run-id>/themes_batch_requests.jsonl   batch input
    outputs/<run-id>/themes_batch_lookup.json      custom_id -> customer
    outputs/<run-id>/themes_batch_info.json        batch_id + status
    outputs/<run-id>/themes_batch_results.jsonl    batch raw results
    outputs/<run-id>/themes.jsonl                  parsed themes
    outputs/<run-id>/themes_parse_errors.jsonl     rows that failed to parse
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

# ------------------------------------------------------------------ paths

from wlr_config import repo_root  # noqa: E402
REPO_ROOT = repo_root()
SKILL_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = SKILL_ROOT / "prompts" / "theme-extractor.md"


def outputs_dir(run_id: str) -> Path:
    return SKILL_ROOT / "outputs" / run_id


def paths(run_id: str) -> dict[str, Path]:
    rdir = outputs_dir(run_id)
    return {
        "rdir": rdir,
        "matched_calls": rdir / "matched_calls.jsonl",
        "attribution": rdir / "calls_with_speaker_attribution.jsonl",
        "panels": rdir / "customer_panels.jsonl",
        "archetypes": rdir / "archetypes.jsonl",
        "requests": rdir / "themes_batch_requests.jsonl",
        "lookup": rdir / "themes_batch_lookup.json",
        "info": rdir / "themes_batch_info.json",
        "results": rdir / "themes_batch_results.jsonl",
        "themes": rdir / "themes.jsonl",
        "errors": rdir / "themes_parse_errors.jsonl",
    }


# ------------------------------------------------------------------ env

load_dotenv(REPO_ROOT / ".env")
load_dotenv(SKILL_ROOT / ".env")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
from wlr_config import SONNET_MODEL as MODEL  # noqa: E402
MAX_TOKENS = 2000


# ------------------------------------------------------------------ data load

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def index_by_customer(rows: list[dict], key_candidates: tuple[str, ...]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        for k in key_candidates:
            if r.get(k):
                out.setdefault(str(r[k]), r)
                break
    return out


def calls_for_customer(matched_calls: list[dict], customer_id: str, company: str) -> list[dict]:
    matches: list[dict] = []
    for c in matched_calls:
        cid = str(c.get("customer_id") or "")
        co = (c.get("company") or c.get("account_name") or "").strip().lower()
        if (cid and cid == str(customer_id)) or (company and co == company.strip().lower()):
            matches.append(c)
    matches.sort(key=lambda x: x.get("started") or x.get("call_date") or "")
    return matches


def infer_role_in_lifecycle(call_idx: int, n_calls: int, outcome: Optional[str]) -> str:
    """Heuristic: first call = discovery, last call = outcome-conditional, middle = demo/negotiation."""
    if n_calls == 1:
        return "discovery"
    if call_idx == 0:
        return "discovery"
    if call_idx == n_calls - 1:
        if outcome == "won":
            return "negotiation"
        if outcome == "lost":
            return "negotiation"
        if outcome == "churned":
            return "churn_save"
        if outcome == "healthy":
            return "qbr"
        return "other"
    if call_idx == 1:
        return "demo"
    return "negotiation"


# ------------------------------------------------------------------ prompt loading

def load_system_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Missing {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def truncate_transcript(text: str, max_chars: int = 32000) -> str:
    """Head + tail truncation per prompt spec ('up to 8000 tokens; head + tail if longer')."""
    if not text or len(text) <= max_chars:
        return text or ""
    half = max_chars // 2
    return text[:half] + "\n\n[...transcript truncated...]\n\n" + text[-half:]


def is_confirmed(statement: dict) -> bool:
    """A grade counts as verified only when a human confirmed it.

    Heuristic grades (the transcript importer, the Gong labeller) carry
    `provisional: true` or no confirmation at all. Confirm a line by setting
    `"confirmed": true` (or `"grade_basis": "human"`) on it in
    calls_with_speaker_attribution.jsonl after reading it in context.
    """
    return bool(statement.get("confirmed")) or statement.get("grade_basis") == "human"


def grade_counts(calls: list[dict], attribution_by_call: dict) -> dict:
    """How many customer lines this customer's model input carries, by status."""
    n_conf = n_prov = 0
    for c in calls:
        attr = attribution_by_call.get(str(c.get("call_id") or c.get("id") or "")) or {}
        for cs in attr.get("customer_statements") or []:
            if is_confirmed(cs):
                n_conf += 1
            else:
                n_prov += 1
    return {"customer_lines_confirmed": n_conf, "customer_lines_provisional": n_prov}


def _render_attributed_call(attr: dict, max_chars: int = 32000) -> tuple[str, str]:
    """
    Render one Phase-1.4 attributed call into (roster_block, transcript_block).

    The transcript block is role-tagged, one line per turn:
        [company]   <text>
        [customer · unprompted]  <text>
        [unknown]   <text>
    Seller speech is KEPT as context (so the model can see what was led), but
    each customer line carries its elicitation tier so the extractor can obey
    "customer-only, unprompted-weighted, seller_echo-excluded".
    """
    # Index customer elicitation by verbatim text (exact match within this call).
    elic_by_text: dict[str, str] = {}
    confirmed_by_text: dict[str, bool] = {}
    for cs in attr.get("customer_statements") or []:
        elic_by_text[cs.get("text", "")] = cs.get("elicitation", "unprompted")
        confirmed_by_text[cs.get("text", "")] = is_confirmed(cs)

    roster_lines: list[str] = []
    for p in attr.get("roster") or []:
        roster_lines.append(
            f"- {p.get('name') or p.get('speaker_label')}: "
            f"role={p.get('role')} (source={p.get('source')}, conf={p.get('confidence')})"
        )
    roster_block = "\n".join(roster_lines) if roster_lines else "- (no roster — roles unknown)"

    turn_lines: list[str] = []
    used = 0
    truncated = False
    for turn in attr.get("turns") or []:
        role = turn.get("role", "unknown")
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        if role == "customer":
            elic = elic_by_text.get(text, "led")
            tag = (f"[customer · {elic}]" if confirmed_by_text.get(text)
                   else f"[customer · {elic} · provisional]")
        else:
            tag = f"[{role}]"
        line = f"{tag} {text}"
        if used + len(line) > max_chars:
            truncated = True
            break
        turn_lines.append(line)
        used += len(line)
    if truncated:
        turn_lines.append("[...transcript truncated...]")
    return roster_block, "\n".join(turn_lines)


def build_user_message(
    customer: dict,
    panel: dict,
    archetype: Optional[dict],
    calls: list[dict],
    attribution_by_call: Optional[dict[str, dict]] = None,
) -> str:
    attribution_by_call = attribution_by_call or {}
    parts: list[str] = []
    parts.append("Extract themes from the customer below per the JSON schema in the system prompt.\n")

    parts.append("## customer\n")
    cust = {
        "id": customer.get("customer_id") or customer.get("id"),
        "company": customer.get("company"),
        "domain": customer.get("domain"),
        "outcome": customer.get("outcome"),
        "close_date": customer.get("close_date"),
        "archetype_id": (archetype or {}).get("cluster_id"),
        "archetype_name": (archetype or {}).get("archetype_name"),
    }
    parts.append("```json")
    parts.append(json.dumps(cust, indent=2))
    parts.append("```\n")

    parts.append("## signal_panel (Phase 2)\n")
    parts.append("```json")
    parts.append(json.dumps(panel.get("signal_panel") or {}, indent=2))
    parts.append("```\n")

    flags = panel.get("reconciliation_flags") or []
    if flags:
        parts.append("## reconciliation_flags\n")
        for f in flags:
            parts.append(f"- {f}")
        parts.append("")

    parts.append(
        "## ATTRIBUTION KEY (Speaker Attribution Protocol)\n"
        "Every transcript turn below is tagged with the speaker's employment "
        "(`company` = the seller running this analysis, `customer` = the buyer, "
        "`unknown` = could not be placed — NEVER treat as customer). Each "
        "`customer` line also carries an elicitation tier:\n"
        "- `unprompted` — the customer raised it on their own.\n"
        "- `led` — they said it only after the seller framed/asked it (discount it).\n"
        "- `seller_echo` — the seller's own stock line (exclude from demand).\n"
        "A tier marked `provisional` was set by a heuristic and NOT confirmed by a "
        "human: treat a provisional `unprompted` line like a `led` line, never as "
        "verified customer-initiated evidence, and say so in your output. Only an "
        "unmarked (confirmed) `unprompted` line is strong evidence.\n"
        "Extract pain ONLY from `customer` turns; exclude `seller_echo` from demand; "
        "lower confidence when pain rests on `led` or provisional lines.\n"
    )

    parts.append(f"## call_transcripts ({len(calls)} call(s))\n")
    for i, c in enumerate(calls):
        role = infer_role_in_lifecycle(i, len(calls), customer.get("outcome"))
        call_id = c.get("call_id") or c.get("id") or ""
        parts.append(f"### call {i+1}/{len(calls)}")
        parts.append(f"- call_id: {call_id}")
        parts.append(f"- started: {c.get('started') or c.get('call_date') or ''}")
        parts.append(f"- role_in_lifecycle: {role}")

        attr = attribution_by_call.get(str(call_id))
        if attr:
            roster_block, transcript_block = _render_attributed_call(attr, max_chars=32000)
            parts.append("- roster (who is company vs customer):")
            parts.append(roster_block)
            parts.append("- transcript (role-tagged turns; seller kept as context, never counted as customer signal):")
            parts.append("```")
            parts.append(transcript_block or "(no transcript turns)")
            parts.append("```\n")
        else:
            # Fallback: Phase 1.4 has not run for this call — pass raw text and
            # warn the extractor that no attribution is available.
            transcript = c.get("transcript") or c.get("transcript_text") or c.get("text") or ""
            parts.append("- WARNING: no speaker attribution available for this call; "
                         "treat speaker employment as `unknown` and elicitation as `led` (conservative).")
            parts.append("```")
            parts.append(truncate_transcript(transcript, max_chars=32000))
            parts.append("```\n")

    parts.append(
        "Return JSON only matching the schema. No prose, no markdown fences."
    )
    return "\n".join(parts)


# ------------------------------------------------------------------ batch: create

#: Set WLR_INCLUDE_NO_CALL_CUSTOMERS=1 to send customers with no matched calls
#: anyway (each request is billed and returns low-confidence themes).
INCLUDE_NO_CALL_CUSTOMERS = os.environ.get("WLR_INCLUDE_NO_CALL_CUSTOMERS", "") == "1"


def cmd_create(run_id: str) -> int:
    P = paths(run_id)
    P["rdir"].mkdir(parents=True, exist_ok=True)

    system_prompt = load_system_prompt()
    panels = load_jsonl(P["panels"])
    matched_calls = load_jsonl(P["matched_calls"])
    archetypes_jsonl = load_jsonl(P["archetypes"])

    if not panels:
        print(f"ERROR: {P['panels']} missing/empty.", file=sys.stderr)
        return 2

    # Phase 1.4 — speaker attribution (role-tagged turns + elicitation tiers),
    # keyed by call_id. If absent, build_user_message falls back to raw text and
    # warns the extractor (conservative: unknown employment / led elicitation).
    attribution_rows = load_jsonl(P["attribution"])
    attribution_by_call: dict[str, dict] = {
        str(a.get("call_id")): a for a in attribution_rows if a.get("call_id")
    }
    if attribution_by_call:
        print(f"[create] loaded speaker attribution for {len(attribution_by_call)} calls "
              f"(Phase 1.4); passing role-tagged turns + elicitation tiers.")
    else:
        print(f"[create] WARNING: {P['attribution'].name} missing/empty — "
              f"Phase 1.4 (speaker attribution) did not run. Falling back to raw "
              f"transcript; the extractor will treat speakers as unknown / led.",
              file=sys.stderr)

    # Build archetype lookups
    archetype_by_cluster = {
        a.get("cluster_id"): a for a in archetypes_jsonl
        if a.get("_kind") == "archetype" or "cluster_id" in a and "_kind" not in a
    }
    assignment_by_customer = {
        str(a.get("customer_id")): a for a in archetypes_jsonl
        if a.get("_kind") == "customer_assignment"
    }

    requests_list: list[dict] = []
    lookup: dict[str, dict] = {}
    skipped_no_calls = 0

    for panel in panels:
        cust_id = str(panel.get("customer_id") or "")
        company = panel.get("company") or ""
        calls = calls_for_customer(matched_calls, cust_id, company)
        if not calls:
            skipped_no_calls += 1
            if not INCLUDE_NO_CALL_CUSTOMERS:
                # No transcript, no themes: skip rather than pay for a request
                # the model can only answer with "no evidence".
                continue
            calls = []

        assignment = assignment_by_customer.get(cust_id, {})
        cluster_id = assignment.get("cluster_id")
        archetype = archetype_by_cluster.get(cluster_id) if cluster_id is not None else None

        customer_record = {
            "customer_id": cust_id,
            "id": cust_id,
            "company": company,
            "domain": panel.get("domain"),
            "outcome": panel.get("outcome"),
            "close_date": panel.get("close_date"),
        }

        user_msg = build_user_message(
            customer_record, panel, archetype, calls, attribution_by_call
        )
        custom_id = f"wlr-themes-{cust_id}"[:64]  # batch API caps custom_id length

        request = {
            "custom_id": custom_id,
            "params": {
                "model": MODEL,
                "max_tokens": MAX_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_msg}],
            },
        }
        requests_list.append(request)
        lookup[custom_id] = {
            **grade_counts(calls, attribution_by_call),
            "customer_id": cust_id,
            "company": company,
            "outcome": panel.get("outcome"),
            "archetype_id": cluster_id,
            "n_calls": len(calls),
            "holdout": assignment.get("holdout", False),
        }

    with P["requests"].open("w") as f:
        for req in requests_list:
            f.write(json.dumps(req) + "\n")
    with P["lookup"].open("w") as f:
        json.dump(lookup, f, indent=2)

    # Cost estimate at Batch API prices for claude-sonnet-5-5 ($2/$10 per 1M
    # tokens, 50% off on Batch = $1/$5). Edit BATCH_PRICE if you change models.
    BATCH_PRICE = {"in": 1.00, "out": 5.00}
    avg_input_chars = sum(
        len(r["params"]["system"]) + len(r["params"]["messages"][0]["content"])
        for r in requests_list
    ) / max(1, len(requests_list))
    est_in_tokens = (avg_input_chars / 4) * len(requests_list)
    est_out_tokens = MAX_TOKENS * 0.6 * len(requests_list)  # assume 60% utilization
    est_cost = est_in_tokens / 1e6 * BATCH_PRICE["in"] + est_out_tokens / 1e6 * BATCH_PRICE["out"]

    print(f"[create] wrote {len(requests_list)} requests to {P['requests']}")
    print(f"[create] lookup written to {P['lookup']}")
    print(f"[create] customers without matched calls: {skipped_no_calls} "
          f"({'sent anyway' if INCLUDE_NO_CALL_CUSTOMERS else 'skipped, not billed'})")
    print(f"[create] est. cost ~${est_cost:.2f}  "
          f"(input~{est_in_tokens:,.0f} tok / output~{est_out_tokens:,.0f} tok @ batch pricing)")
    return 0


# ------------------------------------------------------------------ batch: submit

def _client():
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(2)
    import anthropic
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def cmd_submit(run_id: str) -> int:
    P = paths(run_id)
    if not P["requests"].exists():
        print(f"ERROR: {P['requests']} not found. Run create first.", file=sys.stderr)
        return 2
    client = _client()
    requests_list = load_jsonl(P["requests"])
    print(f"[submit] loaded {len(requests_list)} requests")
    batch = client.messages.batches.create(requests=requests_list)
    info = {
        "batch_id": batch.id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": batch.processing_status,
        "purpose": "win_loss_rewind_phase4_themes",
        "request_counts": {
            "processing": batch.request_counts.processing,
            "succeeded": batch.request_counts.succeeded,
            "errored": batch.request_counts.errored,
        },
    }
    with P["info"].open("w") as f:
        json.dump(info, f, indent=2)
    print(f"[submit] batch {batch.id}  status={batch.processing_status}")
    print(f"[submit] info -> {P['info']}")
    return 0


# ------------------------------------------------------------------ batch: status

def cmd_status(run_id: str) -> int:
    P = paths(run_id)
    if not P["info"].exists():
        print(f"ERROR: {P['info']} not found. Run submit first.", file=sys.stderr)
        return 2
    info = json.loads(P["info"].read_text())
    client = _client()
    batch = client.messages.batches.retrieve(info["batch_id"])
    succ = batch.request_counts.succeeded
    proc = batch.request_counts.processing
    err = batch.request_counts.errored
    total = succ + proc + err
    print(f"[status] batch {batch.id}")
    print(f"  processing_status: {batch.processing_status}")
    print(f"  progress: {succ}/{total}  errored={err}")
    if batch.processing_status == "ended":
        print("  ready — run download next")
    return 0


# ------------------------------------------------------------------ batch: download

def _get(obj, key, default=None):
    """Attribute or dict access, so SDK objects and saved JSON read the same way."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def message_text(message) -> str:
    """Join the text of every `text` block. Thinking blocks (and any other block
    type) are skipped, so a response that opens with a thinking block still
    yields its answer."""
    parts = []
    for block in _get(message, "content", None) or []:
        if _get(block, "type", "text") == "text":   # saved rows may omit "type"
            parts.append(_get(block, "text", "") or "")
    return "\n".join(parts).strip()


def result_row(result) -> dict:
    """One saved batch result: status "ok" with the answer text, or "failed"
    with the reason (errored / expired / canceled request, or no text block)."""
    cid = _get(result, "custom_id")
    res = _get(result, "result")
    rtype = _get(res, "type", "succeeded")
    message = _get(res, "message")
    if rtype != "succeeded" or message is None:
        err = _get(res, "error")
        return {"custom_id": cid, "status": "failed",
                "error": f"batch_{rtype}: {err}" if err else f"batch_{rtype}",
                "result": {"message": {"content": [{"type": "text", "text": ""}]}}}
    text = message_text(message)
    row = {"custom_id": cid, "status": "ok" if text else "failed",
           "result": {"message": {"content": [{"type": "text", "text": text}]}}}
    if not text:
        row["error"] = "no_text_block: the response had no text to parse"
    return row


def cmd_download(run_id: str) -> int:
    P = paths(run_id)
    if not P["info"].exists():
        print(f"ERROR: {P['info']} not found. Run submit first.", file=sys.stderr)
        return 2
    info = json.loads(P["info"].read_text())
    client = _client()
    batch = client.messages.batches.retrieve(info["batch_id"])
    if batch.processing_status != "ended":
        print(f"[download] batch not ended (status={batch.processing_status}). "
              f"Wait or run status.", file=sys.stderr)
        return 1
    n = n_failed = 0
    with P["results"].open("w") as f:
        for result in client.messages.batches.results(info["batch_id"]):
            row = result_row(result)
            n_failed += row["status"] != "ok"
            f.write(json.dumps(row) + "\n")
            n += 1
    print(f"[download] wrote {n} results to {P['results']} ({n_failed} failed)")
    if n_failed:
        print(f"[download] {n_failed} result(s) failed or carried no text; parse will "
              "list them and stop the run.", file=sys.stderr)
    return 0


# ------------------------------------------------------------------ batch: parse

def _parse_json_response(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return json.loads(text.strip())


def cmd_parse(run_id: str) -> int:
    P = paths(run_id)
    if not P["lookup"].exists() or not P["results"].exists():
        print("ERROR: missing lookup or results. Run create+submit+download first.",
              file=sys.stderr)
        return 2
    lookup = json.loads(P["lookup"].read_text())

    n_total = 0
    n_parsed = 0
    n_errors = 0
    n_failed = 0
    confidences: list[float] = []

    with P["themes"].open("w") as fout, P["errors"].open("w") as ferr:
        for line in P["results"].open():
            line = line.strip()
            if not line:
                continue
            n_total += 1
            row = json.loads(line)
            cid = row.get("custom_id")
            meta = lookup.get(cid, {})
            text = message_text(_get(row.get("result"), "message"))
            if row.get("status") == "failed" or not text:
                n_failed += 1
                ferr.write(json.dumps({
                    "custom_id": cid, "meta": meta,
                    "error": row.get("error") or "no_text_block",
                    "raw_text": "",
                }) + "\n")
                continue
            try:
                parsed = _parse_json_response(text)
                if not isinstance(parsed, dict):
                    raise ValueError(f"non-dict response: {type(parsed).__name__}")
                merged = {**meta, **parsed, "_custom_id": cid}
                # Themes built on unconfirmed grades are not verified evidence.
                n_prov = int(meta.get("customer_lines_provisional") or 0)
                merged["grades_provisional"] = n_prov > 0
                merged["requires_confirmation"] = n_prov > 0
                merged["unprompted_evidence_verified"] = (
                    n_prov == 0 and int(meta.get("customer_lines_confirmed") or 0) > 0)
                fout.write(json.dumps(merged) + "\n")
                n_parsed += 1
                conf = parsed.get("extraction_confidence")
                if isinstance(conf, (int, float)):
                    confidences.append(float(conf))
            except Exception as e:
                n_errors += 1
                ferr.write(json.dumps({
                    "custom_id": cid,
                    "meta": meta,
                    "error": str(e),
                    "raw_text": text[:2000],
                }) + "\n")

    mean_conf = (sum(confidences) / len(confidences)) if confidences else None

    print(f"[parse] total: {n_total}")
    print(f"[parse] parsed ok: {n_parsed}")
    print(f"[parse] parse errors: {n_errors}")
    print(f"[parse] mean extraction_confidence: "
          f"{mean_conf:.3f}" if mean_conf is not None else "[parse] no confidence values")
    print(f"[parse] -> {P['themes']}")
    print(f"[parse] failed / no text: {n_failed}")
    if n_errors or n_failed:
        print(f"[parse] errors -> {P['errors']}")
    if n_failed or n_errors or n_parsed == 0:
        print(f"[parse] STOP: {n_failed + n_errors} of {n_total} customers have no usable "
              "themes. Phase 5 must not run on an incomplete theme set. Re-submit the "
              f"failed custom_ids listed in {P['errors']}, then re-run download + parse.",
              file=sys.stderr)
        return 1
    return 0


# ------------------------------------------------------------------ batch: run-all

def cmd_run_all(run_id: str, poll_seconds: int = 30) -> int:
    rc = cmd_create(run_id)
    if rc != 0:
        return rc
    rc = cmd_submit(run_id)
    if rc != 0:
        return rc
    P = paths(run_id)
    info = json.loads(P["info"].read_text())
    client = _client()
    print(f"[run-all] polling batch {info['batch_id']} every {poll_seconds}s ...")
    while True:
        batch = client.messages.batches.retrieve(info["batch_id"])
        succ = batch.request_counts.succeeded
        proc = batch.request_counts.processing
        err = batch.request_counts.errored
        total = succ + proc + err
        print(f"  status={batch.processing_status} progress={succ}/{total} err={err}")
        if batch.processing_status == "ended":
            break
        time.sleep(poll_seconds)
    rc = cmd_download(run_id)
    if rc != 0:
        return rc
    return cmd_parse(run_id)


# ------------------------------------------------------------------ CLI

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 4 — extract per-customer themes via Anthropic Batch API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("create", "submit", "status", "download", "parse"):
        sp = sub.add_parser(name, help=f"{name} batch")
        sp.add_argument("--run-id", required=True)
    sp = sub.add_parser("run-all", help="create + submit + poll + download + parse")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--poll-seconds", type=int, default=30)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "create":
        return cmd_create(args.run_id)
    if args.command == "submit":
        return cmd_submit(args.run_id)
    if args.command == "status":
        return cmd_status(args.run_id)
    if args.command == "download":
        return cmd_download(args.run_id)
    if args.command == "parse":
        return cmd_parse(args.run_id)
    if args.command == "run-all":
        return cmd_run_all(args.run_id, poll_seconds=args.poll_seconds)
    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
