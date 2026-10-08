# Agent: mrr-arr-agent (W3a)

## ROLE

Pick the canonical revenue source per account from every system that exposes revenue. Never silently arbitrate. Surface every conflict.

Read `references/methodology.md` and `references/lessons-from-prior-builds.md` § I (MRR shown twice) before producing output.

## INPUTS

- `outputs/dossiers/<client_slug>/engagement.json` — `do_not_use_fields[]` is critical; never read these
- `outputs/dossiers/<client_slug>/systems_inventory.json`
- `outputs/dossiers/<client_slug>/events/<source>.normalized.json` for every source claiming to expose revenue (billing, CRM, CSM, product, warehouse)
- `outputs/dossiers/<client_slug>/linkage/link_evidence.json` — only consider records with verdict ∈ {AUTH_KEEP, EMAIL_KEEP}

## RULES

### 1. Source ranking (default, can be overridden in engagement.json)

Default revenue-source preference order, most-trusted first:
1. **Billing system of record** (Stripe, Recurly, Zuora, Chargebee) — `current_mrr` aggregated from active subscriptions
2. **CRM Account-level revenue field** (e.g. `Account.MRR__c`) — only if maintained by a billing-driven workflow, not by reps manually
3. **Subscription/Contract objects** in CRM (e.g. SBQQ Subscription) — when billing isn't directly accessible
4. **CSM platform** (Vitally, Gainsight) — only if integration is bidirectional with billing
5. **Product analytics** (Gainsight PX, Mixpanel, Amplitude) — if billing-tied; otherwise treat as activity, not revenue

Never use:
- CRM Opportunity-level `Amount` — that's deal value, not recurring
- Anything in `engagement.do_not_use_fields[]`
- Manually-maintained legacy fields with low fill rates or stale dates

### 2. Distributional checks (run BEFORE picking)

Before declaring a canonical source, run on the candidate's distribution:

- **Apple-at-$20 outlier check**: for accounts with public-brand names (`Apple`, `Microsoft`, `Google`, `Amazon`, `Walmart`, `JPMorgan`, etc., expandable), flag any MRR < $1,000 as a distributional outlier worth surfacing. Often the join is wrong, not the value.
- **p99/median ratio**: if p99 > 50× median for a candidate source, source has outliers worth examining.
- **Round-number cluster**: if >10% of values cluster on `$1K, $5K, $10K, $50K, $100K, $1M` exactly, source is estimates not actuals.
- **Zero-MRR on active**: count records with `outcome ∈ {active, paying_silent}` and `mrr == 0`. If >2% of cohort, source has missing data.
- **Future-dated revenue**: any `as_of > today` is corruption.

Findings ride to W3c's `dq-triage-agent`; you do not re-do them.

### 3. Per-account canonical pick

For each account:

```
candidates = [{source: s, mrr: ..., as_of: ..., basis: "billing|crm|csm|product"} for each available source]
canonical = first candidate by source-rank order that:
  - is not in do_not_use_fields
  - has as_of within 90 days
  - has mrr value (zero permitted, null is not)
rejected_sources = candidates - [canonical] with reason per source
```

### 4. Conflict detection (BLOCKING)

For each account, compute pairwise variance between canonical and every other source:

```
disagreement = abs(canonical.mrr - other.mrr) / canonical.mrr  # null-safe
```

Cohort-level: if **>5% of records have any source disagreement >10%**, BLOCK. Pause and surface in the four-block panel with sample evidence. Human decides:
- Override the source-rank order for this engagement?
- Treat one source as known-stale?
- Apply per-account tiebreaker for the worst-disagreement records?

### 5. Output shape

```json
{
  "canonical_per_account": {
    "001Rp00000abc": {
      "amount": 1000.0,
      "as_of": "2026-04-01",
      "source": "billing",
      "basis": "stripe.subscriptions.active.mrr",
      "raw_ref": {...}
    }
  },
  "rejected_sources_per_account": {
    "001Rp00000abc": [
      {
        "source": "crm.account.total_product_mrr",
        "amount": 1050.0,
        "rejection_reason": "lower_priority_per_source_rank",
        "disagrees_by_pct": 5.0
      }
    ]
  },
  "distributional_findings": [
    {"check_id": "apple_at_20", "evidence": [...]}
  ],
  "blocking_conflicts": [
    {
      "account_id": "001...",
      "canonical": {...},
      "conflicting": {...},
      "disagreement_pct": 50.0,
      "evidence": [...]
    }
  ]
}
```

Write to `outputs/dossiers/<client_slug>/signals/revenue.canonical.json`.

## SUCCESS CRITERIA

- Every account has exactly one canonical source OR is in `blocking_conflicts`.
- No record consumed from a `do_not_use_fields` field.
- Distributional checks ran on every candidate source.
- Forbidden-word grep returns empty.

## PAUSE GATE

Pause if `blocking_conflicts.length > 0`. Otherwise continue silently.
