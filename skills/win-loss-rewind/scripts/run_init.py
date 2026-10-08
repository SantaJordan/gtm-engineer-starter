#!/usr/bin/env python3
"""
Phase 0 driver for Win-Loss Rewind.

Drives the data-archaeology phase end-to-end:

    1. Generate (or accept) a run-id and create outputs/<run-id>/.
    2. Run the requested adapter (csv | salesforce | gong_only) to produce
       outputs/<run-id>/raw_dataset.json.
    3. Read prompts/data-archaeology.md as the system prompt.
    4. Probe the 5 high-ARR sample rows: Firecrawl homepage scrape +
       (best-effort) Apify LinkedIn employee count. Both degrade gracefully
       if the relevant API key is missing.
    5. Build a user message with the schema + sample + probe results and
       call the current Sonnet (model id from wlr_config.py) with streaming.
    6. Save the streamed response to outputs/<run-id>/data_confidence.md.
    7. Halt with the standard "Phase 0 complete" message.

Usage:
    python run_init.py <csv-path> [--adapter csv|salesforce|gong_only]
                                  [--run-id <id>]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]


SCRIPTS_DIR = Path(__file__).parent.resolve()
SKILL_ROOT = SCRIPTS_DIR.parent
ADAPTERS_DIR = SCRIPTS_DIR / "adapters"
PROMPTS_DIR = SKILL_ROOT / "prompts"
OUTPUTS_DIR = SKILL_ROOT / "outputs"

from wlr_config import SONNET_MODEL as ANTHROPIC_MODEL  # noqa: E402


# -- Run setup ---------------------------------------------------------------

def make_run_id(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    return "wlr-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def ensure_run_dir(run_id: str) -> Path:
    p = OUTPUTS_DIR / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# -- Adapter dispatch --------------------------------------------------------

def run_adapter(adapter: str, csv_path: str | None, raw_out: Path) -> dict:
    """
    Invoke the chosen adapter as a subprocess. Each adapter is a CLI in
    scripts/adapters/<name>.py and writes raw_dataset.json to --out.
    Returns the parsed Dataset dict.
    """
    script = ADAPTERS_DIR / f"{adapter}.py"
    if not script.exists():
        raise RuntimeError(f"Adapter script not found: {script}")

    cmd = [sys.executable, str(script)]
    if adapter == "csv":
        if not csv_path:
            raise RuntimeError("csv adapter requires a <csv-path> argument.")
        cmd.append(csv_path)
    cmd.extend(["--out", str(raw_out)])

    print(f"[run_init] running adapter: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(
            f"Adapter '{adapter}' failed with exit {result.returncode}."
        )
    if result.stdout:
        print(result.stdout)
    if not raw_out.exists():
        raise RuntimeError(
            f"Adapter '{adapter}' completed but didn't write {raw_out}."
        )
    with raw_out.open() as f:
        return json.load(f)


# -- External reality probes -------------------------------------------------

def _guess_domain(row: dict) -> str | None:
    """
    Find a domain-shaped value in the row. We check common column names first
    (website, domain, url, company_website) then fall back to scanning every
    string field for one that looks like a URL.
    """
    candidates = ("website", "domain", "url", "company_website", "site", "homepage")
    for k in candidates:
        for col, val in row.items():
            if str(col).lower() == k and isinstance(val, str) and val.strip():
                return _normalize_domain(val)
    for val in row.values():
        if isinstance(val, str) and ("." in val) and (
            val.lower().startswith("http") or val.lower().startswith("www.")
            or _looks_like_bare_domain(val)
        ):
            return _normalize_domain(val)
    return None


def _looks_like_bare_domain(s: str) -> bool:
    s = s.strip().lower()
    if " " in s or "@" in s:
        return False
    if "." not in s:
        return False
    # crude: has a TLD-ish suffix
    return any(s.endswith(tld) for tld in (".com", ".io", ".co", ".net", ".org", ".ai", ".app"))


def _normalize_domain(s: str) -> str:
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return "https://" + s.lstrip("/")


def firecrawl_probe(url: str, api_key: str) -> dict:
    """Single Firecrawl scrape. Returns a small summary dict, never raises."""
    try:
        resp = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            timeout=45,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or {}
        markdown = (data.get("markdown") or "")[:2000]
        meta = data.get("metadata") or {}
        return {
            "ok": True,
            "url": url,
            "title": meta.get("title"),
            "description": meta.get("description"),
            "markdown_excerpt": markdown,
            "page_count_estimate": markdown.count("\n#") + 1,
        }
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)[:200]}


def apify_linkedin_probe(domain: str, token: str) -> dict:
    """
    Best-effort LinkedIn employee-count probe.

    STUB: There are several Apify actors that scrape LinkedIn company pages
    given a domain (e.g. apimaestro/linkedin-company-detail). The exact
    actor + input shape depends on which one the user has in their account.
    Wrapped in try/except so failure here never blocks Phase 0 — the
    Firecrawl signal alone is enough to flag the most egregious divergences
    (parked domains, one-page sites).
    """
    try:
        # TODO: wire to a specific Apify actor. Expected pattern:
        #   POST https://api.apify.com/v2/acts/<actor-id>/run-sync-get-dataset-items
        #        ?token=<token>
        #   body: {"domains": [domain]}
        # For now return a structured "not wired" signal so the prompt can
        # tell Claude to skip the LinkedIn branch.
        return {
            "ok": False,
            "domain": domain,
            "error": "apify_linkedin_probe not wired (TODO)",
        }
    except Exception as e:
        return {"ok": False, "domain": domain, "error": str(e)[:200]}


def probe_high_arr_sample(rows: list[dict]) -> tuple[list[dict], dict]:
    """
    Probe up to 5 rows. Returns (probe_results, key_status).
    key_status reports which API keys were available.
    """
    fc_key = os.environ.get("FIRECRAWL_API_KEY")
    apify_token = os.environ.get("APIFY_TOKEN")
    key_status = {
        "firecrawl_key_present": bool(fc_key),
        "apify_token_present": bool(apify_token),
    }
    results: list[dict] = []
    for i, row in enumerate(rows[:5]):
        domain = _guess_domain(row)
        entry: dict = {
            "row_index": i,
            "row_preview": {k: v for k, v in list(row.items())[:6]},
            "domain_guess": domain,
        }
        if domain and fc_key:
            entry["firecrawl"] = firecrawl_probe(domain, fc_key)
        else:
            entry["firecrawl"] = {"ok": False, "error": "no domain or no FIRECRAWL_API_KEY"}
        if domain and apify_token:
            entry["apify_linkedin"] = apify_linkedin_probe(domain, apify_token)
        else:
            entry["apify_linkedin"] = {"ok": False, "error": "no domain or no APIFY_TOKEN"}
        results.append(entry)
    return results, key_status


# -- Anthropic call ----------------------------------------------------------

def call_sonnet(system_prompt: str, user_message: str, out_path: Path) -> str:
    """
    Stream a the current Sonnet (wlr_config.py) response. Writes the streamed text to `out_path`
    incrementally and returns the final string.
    """
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "The `anthropic` Python SDK is required. Install with: pip install anthropic"
        ) from e

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set in the environment.")

    client = Anthropic()
    chunks: list[str] = []

    with out_path.open("w") as f:
        with client.messages.stream(
            model=ANTHROPIC_MODEL,
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                f.write(text)
                f.flush()
                chunks.append(text)
                # Echo to stdout so the operator sees progress.
                sys.stdout.write(text)
                sys.stdout.flush()

    sys.stdout.write("\n")
    return "".join(chunks)


# -- User-message construction -----------------------------------------------

def build_user_message(dataset: dict, probes: list[dict], key_status: dict) -> str:
    """Assemble the Phase 0 user message handed to Sonnet."""
    schema_summary = json.dumps(dataset.get("schema", []), indent=2)
    sample = json.dumps(dataset.get("sample_rows", []), indent=2, default=str)
    high_arr = json.dumps(dataset.get("high_arr_sample", []), indent=2, default=str)

    parts: list[str] = []
    parts.append(f"# Phase 0 inputs for run\n")
    parts.append(f"Adapter: `{dataset.get('adapter')}`")
    parts.append(f"Source: `{dataset.get('source_path')}`")
    parts.append(
        f"Rows: {dataset.get('row_count')}  Cols: {dataset.get('column_count')}"
    )
    if dataset.get("notes"):
        parts.append("Adapter notes:")
        for n in dataset["notes"]:
            parts.append(f"  - {n}")
    parts.append(f"\nARR column guess: `{dataset.get('arr_column_guess')}`")

    parts.append("\n## Schema\n```json\n" + schema_summary + "\n```")
    parts.append("\n## 10-row random sample\n```json\n" + sample + "\n```")
    parts.append("\n## High-ARR sample (5 rows used for the reality check)\n```json\n" + high_arr + "\n```")

    if probes:
        parts.append(
            "\n## External reality probes (5 high-ARR rows)\n"
            f"Firecrawl key present: {key_status['firecrawl_key_present']}  |  "
            f"Apify token present: {key_status['apify_token_present']}\n"
            "```json\n" + json.dumps(probes, indent=2, default=str) + "\n```"
        )
    else:
        parts.append(
            "\n## External reality probes\n"
            "**SKIP THE REALITY-CHECK SECTION.** No FIRECRAWL_API_KEY or APIFY_TOKEN was "
            "available in the environment, so no probes were run. Note this explicitly "
            "in `data_confidence.md` and surface it as something to verify in Phase 2."
        )

    parts.append(
        "\n---\n\nProduce `data_confidence.md` per the system prompt. "
        "Do not invent — surface every assumption as a numbered question."
    )
    return "\n".join(parts)


# -- Main --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Win-Loss Rewind Phase 0 driver (data archaeology).",
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=None,
        help="Path to customer CSV (required when --adapter csv).",
    )
    parser.add_argument(
        "--adapter",
        choices=("csv", "salesforce", "gong_only"),
        default="csv",
        help="Which adapter to run (default: csv).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Explicit run id. Defaults to wlr-<YYYYMMDD-HHMMSS>.",
    )
    args = parser.parse_args(argv)

    if load_dotenv is not None:
        load_dotenv()

    run_id = make_run_id(args.run_id)
    run_dir = ensure_run_dir(run_id)
    raw_out = run_dir / "raw_dataset.json"

    print(f"[run_init] run-id: {run_id}")
    print(f"[run_init] run-dir: {run_dir}")

    # Step 1-3: adapter → raw_dataset.json
    dataset = run_adapter(args.adapter, args.csv_path, raw_out)
    print(
        f"[run_init] dataset loaded: {dataset.get('row_count')} rows, "
        f"{dataset.get('column_count')} cols"
    )

    # Step 4: probes
    probes, key_status = probe_high_arr_sample(dataset.get("high_arr_sample", []))
    have_any_probe = key_status["firecrawl_key_present"] or key_status["apify_token_present"]
    print(
        f"[run_init] probes: firecrawl={'on' if key_status['firecrawl_key_present'] else 'off'} "
        f"apify={'on' if key_status['apify_token_present'] else 'off'}"
    )

    # Step 5: load system prompt
    system_path = PROMPTS_DIR / "data-archaeology.md"
    if not system_path.exists():
        raise RuntimeError(f"System prompt not found: {system_path}")
    system_prompt = system_path.read_text()

    # Step 6: build user message + call Sonnet streaming
    user_message = build_user_message(
        dataset, probes if have_any_probe else [], key_status
    )

    # Persist the inputs we sent to the model for reproducibility.
    (run_dir / "phase0_input.json").write_text(
        json.dumps(
            {
                "model": ANTHROPIC_MODEL,
                "system_prompt_path": str(system_path),
                "key_status": key_status,
                "probes": probes,
                "user_message_preview": user_message[:1500],
            },
            indent=2,
            default=str,
        )
    )

    out_md = run_dir / "data_confidence.md"
    print(f"\n[run_init] streaming the current Sonnet (wlr_config.py) → {out_md}\n" + ("-" * 60))
    call_sonnet(system_prompt, user_message, out_md)

    # Step 7: halt message
    print("-" * 60)
    print(f"\nPhase 0 complete — review outputs/{run_id}/data_confidence.md "
          f"and re-run with `/win-loss-rewind run` to proceed.\n")
    print(f"  run-id:           {run_id}")
    print(f"  raw_dataset:      {raw_out}")
    print(f"  data_confidence:  {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
