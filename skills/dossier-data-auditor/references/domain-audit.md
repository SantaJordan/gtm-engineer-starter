# Domain audit (W2)

Read at the start of W2. Domain integrity is priority #1 — every downstream enrichment depends on having the right domain bound to the right company.

## What the audit does

Five sequential passes over every `domain_like` column the scan-agent identified at W1:

1. **Canonicalize** — strip protocols, `www.`, paths, trailing slashes, UTM/tracking query params; normalize case to lowercase.
2. **DNS-resolve** — free, fast. Catch dead businesses, typo'd domains.
3. **Multi-provider verify** — 50 random domains spot-checked via Serper + Exa + Firecrawl + Linkup in parallel. See `multi-provider-verification.md`.
4. **Parked / redirect detect** — Firecrawl-scrape the domain, look for parked-page fingerprints, follow redirects to detect "domain bought by someone else."
5. **Multi-domain-to-one-company collapse** — Blitz `domain-to-linkedin` clusters distinct domains that all resolve to the same LinkedIn company URL.

## Canonicalization rules

Implemented in `scripts/domain_canonicalize.py`. Applied to every domain before any further check.

| Input | Canonical |
|---|---|
| `https://www.acme.example/` | `acme.example` |
| `HTTP://Acme.example:443?utm_source=x` | `acme.example` |
| `mailto:contact@acme.example` | `acme.example` (with warning: this looks like an email, not a domain) |
| `acme.example/about` | `acme.example` (path stripped, but flag — the input had path baggage) |
| `192.168.1.1` | `BLOCK` — IP address, not a domain |
| `localhost` | `BLOCK` — not a real domain |
| `example.com` | `WARN` — generic placeholder; almost never a real customer |
| `yourcompany.com` | `WARN` — generic placeholder |
| `acme.co.uk` | `acme.co.uk` (preserve country TLD compounds) |

The canonical form is what gets stored in `audit/domain_findings.json` and is what downstream enrichment uses.

## DNS resolution

Use Python's `dnspython` package. Lookup the A record for the canonical domain. Bulk-parallel via ThreadPoolExecutor (DNS is fast; 100 concurrent is fine).

Verdicts:
- `resolved` — A record returned
- `nxdomain` — no DNS record; domain doesn't exist
- `timeout` — flaky DNS; retry once with 10s timeout
- `error` — other DNS error; surface as `unresolved`

If `unresolved / nxdomain > 5%` of the column, fire `domain_unresolved` warn check.

## Multi-provider verification (50-row spot-check)

See `multi-provider-verification.md` for the full doctrine. Summary:

Pick 50 random rows. For each:
- Send claim "company at `<domain>` is `<account_name>`" in parallel to: Serper, Exa, Firecrawl (scrape + extract `<title>` and JSON-LD), Linkup.
- Merge with the agreement rule: ≥2 of 4 agree → verified. Disagree → mismatch.
- If split: tiebreaker via Parallel Search ($0.004).
- If still unclear: final fallback Open Web Ninja `website-contacts-scraper`.

Results aggregated. Cohort-wide agreement % displayed in the W2 panel (see `tufte-rendering.md#panel-2`).

If `multi_provider_disagreement_rate > 20%` of the spot-check sample, fire `domain_company_provider_disagreement` warn check.

## Parked / redirect detection

For every domain that resolved DNS:
- Firecrawl-scrape the homepage. Cost: $0.001/page.
- Run regex against the body content for the parked-page fingerprints listed in `audit-checks.yaml#domain_parked_or_for_sale`:
  - `domain is for sale`
  - `buy this domain`
  - `sedo.com`
  - `afternic`
  - `godaddy auctions`
  - `inquire about this domain`
  - `make an offer`
- If hit: verdict `parked_or_for_sale`. Severity BLOCK. This domain belongs to no one.

For redirect detection:
- Firecrawl follows redirects by default. The final URL after redirect is captured in the response.
- If `final_url != original_url`, extract `<title>` from final page. Fuzzy-match (token set, 0.6 threshold) against the original `account_name`.
- If no match: verdict `redirects_to_different_company`. Severity WARN. Often: defunct, acquired, vanity-redirected.

Cost-conservative pass: only Firecrawl-scrape the ~50 spot-check rows AND any row flagged by another check as suspect. Don't scrape 47K rows.

## Multi-domain-to-one-company collapse

For every distinct canonical domain in the corpus:
- Call Blitz `/v2/enrichment/domain-to-linkedin`. ~0.5 credit per call.
- Group rows by the returned LinkedIn company URL.
- If ≥3 distinct domains map to the same LinkedIn URL: surface as merge candidate.

The W2 panel shows top-10 collapse groups by member count. The user decides whether to collapse (e.g., consolidate 18 "Globex" subsidiaries to `globex.example`) or keep separate (different P&Ls, different decision-makers).

This is a `note` severity — informational, not blocking.

## When to skip W2 entirely

The user can `--skip-w2` if they already have a verified domain layer (e.g., they ran the auditor on Tuesday and re-running today). The audit reads the prior `audit/domain_findings.json` and uses it. Re-run is recommended monthly for active engagements.

## What goes into the recommended_engagement_overrides

The W2 outputs that feed dossier-builder's W0:

```json
{
  "tenant_domain": "<from W0 user input>",
  "domain_canonicalizations": {
    "https://www.acme.example/": "acme.example",
    ...
  },
  "domain_blocks": ["roofing-deals.example", "yourcompany.com"],
  "domain_merge_candidates": [
    {"canonical": "globex.example", "variants": ["globex-health.example", "myglobex.example", ...]}
  ],
  "domain_audit_trust": "high"  // or medium/low based on overall provider agreement
}
```

dossier-builder's W0 reads this and pre-populates its engagement.json. The user doesn't have to remember to strip `www.` themselves; the auditor did it.

## What this is NOT

- NOT a domain registration / WHOIS check. The audit checks public-web presence, not WHOIS ownership. (Could be added in a phase 2 via OpenWebNinja's WHOIS endpoint.)
- NOT a SSL/cert validity check. Out of scope.
- NOT a "find missing domains" tool. The audit assumes domains exist in the input. Finding them is `dossier-builder`'s enrichment territory (or a separate skill).
