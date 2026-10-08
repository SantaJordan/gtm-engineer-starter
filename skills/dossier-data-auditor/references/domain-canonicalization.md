# Domain canonicalization rubric (W2a + W2b)

> Loaded by `scripts/canonical_domain_pick.py` (deterministic) and `domain-canonicalizer-agent` (W2b semantic adjudication). The rule encoded in `config/domain-rubric.yaml`; this file is the prose explanation of why each rule exists.

## The core problem

In real B2B engagement data, no record has "the company domain" as a single trustworthy column. Every account has 3-8 URL-bearing fields and they disagree:

- `Account.Website` (sales rep typed; often dirty: `www.acme.example/about-us` or just `acme`)
- `Account.Domain__c` or `Account.Email_Domain__c` (custom field, often stale)
- Domain extracted from contact emails (`@acme.example` × 47, `@gmail.com` × 12, `@ext-acme.example` × 3)
- LinkedIn URL → registered domain (via Blitz round-trip)
- `clearbit_domain`, `zoominfo_website`, `apollo_domain` (enrichment columns; varying freshness)
- `additional_domains[]`, `subsidiaries[]` (occasionally populated)
- Free-text URLs in `Notes`, `Description`
- Billing-side `Stripe.Customer.email` domain (often the AR person's personal email)

The auditor's job: per account, pick ONE canonical domain (with state and confidence), or refuse to pick and surface the conflict.

## The rubric (encoded in `config/domain-rubric.yaml`)

### Step 1 — canonicalize each candidate

```
lowercase → strip_scheme (http://, https://) → strip_www_prefix →
strip_path → strip_query_string → strip_fragment →
strip_trailing_slash → idn_decode (xn-- prefix → unicode)
```

### Step 2 — exclude noise BEFORE scoring

- The tenant's own domain (`audit_context.tenant_domain`).
- Personal email domains: the core list shipped at `config/personal-domains.txt` (gmail, outlook, hotmail, yahoo, icloud, aol, proton and other large providers). Add a longer free-mail list with `--personal-domains <file>`. The picker refuses to run if the shipped list is missing.
- Parking-page domains: `sedo.com`, `dan.com`, `hugedomains`, `bodis`, GoDaddy auctions.
- Platform tenant subdomains: `*.salesforce.com`, `*.lightning.force.com`, `*.hubspot.com`, `*.myshopify.com`, `*.zendesk.com`, `*.atlassian.net`. Strip to platform root and flag for re-resolution.
- Link shorteners: `bit.ly`, `lnkd.in`, `t.co`, `ow.ly`, `tinyurl.com`. Expand or drop.

### Step 3 — score each remaining candidate

| Signal | Weight | Rationale |
|---|---|---|
| Appears in any `primary_website` column | +3 | Strongest single signal; this is what the sales rep typed. |
| Appears in any `domain_only` column | +3 | Often hand-entered by RevOps; trustworthy when present. |
| Agreement across ≥2 enrichment providers | +2 | Independent confirmation. |
| Agreement from 1 enrichment provider | +1 | Helpful when primary_website is blank. |
| Blitz LinkedIn round-trip match (LinkedIn URL → registered domain matches candidate) | +2 | LinkedIn's own canonical mapping is authoritative within its index. |
| Modal email-inferred domain ≥ 40% (after tenant_strip + personal-domain exclusion) | +2 | Strong real-world usage signal. |
| Modal email-inferred domain 20–40% | +1 | Plausible but weaker. |
| Appears in `secondary_domain` field but NOT `primary_website` | -2 | Subsidiary, not parent. |
| DNS NXDOMAIN | -3 | Dead. |
| Firecrawl parking-page signature match | -3 | Parked. |
| Is a subdomain (store.X, blog.X, support.X) when an apex variant also scores positive | -1 | Prefer apex. |

### Step 4 — confidence band

| Band | Min score | Min agreement quorum (count of positive rules) |
|---|---|---|
| `high` | 6 | 4 |
| `medium` | 4 | 3 |
| `low` | 2 | 2 |
| `zero` | otherwise — surface to human |

### Step 5 — verdict labels (mutually exclusive)

- `CANONICAL_VERIFIED` — high confidence; primary_website matches a strong cluster.
- `CANONICAL_INFERRED` — medium confidence; primary_website unset but enrichment + email-domain modal agree.
- `CANONICAL_PARKED` — best candidate resolves to parking page.
- `CANONICAL_DEAD` — best candidate fails DNS.
- `CANONICAL_ACQUIRED_REPOINTED` — 301-chain ends at unrelated domain. Re-canonicalize to redirect target.
- `MULTI_DOMAIN_NEEDS_CONSOLIDATION` — two candidates both score high AND Blitz/DiscoLike say one is a child of the other. Forward to W2c.
- `CONFLICT_REQUIRES_HUMAN` — candidates disagree at similar scores. Tiebreaker required.
- `NO_USABLE_DOMAIN` — all candidates excluded. Drop account or invoke `domain-linkedin-finder` on account_name.

## What the panel surfaces at W2b

Render the worst 5 disagreement cases first. RevOps eyeballs these — they're often recently-renamed companies, M&A targets, or parking situations RevOps recognizes immediately. The freemail-as-account-domain count goes next: "47 accounts have canonical domain `gmail.com`. Real SMB customers or noise?" Then state distribution: how many `verified` vs `parked` vs `dead` vs `acquired_or_repointed`.

## When to call out to `enrichment/domain-linkedin-finder`

The auditor imports four scripts from this skill (don't reimplement):

- `domain_resolver.py` — orchestrated lookup waterfall (OWN Maps → Search → AI → Exa → Blitz → scrape → LLM judge).
- `website_scraper.py` — local aiohttp + BS4 scraper; Apify fallback. Use for `domain_state_classify.py`.
- `llm_judge.py` — calibrated Opus prompt for "does this domain belong to this company name?" verdicts.
- `domain_utils.py` — `clean_domain`, `is_personal_domain`, `is_gov_domain`, `is_likely_parked` heuristics.
