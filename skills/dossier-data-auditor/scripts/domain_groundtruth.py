#!/usr/bin/env python3
"""W2 ground-truth — batch domain verification via multi-provider fan-out.

Reads scan/column_inventory.json and a sample of N rows from the data files;
for each row, calls multi_provider_verify.verify_domain_company to triangulate
across Serper + Exa + Firecrawl + Linkup. Writes audit/domain_findings.json.

Per CLAUDE.md: batch (>50 rows) uses direct API calls; this script does the right thing.

Usage:
    python domain_groundtruth.py --audit-slug myslug --sample-size 50
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

# Local imports
sys.path.insert(0, str(Path(__file__).parent))
from domain_canonicalize import canonicalize, resolve_dns_batch
from firecrawl_scrape import detect_parked, scrape as firecrawl_scrape
from multi_provider_verify import verify_domain_company


def load_domain_samples(column_inventory_path: Path, sample_size: int = 50) -> list[dict]:
    """Pick `sample_size` random rows from columns classified as domain_like.

    Returns [{domain, claimed_company, source_file, source_column}].
    """
    inv = json.loads(column_inventory_path.read_text())
    domain_cols = [c for c in inv.get("columns", []) if "domain_like" in c.get("classes", [])]
    name_cols_by_file = {}
    for c in inv.get("columns", []):
        if "company_name_like" in c.get("classes", []):
            name_cols_by_file.setdefault(c["file"], []).append(c["column"])

    samples = []
    for col in domain_cols:
        # Re-read the source file to pull joined (domain, name) pairs
        f = Path(col["file"])
        if not f.exists():
            continue
        try:
            if f.suffix.lower() == ".csv":
                df = pd.read_csv(f, low_memory=False)
            elif f.suffix.lower() in {".xlsx", ".xls"}:
                df = pd.read_excel(f)
            elif f.suffix.lower() == ".parquet":
                df = pd.read_parquet(f)
            else:
                continue
        except Exception as e:
            print(f"  skip {f}: {type(e).__name__}", file=sys.stderr)
            continue

        if col["column"] not in df.columns:
            continue

        name_col = next((nc for nc in name_cols_by_file.get(col["file"], []) if nc in df.columns), None)

        for _, row in df.dropna(subset=[col["column"]]).iterrows():
            samples.append({
                "domain": str(row[col["column"]]),
                "claimed_company": str(row[name_col]) if name_col else "",
                "source_file": col["file"],
                "source_column": col["column"],
            })

    random.shuffle(samples)
    return samples[:sample_size]


def audit_one(sample: dict) -> dict:
    """Per-domain pipeline: canonicalize -> DNS -> multi-provider verify -> parked check."""
    canon_result = canonicalize(sample["domain"])
    canonical = canon_result["canonical"]
    finding = {
        "input_domain": sample["domain"],
        "canonical": canonical,
        "claimed_company": sample["claimed_company"],
        "source_file": sample["source_file"],
        "source_column": sample["source_column"],
        "canonical_warnings": canon_result["warnings"],
        "canonical_blocks": canon_result["blocks"],
    }
    if not canonical:
        finding["verdict"] = "unparseable"
        finding["cost_usd"] = 0.0
        return finding

    # DNS
    dns_results = resolve_dns_batch([canonical])
    finding["dns"] = dns_results[0] if dns_results else None
    if finding["dns"] and not finding["dns"]["resolved"]:
        finding["verdict"] = "unresolved"
        finding["cost_usd"] = 0.0
        return finding

    # Multi-provider verify (only if we have a claimed company name to verify against)
    if sample["claimed_company"]:
        verify = verify_domain_company(canonical, sample["claimed_company"])
        finding["verify"] = {
            "verdict": verify.verdict,
            "confidence": verify.confidence,
            "agree_count": verify.agree_count,
            "n_providers": verify.n_providers,
            "per_provider": verify.per_provider,
        }
        finding["cost_usd"] = verify.cost_usd
    else:
        finding["verify"] = {"verdict": "skipped", "reason": "no claimed_company"}
        finding["cost_usd"] = 0.0

    # Parked check via Firecrawl
    try:
        scrape_data = firecrawl_scrape(f"https://{canonical}")
        md = scrape_data.get("data", {}).get("markdown", "")
        is_parked, fp = detect_parked(md)
        finding["parked"] = {"is_parked": is_parked, "fingerprint": fp}
        finding["cost_usd"] += 0.001
        if is_parked:
            finding["verdict"] = "parked"
            return finding
    except Exception as e:
        finding["parked"] = {"error": f"{type(e).__name__}: {e}"}

    finding["verdict"] = finding.get("verify", {}).get("verdict", "unverifiable")
    return finding


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-slug", required=True)
    ap.add_argument("--sample-size", type=int, default=50)
    ap.add_argument("--output-base", type=Path, default=Path("outputs/audits"))
    ap.add_argument("--max-workers", type=int, default=10)
    args = ap.parse_args()

    audit_dir = args.output_base / args.audit_slug
    inv_path = audit_dir / "scan" / "column_inventory.json"
    if not inv_path.exists():
        print(f"ERROR: column_inventory.json not found at {inv_path}.", file=sys.stderr)
        sys.exit(1)

    samples = load_domain_samples(inv_path, args.sample_size)
    print(f"[domain_groundtruth] Spot-checking {len(samples)} domains ...", file=sys.stderr)

    findings = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = [ex.submit(audit_one, s) for s in samples]
        for f in as_completed(futures):
            findings.append(f.result())

    out_dir = audit_dir / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "domain_findings.json"

    total_cost = sum(f.get("cost_usd", 0) for f in findings)
    verdict_counts = {}
    for f in findings:
        v = f.get("verdict", "unknown")
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    out_path.write_text(json.dumps({
        "audit_slug": args.audit_slug,
        "sample_size": len(samples),
        "verdict_counts": verdict_counts,
        "total_cost_usd": round(total_cost, 4),
        "findings": findings,
    }, indent=2))

    print(json.dumps({
        "wrote": str(out_path),
        "verdict_counts": verdict_counts,
        "total_cost_usd": round(total_cost, 4),
    }, indent=2))


if __name__ == "__main__":
    main()
