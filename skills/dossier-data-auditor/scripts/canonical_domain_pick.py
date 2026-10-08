#!/usr/bin/env python3
"""
canonical_domain_pick.py — W2a step 2: apply the +3/+2/-3 rubric from
config/domain-rubric.yaml to each account's URL candidates. Emit
canonical_domain_picks.json with confidence band per account.

Usage:
    uv run canonical_domain_pick.py --audit-slug <slug>
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import yaml


def load_rubric(skill_root: Path) -> dict:
    with (skill_root / "config" / "domain-rubric.yaml").open() as f:
        return yaml.safe_load(f)


def load_personal_domains_blocklist(skill_root: Path, extra: list[Path] | None = None) -> set[str]:
    """The shipped personal/free-email list (config/personal-domains.txt) plus any
    --personal-domains files. Fails loudly: a missing or empty list would let
    gmail.com be picked as a customer's company domain."""
    paths = [skill_root / "config" / "personal-domains.txt", *(extra or [])]
    out: set[str] = set()
    for p in paths:
        if not p.is_file():
            raise SystemExit(f"personal-domains list not found: {p} "
                             "(it ships with the skill; refusing to run without it)")
        out |= {line.strip().lower() for line in p.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")}
    if "gmail.com" not in out:
        raise SystemExit(f"personal-domains list looks wrong (no gmail.com): {paths[0]}")
    return out


PARKING_FINGERPRINTS = {"sedo.com", "dan.com", "godaddy.com", "hugedomains.com", "bodis.com", "afternic.com"}
PLATFORM_SUBDOMAIN_SUFFIXES = (".salesforce.com", ".lightning.force.com", ".hubspot.com",
                                ".myshopify.com", ".shopify.com", ".zendesk.com",
                                ".freshdesk.com", ".atlassian.net")
LINK_SHORTENERS = {"bit.ly", "lnkd.in", "t.co", "ow.ly", "tinyurl.com", "buff.ly"}


def is_excluded(domain: str, tenant_domain: set[str], personal_domains: set[str]) -> tuple[bool, str | None]:
    if domain in tenant_domain:
        return True, "tenant_domain"
    if domain in personal_domains:
        return True, "personal_email_domain"
    if domain in PARKING_FINGERPRINTS:
        return True, "parking_page_domain"
    if any(domain.endswith(suffix) for suffix in PLATFORM_SUBDOMAIN_SUFFIXES):
        return True, "platform_subdomain"
    if domain in LINK_SHORTENERS:
        return True, "link_shortener"
    return False, None


def score_candidate(
    domain: str,
    account_buckets: dict[str, list[str]],
    rubric: dict,
) -> tuple[float, list[str]]:
    """Return (score, contributing_rule_ids)."""
    score = 0.0
    contributors: list[str] = []

    primary_websites = set(account_buckets.get("primary_website", []))
    domain_onlys = set(account_buckets.get("domain_only", []))
    enrichment_domains_list = []
    for k, v in account_buckets.items():
        if k.startswith("enrichment_") or k == "enrichment_domain":
            enrichment_domains_list.extend(v)
    enrichment_counter = Counter(enrichment_domains_list)
    secondary_domains = set(account_buckets.get("secondary_domain", []))

    if domain in primary_websites:
        score += 3
        contributors.append("appears_in_primary_website")
    if domain in domain_onlys:
        score += 3
        contributors.append("appears_in_domain_only_field")
    if enrichment_counter.get(domain, 0) >= 2:
        score += 2
        contributors.append("agreement_two_plus_enrichment_providers")
    elif enrichment_counter.get(domain, 0) == 1:
        score += 1
        contributors.append("agreement_one_enrichment_provider")
    if domain in secondary_domains and domain not in primary_websites:
        score -= 2
        contributors.append("in_secondary_domains_but_not_primary")

    # Modal email-inferred domain
    email_domains = account_buckets.get("email_inferred_domain", [])
    if email_domains:
        counts = Counter(email_domains)
        if counts:
            top_domain, top_count = counts.most_common(1)[0]
            share = top_count / len(email_domains)
            if domain == top_domain:
                if share >= 0.40:
                    score += 2
                    contributors.append("modal_email_domain_majority")
                elif share >= 0.20:
                    score += 1
                    contributors.append("modal_email_domain_minority")
    return score, contributors


def confidence_band(score: float, quorum: int, rubric: dict) -> str:
    bands = rubric.get("confidence_bands", {})
    for band_name in ("high", "medium", "low"):
        band = bands.get(band_name, {})
        if score >= band.get("min_score", 999) and quorum >= band.get("min_agreement_quorum", 999):
            return band_name
    return "zero"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--tenant-domain", action="append", default=[],
                        help="Tenant domain (can repeat for multiple)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--personal-domains", type=Path, action="append", default=[],
                        help="Extra personal/free-email domain list (one per line); "
                             "added to the shipped config/personal-domains.txt")
    parser.add_argument("--skill-root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    rubric = load_rubric(args.skill_root)
    personal_domains = load_personal_domains_blocklist(args.skill_root, args.personal_domains)
    tenant_domain = {d.lower() for d in args.tenant_domain}

    per_account = json.loads((out_dir / "domain_candidates_per_account.json").read_text())

    picks: list[dict] = []
    for account in per_account:
        buckets = account["url_field_values"]
        all_domains = set()
        for vs in buckets.values():
            for v in vs:
                all_domains.add(v)
        candidates = []
        for d in all_domains:
            excluded, reason = is_excluded(d, tenant_domain, personal_domains)
            if excluded:
                continue
            score, contributors = score_candidate(d, buckets, rubric)
            candidates.append({
                "domain": d,
                "score": score,
                "agreement_quorum": len(contributors),
                "contributors": contributors,
            })
        candidates.sort(key=lambda c: -c["score"])
        if not candidates:
            picks.append({
                **account,
                "verdict": "NO_USABLE_DOMAIN",
                "canonical_domain": None,
                "confidence": "zero",
                "runner_up": None,
                "all_candidates": [],
            })
            continue
        top = candidates[0]
        runner = candidates[1] if len(candidates) > 1 else None
        conf = confidence_band(top["score"], top["agreement_quorum"], rubric)
        picks.append({
            **account,
            "verdict": "RUBRIC_PICK",
            "canonical_domain": top["domain"],
            "score": top["score"],
            "agreement_quorum": top["agreement_quorum"],
            "contributors": top["contributors"],
            "confidence": conf,
            "runner_up": runner["domain"] if runner else None,
            "runner_up_score": runner["score"] if runner else None,
            "all_candidates": candidates[:5],
        })

    (out_dir / "canonical_domain_picks.json").write_text(json.dumps(picks, indent=2, default=str))
    summary = {
        "accounts_scored": len(picks),
        "by_confidence": Counter([p["confidence"] for p in picks]),
        "no_usable_domain_count": sum(1 for p in picks if p["verdict"] == "NO_USABLE_DOMAIN"),
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
