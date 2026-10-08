#!/usr/bin/env python3
"""
Top-level driver for `/win-loss-rewind run` — walks Phases 1–7 in order.

Phase 0 (`init`) must already have run; this script halts if
`outputs/<run-id>/data_confidence.md` is missing.

Usage:
    python orchestrate.py --run-id <id>
    python orchestrate.py --run-id <id> --resume-from 4
    python orchestrate.py --run-id <id> --rerun-phase 3

Phase map:
    1 → pull_gong → match_gong → opus_audit_matches (run-all) → label_speakers (Phase 1.4)
    2 → enrich_panel
    3 → segment
    4 → batch_themes (run-all)
    5 → generate_hypotheses
    6 → backcast_validate
    7 → compose_discriminator
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"


# Phase definition: (phase_number, label, [cmd_steps])
# Each step is a list of CLI args passed to subprocess (script paths are absolute).
def phase_steps(phase: int, run_id: str) -> list[list[str]]:
    py = sys.executable
    sd = SCRIPTS_DIR

    if phase == 1:
        return [
            [py, str(sd / "pull_gong.py"), "--run-id", run_id],
            [py, str(sd / "match_gong.py"), "--run-id", run_id],
            [py, str(sd / "opus_audit_matches.py"), "run-all", "--run-id", run_id],
            # Phase 1.4 — speaker attribution (free; uses Gong party metadata).
            [py, str(sd / "label_speakers.py"), "--run-id", run_id],
        ]
    if phase == 2:
        return [[py, str(sd / "enrich_panel.py"), "--run-id", run_id]]
    if phase == 3:
        return [[py, str(sd / "segment.py"), "--run-id", run_id]]
    if phase == 4:
        return [[py, str(sd / "batch_themes.py"), "run-all", "--run-id", run_id]]
    if phase == 5:
        return [[py, str(sd / "generate_hypotheses.py"), "--run-id", run_id]]
    if phase == 6:
        return [[py, str(sd / "backcast_validate.py"), "--run-id", run_id]]
    if phase == 7:
        return [[py, str(sd / "compose_discriminator.py"), "--run-id", run_id]]
    raise ValueError(f"Unknown phase: {phase}")


# Artifacts to delete when --rerun-phase N is set (downstream of N, inclusive of N).
PHASE_ARTIFACTS: dict[int, list[str]] = {
    1: ["matched_calls.jsonl", "audit_report.md", "proposed_matches.jsonl",
        "gong_calls_raw.jsonl", "calls_with_speaker_attribution.jsonl"],
    2: ["customer_panels.jsonl"],
    3: ["archetypes.md", "archetypes.jsonl"],
    4: ["themes.jsonl"],
    5: ["hypotheses.jsonl", "hypotheses-rejected.jsonl"],
    6: ["validated_signals.md", "validated_signals.jsonl"],
    7: ["discriminator.md", "discriminator.jsonl", "cost_summary.json"],
}


def delete_phase_artifacts(run_dir: Path, phase: int) -> None:
    for p in range(phase, 8):
        for name in PHASE_ARTIFACTS.get(p, []):
            f = run_dir / name
            if f.exists():
                f.unlink()
                print(f"[rerun] deleted {f}", file=sys.stderr)


def run_phase(phase: int, run_id: str) -> tuple[bool, float]:
    print(f"\n────────────  PHASE {phase}  ────────────", file=sys.stderr)
    started = time.time()
    for step in phase_steps(phase, run_id):
        print(f"[orchestrate] $ {' '.join(step)}", file=sys.stderr)
        result = subprocess.run(step)
        if result.returncode != 0:
            print(f"[orchestrate] phase {phase} FAILED at step "
                  f"`{Path(step[1]).name}` (exit={result.returncode})",
                  file=sys.stderr)
            return False, time.time() - started
    return True, time.time() - started


APPROVAL_NAME = "archetypes.approved.json"


def archetypes_sha(run_dir: Path) -> str | None:
    import hashlib  # noqa: PLC0415
    f = run_dir / "archetypes.jsonl"
    return hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None


def approve_archetypes(run_dir: Path) -> Path:
    """Record that a human approved archetypes.jsonl exactly as it is now."""
    sha = archetypes_sha(run_dir)
    if sha is None:
        raise SystemExit(f"Error: {run_dir / 'archetypes.jsonl'} not found; run phase 3 first.")
    out = run_dir / APPROVAL_NAME
    out.write_text(json.dumps({"archetypes_sha256": sha,
                               "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
    return out


def archetypes_approved(run_dir: Path) -> bool:
    """True only if the approval matches archetypes.jsonl byte for byte (an edit needs re-approval)."""
    f = run_dir / APPROVAL_NAME
    if not f.exists():
        return False
    try:
        return json.loads(f.read_text()).get("archetypes_sha256") == archetypes_sha(run_dir)
    except (OSError, json.JSONDecodeError):
        return False


def main(argv=None, runner=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--resume-from", type=int, default=1, choices=range(1, 8),
                    help="Phase to start at (default 1)")
    ap.add_argument("--rerun-phase", type=int, default=None, choices=range(1, 8),
                    help="Delete artifacts for phase N onward and re-enter at N")
    ap.add_argument("--stop-after", type=int, default=7, choices=range(1, 8),
                    help="Stop after phase N (use 3 to review archetypes before themes)")
    ap.add_argument("--approve-archetypes", action="store_true",
                    help="Record that you reviewed archetypes.jsonl as it is now; "
                         "phases 4-7 refuse to run without this")
    args = ap.parse_args(argv)
    runner = runner or run_phase

    run_dir = OUTPUTS_ROOT / args.run_id
    if not run_dir.exists():
        raise SystemExit(
            f"Error: {run_dir} does not exist. Run `/win-loss-rewind init <csv>` first."
        )

    data_confidence = run_dir / "data_confidence.md"
    if not data_confidence.exists():
        raise SystemExit(
            f"Error: {data_confidence} not found. "
            "Phase 0 must complete before Phases 1–7. Run `init <csv>` first."
        )

    if args.approve_archetypes:
        print(f"[orchestrate] approved archetypes -> {approve_archetypes(run_dir)}", file=sys.stderr)

    if args.rerun_phase is not None:
        delete_phase_artifacts(run_dir, args.rerun_phase)
        start_phase = args.rerun_phase
    else:
        start_phase = args.resume_from

    print(f"[orchestrate] run_id={args.run_id} start_phase={start_phase}", file=sys.stderr)

    summary: list[dict] = []
    overall_started = time.time()

    for phase in range(start_phase, args.stop_after + 1):
        if phase >= 4 and not archetypes_approved(run_dir):
            print("\n[orchestrate] STOP: archetypes are not approved (or changed since "
                  "approval).\n  Read archetypes.md. To merge or drop archetypes, edit "
                  "archetypes.jsonl: move member records between archetypes, or delete an "
                  "archetype (its members are then left out). Keep each member's holdout "
                  "flag as it is.\n  Then re-run with --approve-archetypes "
                  f"--resume-from {phase}.", file=sys.stderr)
            _print_summary(summary, overall_started, run_dir, halted_at=phase)
            return 3
        ok, elapsed = runner(phase, args.run_id)
        summary.append({"phase": phase, "ok": ok, "elapsed_seconds": round(elapsed, 1)})
        if not ok:
            _print_summary(summary, overall_started, run_dir, halted_at=phase)
            return 1

    _print_summary(summary, overall_started, run_dir, halted_at=None)
    if args.stop_after < 7:
        print(f"Stopped after phase {args.stop_after} as asked.", file=sys.stderr)
    return 0


def _print_summary(summary: list[dict], overall_started: float,
                   run_dir: Path, halted_at: int | None) -> None:
    print("\n=== Orchestrate summary ===", file=sys.stderr)
    for row in summary:
        status = "✓" if row["ok"] else "✗"
        print(f"  Phase {row['phase']}: {status}  ({row['elapsed_seconds']}s)",
              file=sys.stderr)
    if halted_at is not None:
        print(f"\nHALTED at phase {halted_at}.", file=sys.stderr)
    else:
        print("\nAll phases completed.", file=sys.stderr)
    print(f"Total elapsed: {round(time.time() - overall_started, 1)}s", file=sys.stderr)

    cost_file = run_dir / "cost_summary.json"
    if cost_file.exists():
        try:
            data = json.loads(cost_file.read_text())
            tu = data.get("total_usage", {})
            print(f"Total tokens — in: {tu.get('input_tokens', 0)} "
                  f"out: {tu.get('output_tokens', 0)}", file=sys.stderr)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
