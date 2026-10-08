#!/usr/bin/env python3
"""Perplexity Sonar / Sonar Pro — second-opinion verifier with citations.

Used in W6 when Parallel Task Group returns a row with confidence < 0.6.
Sonar Pro performs multi-hop research and returns cited prose; the prose IS
the audit-trail artifact.

Sonar: $0.006/q. Sonar Pro: $0.018/q. Pro recommended for paradox-resolution use cases.

Usage as library:
    from perplexity_second_opinion import ask_sonar_pro

    result = ask_sonar_pro(
        question="Is acme.example a roofing contractor in Denver?",
    )
    # result = {answer: "...", citations: ["url1", "url2", ...], model: "sonar-pro", cost_usd: 0.018}
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

import requests

ENDPOINT = "https://api.perplexity.ai/chat/completions"

MODEL_SONAR = "llama-3.1-sonar-small-128k-online"
MODEL_SONAR_PRO = "llama-3.1-sonar-large-128k-online"


def ask(question: str, model: str = MODEL_SONAR_PRO, timeout_s: int = 30) -> dict:
    """Ask Perplexity. Returns {answer, citations, model, cost_usd}."""
    key = os.environ.get("PERPLEXITY_API_KEY")
    if not key:
        raise RuntimeError("PERPLEXITY_API_KEY not set. See ../references/tool-equipping.md.")

    r = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "Answer briefly. Cite sources."},
                {"role": "user", "content": question},
            ],
            "return_citations": True,
            "temperature": 0,
        },
        timeout=timeout_s,
    )
    r.raise_for_status()
    data = r.json()
    answer = data["choices"][0]["message"]["content"]
    citations = data.get("citations", [])
    cost = 0.018 if model == MODEL_SONAR_PRO else 0.006
    return {
        "answer": answer,
        "citations": citations,
        "model": model,
        "cost_usd": cost,
    }


def ask_sonar(question: str) -> dict:
    """Cheap one-hop Sonar ($0.006)."""
    return ask(question, model=MODEL_SONAR)


def ask_sonar_pro(question: str) -> dict:
    """Multi-hop Sonar Pro ($0.018) — default for second opinions."""
    return ask(question, model=MODEL_SONAR_PRO)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--question", required=True)
    ap.add_argument("--model", default="pro", choices=["sonar", "pro"])
    args = ap.parse_args()

    fn = ask_sonar if args.model == "sonar" else ask_sonar_pro
    try:
        result = fn(args.question)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
