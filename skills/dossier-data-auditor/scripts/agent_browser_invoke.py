#!/usr/bin/env python3
"""Shell-out wrapper for the Vercel agent-browser CLI.

agent-browser is a Rust CLI that drives Playwright with extreme token efficiency
(1,400 vs Playwright-MCP's 7,800 tokens per 6-step sequence). Apache-2.0.

Install once: npm install -g @vercel-labs/agent-browser

Used as a JS-page fallback when Firecrawl misses content. The skill prefers
Firecrawl first; falls back here only when needed.

Usage:
    python agent_browser_invoke.py --url https://acme.example --task "extract company name and employee count"
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys


def check_installed() -> bool:
    return shutil.which("agent-browser") is not None


def run_task(url: str, task: str, timeout_s: int = 90) -> dict:
    """Invoke agent-browser with a natural-language task.

    Returns {success, output, stderr, exit_code, elapsed_s}.
    """
    if not check_installed():
        return {
            "success": False,
            "output": "",
            "stderr": "agent-browser CLI not installed. Run: npm install -g @vercel-labs/agent-browser",
            "exit_code": -1,
        }

    # The actual agent-browser CLI accepts a flag pattern like:
    #     agent-browser visit <url> --task "<natural-language>"
    # Verify against the README for current syntax; this is the documented shape.
    cmd = ["agent-browser", "visit", url, "--task", task, "--format", "json"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
        return {
            "success": proc.returncode == 0,
            "output": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "stderr": f"timeout after {timeout_s}s",
            "exit_code": -2,
        }
    except FileNotFoundError as e:
        return {
            "success": False,
            "output": "",
            "stderr": f"agent-browser binary not found: {e}",
            "exit_code": -3,
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    result = run_task(args.url, args.task, timeout_s=args.timeout)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
