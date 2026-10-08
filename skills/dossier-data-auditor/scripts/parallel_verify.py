#!/usr/bin/env python3
"""Parallel.ai Task Group wrapper — async batch web research with citations.

Used at W6 for:
  1. ICP-cohort sanity sample (200 rows at Base tier, ~$8 total)
  2. Per-record paradox resolution (Task Core at $0.05/row, capped at $15)

Replaces the legacy web_sanity_sample.py (Serper+Exa+Opus-judge) for new audits.
The legacy script remains in dossier-builder for backward compatibility.

Async by design — Task Groups take 3-8 minutes for 200 rows. The skill polls
status and resumes when complete. Users can --skip-and-resume to suspend the
audit and pick up results in a later session.

Usage:
    # Create a task group
    python parallel_verify.py create --tier base --rows rows.jsonl --schema schema.json
    # Poll an existing task group
    python parallel_verify.py status --task-group-id tgrp_xxx
    # Fetch results
    python parallel_verify.py results --task-group-id tgrp_xxx
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "_shared"))
import project_env  # noqa: E402  (loads <project root>/.env; real env wins)
project_env.load()
import time

import requests

BASE_URL = "https://api.parallel.ai/v1beta"


def _headers() -> dict:
    key = os.environ.get("PARALLEL_API_KEY")
    if not key:
        raise RuntimeError("PARALLEL_API_KEY not set. See ../references/tool-equipping.md.")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def create_task_group(
    rows: list[dict],
    output_schema: dict,
    processor: str = "base",
    timeout_s: int = 60,
) -> dict:
    """Submit a batch of N rows as a Task Group.

    Each row should be a dict with at least an `input` field describing the task.
    output_schema is the JSON Schema the task should conform to.

    Returns {task_group_id, status, n_rows, estimated_cost_usd}.
    """
    payload = {
        "processor": processor,
        "output_schema": output_schema,
        "tasks": [
            {"input": row["input"], "metadata": row.get("metadata", {})}
            for row in rows
        ],
    }
    r = requests.post(
        f"{BASE_URL}/task_groups", headers=_headers(), json=payload, timeout=timeout_s,
    )
    r.raise_for_status()
    data = r.json()
    cost_per_row = {"base": 0.04, "core": 0.10, "pro": 0.30, "ultra": 0.60}.get(processor, 0.04)
    return {
        "task_group_id": data.get("id"),
        "status": data.get("status", "queued"),
        "n_rows": len(rows),
        "estimated_cost_usd": round(len(rows) * cost_per_row, 2),
    }


def get_status(task_group_id: str, timeout_s: int = 30) -> dict:
    """Poll a Task Group's status. Returns {status, n_complete, n_total, errors}."""
    r = requests.get(
        f"{BASE_URL}/task_groups/{task_group_id}", headers=_headers(), timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()


def get_results(task_group_id: str, timeout_s: int = 60) -> list[dict]:
    """Fetch all task results for a Task Group. Streams via SSE in production;
    this convenience wrapper fetches the final aggregated list.
    """
    r = requests.get(
        f"{BASE_URL}/task_groups/{task_group_id}/runs",
        headers=_headers(),
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json().get("runs", [])


def poll_until_complete(
    task_group_id: str,
    max_wait_s: int = 600,
    poll_interval_s: int = 10,
) -> dict:
    """Block until the Task Group finishes or max_wait_s elapses."""
    start = time.time()
    while time.time() - start < max_wait_s:
        status = get_status(task_group_id)
        if status.get("status") in {"completed", "failed", "cancelled"}:
            return status
        n_done = status.get("n_complete", 0)
        n_total = status.get("n_total", 1)
        print(f"[parallel_verify] {n_done}/{n_total} complete; polling again in {poll_interval_s}s",
              file=sys.stderr)
        time.sleep(poll_interval_s)
    print(f"[parallel_verify] timed out after {max_wait_s}s", file=sys.stderr)
    return get_status(task_group_id)


# ─────── ICP cohort sanity helper ───────

ICP_SCHEMA = {
    "type": "object",
    "properties": {
        "in_icp": {"type": "boolean", "description": "Is this account in the stated ICP?"},
        "evidence_url": {"type": "string", "description": "URL where evidence was found"},
        "evidence_text": {"type": "string", "description": "Brief quote from the evidence"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["in_icp", "confidence"],
}


def icp_cohort_sample(rows: list[dict], expected_icp: str, processor: str = "base") -> dict:
    """Build + submit an ICP-verification Task Group.

    rows: [{account_id, domain, name}, ...]
    expected_icp: string description, e.g., "FQHC clinic"
    """
    tasks = [
        {
            "input": (
                f"Is {r['name']} (domain: {r['domain']}) a {expected_icp}? "
                "Answer with structured JSON: in_icp (boolean), evidence_url (string), "
                "evidence_text (short quote), confidence (0-1 float)."
            ),
            "metadata": {"account_id": r["account_id"]},
        }
        for r in rows
    ]
    return create_task_group(tasks, ICP_SCHEMA, processor=processor)


# ─────── CLI ───────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create")
    pc.add_argument("--tier", choices=["base", "core", "pro", "ultra"], default="base")
    pc.add_argument("--rows", required=True, help="path to JSONL file of {input, metadata}")
    pc.add_argument("--schema", required=True, help="path to JSON schema file")

    ps = sub.add_parser("status")
    ps.add_argument("--task-group-id", required=True)

    pr = sub.add_parser("results")
    pr.add_argument("--task-group-id", required=True)

    pp = sub.add_parser("poll-until-done")
    pp.add_argument("--task-group-id", required=True)
    pp.add_argument("--max-wait", type=int, default=600)

    args = ap.parse_args()

    try:
        if args.cmd == "create":
            with open(args.rows) as f:
                rows = [json.loads(line) for line in f if line.strip()]
            with open(args.schema) as f:
                schema = json.load(f)
            result = create_task_group(rows, schema, processor=args.tier)
        elif args.cmd == "status":
            result = get_status(args.task_group_id)
        elif args.cmd == "results":
            result = get_results(args.task_group_id)
        elif args.cmd == "poll-until-done":
            result = poll_until_complete(args.task_group_id, max_wait_s=args.max_wait)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
