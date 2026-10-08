#!/usr/bin/env python3
"""
Phase 1.1 — Pull Gong calls + transcripts in a date range.

Notes:
  - Writes JSONL (one call per line) so the match step can stream.
  - Output path is run-id scoped: outputs/<run-id>/gong_calls_raw.jsonl
  - Optional --from / --to date overrides; --days fallback (default 540 = ~18mo)

Each line in the output JSONL is one call:
  {
    "call_id": str,
    "gong_url": str,
    "title": str,
    "started": ISO8601 str,
    "duration_seconds": int,
    "direction": str,
    "parties": [...],
    "transcript": [...]   # empty list if --no-transcripts
  }

Usage:
    python pull_gong.py --run-id <id>
    python pull_gong.py --run-id <id> --days 180
    python pull_gong.py --run-id <id> --from 2024-01-01 --to 2024-12-31
    python pull_gong.py --run-id <id> --no-transcripts

Env (read via python-dotenv from .env):
    GONG_BASE_URL          # REQUIRED: your Gong API host, e.g. https://us-XXXXX.api.gong.io
    GONG_ACCESS_KEY
    GONG_SECRET_KEY
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

#: Your own Gong API host (each Gong account has its own). No default on purpose.
GONG_BASE_URL = os.getenv("GONG_BASE_URL", "").rstrip("/")
GONG_ACCESS_KEY = os.getenv("GONG_ACCESS_KEY")
GONG_SECRET_KEY = os.getenv("GONG_SECRET_KEY")

# Output root: scripts/ -> ../outputs/
SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"


def get_gong_headers() -> dict:
    """Get Gong API headers with Basic auth."""
    if not GONG_ACCESS_KEY or not GONG_SECRET_KEY:
        raise SystemExit(
            "Error: GONG_ACCESS_KEY and GONG_SECRET_KEY must be set in env / .env"
        )
    auth = base64.b64encode(f"{GONG_ACCESS_KEY}:{GONG_SECRET_KEY}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }


def list_calls(from_date: str, to_date: str, cursor: str | None = None) -> dict:
    """List calls within a date range. Returns paginated response."""
    url = f"{GONG_BASE_URL}/v2/calls"
    params = {
        "fromDateTime": from_date,
        "toDateTime": to_date,
    }
    if cursor:
        params["cursor"] = cursor

    response = requests.get(url, headers=get_gong_headers(), params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def get_transcripts(call_ids: list[str]) -> dict:
    """Fetch transcripts for up to 100 call IDs at once."""
    url = f"{GONG_BASE_URL}/v2/calls/transcript"
    response = requests.post(
        url,
        headers=get_gong_headers(),
        json={"filter": {"callIds": call_ids}},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def pull_all_calls(from_str: str, to_str: str) -> list[dict]:
    """Pull all calls between from_str and to_str (ISO8601 UTC)."""
    print(f"Pulling Gong calls from {from_str[:10]} to {to_str[:10]}", flush=True)

    all_calls: list[dict] = []
    cursor: str | None = None
    page = 1

    while True:
        print(f"  Fetching page {page}...", flush=True)
        response = list_calls(from_str, to_str, cursor)

        calls = response.get("calls", [])
        all_calls.extend(calls)
        print(f"  Got {len(calls)} calls (total: {len(all_calls)})", flush=True)

        records = response.get("records", {})
        cursor = records.get("cursor")
        if not cursor:
            break
        page += 1

    return all_calls


def pull_transcripts(calls: list[dict]) -> dict:
    """Pull transcripts for all calls in batches of 100."""
    call_ids = [c["id"] for c in calls]
    transcripts_by_id: dict = {}

    print(f"\nPulling transcripts for {len(call_ids)} calls...", flush=True)

    for i in range(0, len(call_ids), 100):
        batch = call_ids[i : i + 100]
        batch_num = i // 100 + 1
        total_batches = (len(call_ids) + 99) // 100
        print(
            f"  Batch {batch_num}/{total_batches}: {len(batch)} calls...",
            flush=True,
        )
        try:
            result = get_transcripts(batch)
            for t in result.get("callTranscripts", []):
                transcripts_by_id[t["callId"]] = t.get("transcript", [])
            print(
                f"    Got {len(result.get('callTranscripts', []))} transcripts",
                flush=True,
            )
        except Exception as e:
            print(f"    Error: {e}", flush=True)

    return transcripts_by_id


def resolve_dates(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve --from / --to / --days into ISO8601 UTC bounds."""
    if args.from_date and args.to_date:
        from_dt = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
    elif args.from_date and not args.to_date:
        from_dt = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        to_dt = datetime.now(timezone.utc)
    else:
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=args.days)
    return (
        from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 1.1 — pull Gong calls + transcripts to outputs/<run-id>/gong_calls_raw.jsonl"
    )
    parser.add_argument("--run-id", required=True, help="Run identifier; sets outputs/<run-id>/")
    parser.add_argument(
        "--days",
        type=int,
        default=540,
        help="Pull calls from the last N days (default: 540, ~18 months)",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="From date (YYYY-MM-DD), overrides --days",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        default=None,
        help="To date (YYYY-MM-DD), defaults to now if --from is set",
    )
    parser.add_argument(
        "--no-transcripts",
        action="store_true",
        help="Skip transcript pull (useful for fast roster preview)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not GONG_ACCESS_KEY or not GONG_SECRET_KEY or not GONG_BASE_URL:
        print(
            "Error: GONG_ACCESS_KEY, GONG_SECRET_KEY and GONG_BASE_URL must be set in env / .env "
            "(GONG_BASE_URL is your own Gong API host, e.g. https://us-XXXXX.api.gong.io)",
            flush=True,
        )
        sys.exit(1)

    from_str, to_str = resolve_dates(args)

    run_dir = OUTPUTS_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output_file = run_dir / "gong_calls_raw.jsonl"

    calls = pull_all_calls(from_str, to_str)
    print(f"\nTotal calls retrieved: {len(calls)}", flush=True)

    if not calls:
        print("No calls found.", flush=True)
        # Still write an empty file so downstream phases can detect "ran but empty"
        output_file.write_text("")
        return

    if args.no_transcripts:
        print("Skipping transcript pull (--no-transcripts)", flush=True)
        transcripts = {}
    else:
        transcripts = pull_transcripts(calls)
        print(f"Got transcripts for {len(transcripts)} calls", flush=True)

    # Stream-write JSONL (one call per line)
    written = 0
    with open(output_file, "w") as f:
        for call in calls:
            call_id = call["id"]
            row = {
                "call_id": call_id,
                "gong_url": f"https://app.gong.io/call?id={call_id}",
                "title": call.get("title", ""),
                "started": call.get("started", ""),
                "duration_seconds": call.get("duration", 0),
                "direction": call.get("direction", ""),
                "parties": call.get("parties", []),
                "transcript": transcripts.get(call_id, []),
            }
            f.write(json.dumps(row, default=str) + "\n")
            written += 1

    print(f"\nSaved {written} calls to: {output_file}", flush=True)

    with_transcript = sum(1 for c in calls if transcripts.get(c["id"]))
    print(f"  - Calls with transcripts: {with_transcript}", flush=True)
    if calls:
        starts = [c.get("started", "") for c in calls if c.get("started")]
        if starts:
            print(
                f"  - Date range: {min(starts)[:10]} to {max(starts)[:10]}",
                flush=True,
            )


if __name__ == "__main__":
    main()
