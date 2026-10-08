# Multi-domain consolidation (W2c)

> Detect when multiple "separate accounts" with distinct domains roll up to the same parent company. Loaded by `scripts/multi_domain_cluster.py`.

## The problem

One parent company often sells under several brands, each on its own domain.
Intuit, for example, sells QuickBooks, TurboTax, Credit Karma (acquired 2020) and
Mailchimp (acquired 2021) alongside intuit.com.

If a CRM has (an invented example):
- `globexmail.example` as a paying customer
- `globex.example` as a closed-lost prospect that sales is now pursuing again
- `globexbooks.example` as a never-contacted lead

The auditor must surface that all three roll up to Globex Holdings and the sales team needs to know. Otherwise outbound to "Globex" looks net-new when there's already a paying relationship through GlobexMail.

Other well-known patterns:
- Microsoft: microsoft.com, plus GitHub (acquired 2018) and LinkedIn (acquired 2016), plus product sites such as bing.com and xbox.com.
- Adobe: adobe.com, plus Marketo and Magento (both acquired 2018) and Frame.io (acquired 2021).
- A large conglomerate may run divisional or regional sites that are separate legal entities with separate contracts. Those are a human decision (`keep_separate` is often right), not an automatic merge.

**Check current ownership before you merge.** Ownership changes: announced deals get called off (Adobe's planned purchase of Figma was abandoned in 2023, so Figma is not an Adobe company), and brands get sold. Treat any ownership list, including this one, as a starting point and confirm with the signals below.

## The algorithm

For each unordered pair (d_i, d_j) of canonical domains from W2a-b output:

| Signal | Weight | API |
|---|---|---|
| Blitz `domain-to-linkedin(d_i)` == Blitz `domain-to-linkedin(d_j)` | strong | Blitz (unlimited) |
| OpenWebNinja AI Mode response on "what company owns d_i" matches "what company owns d_j" | strong | OpenWebNinja |
| Same WHOIS owner organization | strong | WHOIS (free) |
| 301-chain `d_i → d_j` (one redirects to the other) | strong | Firecrawl / curl |
| Shared Google Tag Manager / GA4 / Segment write key | medium | Page scrape via Firecrawl |
| Same DNS NS records | weak | DNS (free) |
| DiscoLike `/subsidiaries(d_i).children` contains d_j | strong (last-resort) | DiscoLike (only if Blitz + OWN fail) |

### Clustering decision

- **≥2 signals total** → cluster (both `d_i` and `d_j` group together).
- **≥1 strong signal** (Blitz match OR OpenWebNinja parent match OR WHOIS match OR 301-chain) → cluster.
- **≥1 weak signal alone** (shared GTM or shared NS) → flag for human, don't auto-cluster.

## Provider routing

Blitz first (unlimited; called per pair). OpenWebNinja AI Mode second. DiscoLike `/subsidiaries` is last-resort — only run when Blitz returns null AND OpenWebNinja returns null.

Pair-space is O(N²) on number of distinct domains in the corpus. For a 5K-account corpus with ~2K distinct canonical domains after W2a-b, that's 2M pairs — too many to query. Reduce by:

1. Cluster candidates by shared 2nd-level word (`globex*`, `contoso*`) before querying.
2. Only query Blitz on pairs where the LinkedIn round-trip on each domain individually returned a non-null LinkedIn URL.
3. Only query OpenWebNinja AI Mode on pairs that Blitz couldn't resolve.

Typical query volume on a 5K-account corpus: ~50-200 Blitz calls, ~50 OpenWebNinja calls. Cost: ~$0.50-1.00.

## What the panel surfaces at W2c

For each cluster with ≥2 affected accounts in the customer's data:

```
CLUSTER cl_0017 (3 domains, evidence: Blitz + OpenWebNinja):
  Parent:    globex.example
  Children:  globexmail.example, globexbooks.example
  Affected accounts in your data:
    - Globex Holdings         ($40K MRR, customer)
    - GlobexMail              ($10K MRR, customer)
    - GlobexBooks (prospect)  (no MRR, open opportunity)

  ⚠ Sales is pursuing "GlobexBooks" as net-new. They may not know Globex is
    already a paying customer. Recommend roll-up to parent.

  Action: [rollup_to_parent / keep_separate / both]
```

## Output

`multi_domain_clusters.json`:

```json
{
  "clusters": [
    {
      "cluster_id": "cl_0017",
      "parent_domain": "globex.example",
      "child_domains": ["globexmail.example", "globexbooks.example"],
      "evidence_signals": ["blitz_same_linkedin", "openwebninja_ai_parent_match"],
      "evidence_quality": "strong",
      "affected_account_ids": ["001A...", "001B...", "001C..."],
      "rollup_recommendation": "rollup_to_parent",
      "human_decision": null
    }
  ]
}
```

This file feeds `recommended_engagement_overrides.json:multi_domain_clusters` at W7. The dossier-builder's W2b identity-linking agent reads this and, if `rollup_to_parent` is set, treats the child accounts as the same logical account.
