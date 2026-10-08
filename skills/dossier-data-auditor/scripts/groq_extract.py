#!/usr/bin/env python3
"""Groq Llama wrapper — cheap structured-field extraction.

OpenAI-compatible endpoint. Use for parsing structured JSON out of unstructured
text (e.g., scraped markdown -> {company_name, address, employees, revenue}).

At ~$0.00005/call this is essentially free. Use liberally.

NEVER use this for reasoning verdicts. Per ../references/skeptical-voice.md and
the global cost-control doctrine: cheap extraction model only. Verdicts go to Opus.

Usage as library:
    from groq_extract import extract_json

    result = extract_json(
        text="Acme Corp is a Denver-based plumbing company with 47 employees.",
        schema={"company_name": "string", "city": "string", "industry": "string", "employees": "integer"},
    )
    # result = {"company_name": "Acme Corp", "city": "Denver", "industry": "plumbing", "employees": 47}
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
from typing import Any

import requests

DEFAULT_MODEL = "llama-3.1-8b-instant"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


def extract_json(
    text: str,
    schema: dict[str, str],
    model: str = DEFAULT_MODEL,
    timeout_s: int = 20,
) -> dict[str, Any]:
    """Extract structured fields from text per the schema.

    Schema is field_name -> type_hint (informational only — Groq's JSON mode does
    the heavy lifting). Returns dict matching schema keys, or {} on failure.
    """
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY not set. See ../references/tool-equipping.md.")

    schema_desc = ", ".join(f"{k} ({v})" for k, v in schema.items())
    system = (
        "You extract structured fields from text. Output ONLY a JSON object. "
        f"Fields required: {schema_desc}. If a field cannot be extracted, set it to null. "
        "No prose, no markdown fences, no commentary — just the JSON."
    )

    r = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text[:4000]},   # cap input
            ],
            "temperature": 0,
            "max_tokens": 400,
        },
        timeout=timeout_s,
    )
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Salvage: strip code fences if Groq added them despite the instruction
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            try:
                return json.loads(content.strip())
            except json.JSONDecodeError:
                return {}
        return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text", required=True)
    ap.add_argument("--schema", required=True,
                    help='JSON dict, e.g., \'{"company_name":"string","employees":"integer"}\'')
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    try:
        schema = json.loads(args.schema)
    except json.JSONDecodeError as e:
        print(f"ERROR: --schema must be valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        result = extract_json(args.text, schema, model=args.model)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
