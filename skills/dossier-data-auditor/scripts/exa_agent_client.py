#!/usr/bin/env python3
"""
exa_agent_client.py — Thin client for Exa Agent (beta). Pins the
`Exa-Beta: agent-2026-05-07` header per provider-routing.yaml.
Mirrors Parallel.ai Task Group interface so the W6 sanity-agent can
swap providers without changing call shape.

Used for:
  - W6 ICP cohort sanity (secondary when Parallel returns <0.6 confidence)
  - W6 per-record paradox resolution (secondary on disputed rows)
  - W2c last-resort multi-domain consolidation (when Blitz + OWN both fail)

Usage:
    from exa_agent_client import enrich_rows
    results = enrich_rows(rows, output_schema, system_prompt)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "_shared"))
import project_env  # noqa: E402  (loads <project root>/.env; real env wins)
project_env.load()
import time
from typing import Any

import requests

REQUIRED_BETA_HEADER = "agent-2026-05-07"


class ExaBetaVersionError(RuntimeError):
    """Raised when the pinned beta version is unavailable.
    Auto-upgrade is forbidden — surface loudly per provider-routing.yaml."""


def get_client_headers(api_key: str | None = None) -> dict[str, str]:
    key = api_key or os.environ.get("EXA_API_KEY")
    if not key:
        raise RuntimeError("EXA_API_KEY not set in environment")
    return {
        "x-api-key": key,
        "Exa-Beta": REQUIRED_BETA_HEADER,
        "Content-Type": "application/json",
    }


def enrich_rows(
    rows: list[dict[str, Any]],
    output_schema: dict[str, Any],
    system_prompt: str,
    *,
    effort_cap_usd: float = 25.0,
    api_key: str | None = None,
    poll_interval_s: float = 5.0,
    timeout_s: float = 600.0,
) -> list[dict[str, Any]]:
    """Submit a batch of rows to Exa Agent. Block until results or timeout.

    Each input row becomes one Agent run with the same output_schema and
    system_prompt. Returns per-row {output, grounding, error}.
    """
    headers = get_client_headers(api_key)

    # Submit batch
    submit_payload = {
        "input": {"data": rows},
        "outputSchema": output_schema,
        "systemPrompt": system_prompt,
        "effort": {"maxUsd": effort_cap_usd},
    }
    try:
        r = requests.post(
            "https://api.exa.ai/agent",
            headers=headers,
            json=submit_payload,
            timeout=30,
        )
    except Exception as e:
        raise RuntimeError(f"Exa Agent submit failed: {e}") from e

    if r.status_code == 410 or "agent-2026-05-07" in r.text.lower() and "deprecated" in r.text.lower():
        raise ExaBetaVersionError(
            f"Exa rolled the agent-2026-05-07 beta forward. Pin must be updated explicitly. "
            f"Response: {r.text[:500]}"
        )
    if r.status_code != 200:
        raise RuntimeError(f"Exa Agent submit returned {r.status_code}: {r.text[:500]}")

    submit_resp = r.json()
    agent_id = submit_resp.get("id") or submit_resp.get("agent_id")
    if not agent_id:
        raise RuntimeError(f"Exa Agent did not return an id: {submit_resp}")

    # Poll until complete
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            r = requests.get(
                f"https://api.exa.ai/agent/{agent_id}",
                headers=headers,
                timeout=20,
            )
        except Exception:
            time.sleep(poll_interval_s)
            continue
        if r.status_code != 200:
            time.sleep(poll_interval_s)
            continue
        data = r.json()
        status = data.get("status")
        if status in ("completed", "failed"):
            results = data.get("results") or data.get("output") or []
            return [{"row_index": i, "output": item.get("output"),
                      "grounding": item.get("grounding"),
                      "error": item.get("error")}
                     for i, item in enumerate(results)]
        time.sleep(poll_interval_s)

    raise TimeoutError(f"Exa Agent did not complete within {timeout_s}s for agent_id={agent_id}")


def main():
    """CLI smoke-test: enrich a single row with a trivial schema."""
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke":
        try:
            results = enrich_rows(
                rows=[{"company_name": "Stripe", "domain": "stripe.com"}],
                output_schema={
                    "type": "object",
                    "properties": {
                        "primary_business_line": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["primary_business_line", "confidence"],
                },
                system_prompt="Identify the primary business line of the company. Return a one-line description and confidence 0-1.",
                effort_cap_usd=1.0,
            )
            print(json.dumps(results, indent=2))
        except Exception as e:
            print(f"SMOKE TEST FAILED: {e}", file=sys.stderr)
            sys.exit(2)


if __name__ == "__main__":
    main()
