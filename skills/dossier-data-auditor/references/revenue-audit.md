# Revenue audit (W4)

> Loaded by `scripts/revenue_reconcile.py` and the W4 revenue-agent. The job: when an account has six revenue-like columns reporting different numbers, pick the canonical one per concept cluster (MRR, ARR, deal_amount, etc.) using a deterministic rubric — and refuse to silently pick when the conflict is too large.

## The six-revenue-columns problem

Every CRM has six revenue columns. They disagree.

Invented example:

| Column | Value (account X) | Likely meaning |
|---|---|---|
| `Account.AnnualRevenue` | $3,000,000 | third-party firmographic enrichment, years old |
| `Account.MRR__c` | $10,000 | integration sync from billing, nightly |
| `Account.Booked_MRR__c` | $7,500 | RevOps-maintained subset |
| `Opportunity.Amount` (sum of open opps) | $30,000 | Sales pipeline value |
| `Subscription.Amount` (sum of active subs) | $11,000 | subscription-system truth |
| `payments_last_12_months` (from billing) | $120,000 | Trailing-12-month actuals |

These represent six different things. The dossier-builder will pick whichever it queries first unless told otherwise. The auditor's job is to tell the builder what to pick.

## The canonical-pick rubric

Per `config/revenue-rubric.yaml`. Each candidate column gets a score:

| Rule | Weight | Effect |
|---|---|---|
| `highest_fill_rate` | +10 | The biggest single signal. Normalized 0-1. |
| `do_not_use_demotion` | -100 | Blocking. Listed in known-bad patterns: `Account.Revenue_Rollup__c`, `Opportunity.Amount`, `Account.AnnualRevenue`. |
| `round_number_demotion` | -3 | ≥ 10% of values cluster at round numbers (1000, 5000, ...) — estimates, not actuals. |
| `source_reputation` | +2 | billing (5) > crm (4) > csm (3) > marketing (2) > scraped (1). |
| `recency_tiebreaker` | +1 | Used when tied; prefers the column from the most-recently-updated source. |
| `benfords_law_check` | -2 | Fails Benford's first-digit χ² p < 0.01. |
| `apple_at_20_penalty` | -2 | Brand-name account at MRR < $1K — penalize the source column. |
| `round_cluster_modal_spike` | -2 | > 5% of values share modal round number (default placeholder). |

Highest score per cluster wins. Runner-up surfaced if score margin < 2.0 so the human can override.

## The do-not-use blocklist

Known-bad patterns (`config/revenue-rubric.yaml:selection_rules.do_not_use_demotion.known_patterns`):

- `Account.Revenue_Rollup__c` — example: always zero because the integration that filled it silently stopped.
- `Opportunity.Amount` — deal value, not MRR. Picking this as MRR inflates by ~10-50× per opportunity.
- `Account.AnnualRevenue` — usually ZoomInfo-stamped, often years stale; useful for ICP firmographics but not for current revenue.

The auditor also reads `audit_context.do_not_use_fields_from_revops[]` (captured at W0) and adds any field the user explicitly flagged.

## The concept clusters

`revenue_reconcile.py` clusters all `revenue_like` columns into concept groups by header-keyword match:

| Cluster | Keywords | Typical unit |
|---|---|---|
| `probable_mrr` | mrr, monthly_revenue, monthly_recurring, MRR_Canonical, Net_MRR | usd/month |
| `probable_arr` | arr, annual_recurring, annual_revenue, AnnualRevenue | usd/year |
| `probable_deal_amount` | Opportunity.Amount, deal_value, contract_value, ACV, TCV | usd one-time. **Cannot be used for recurring.** |
| `probable_historical_total` | total_revenue, total_revenue_t12m, lifetime_revenue, LTV | usd cumulative |
| `probable_payment_volume` | total_payments, gmv, gross_processed, total_medicare_payments | usd cumulative |
| `probable_count_proxy` | total_drivers, trucks, total_patients, num_seats | count (not currency) |

For each cluster, the rubric picks one canonical column. Conflict-block kicks in when two sources disagree by > 10% on > 5% of records — pause and surface, don't silently pick.

## The Apple-at-$20 watchlist

15 brand-name companies on the watchlist (Apple, Microsoft, Google, Amazon, Walmart, JPMorgan, Goldman, Tesla, Meta, Netflix, Adobe, Salesforce, Oracle, IBM, Intel, Stripe, Shopify). If any of them appears in the customer's data at MRR < $1K, it's almost certainly a join error — a record from a parent company collapsed onto a record from a tiny customer with the same domain root.

Surface every hit with the full join lineage attached so the user can decide whether it's a real R&D-arm-of-Apple customer or a join bug.

## Unit-mismatch check

For each cluster, if two columns in the same concept group differ by ~1000× on > 100 overlapping records, flag a unit mismatch ($K vs $M or cents vs dollars). The auditor doesn't auto-convert — it surfaces for the human.

## Output

`audit/revenue_findings.json`:

```json
{
  "clusters": [
    {
      "cluster": "probable_mrr",
      "candidate_columns": [
        {"column": "sfdc.Account.MRR__c", "score": 12.0, "fill_rate": 0.94, "rejected": false},
        {"column": "sfdc.Account.Booked_MRR__c", "score": 9.1, "fill_rate": 0.72, "rejected": false},
        {"column": "sfdc.Account.Revenue_Rollup__c", "score": -90.0, "fill_rate": 1.00, "rejected": true, "reject_reason": "do_not_use blocklist — known-zero rollup engagement"},
        {"column": "stripe.subscription.amount", "score": 15.0, "fill_rate": 0.81, "rejected": false}
      ],
      "canonical_pick": "stripe.subscription.amount",
      "runner_up": "sfdc.Account.MRR__c",
      "margin": 2.2,
      "conflict_log": [],
      "apple_at_20_hits": []
    },
    {
      "cluster": "probable_arr",
      ...
    }
  ],
  "unit_mismatches": [],
  "apple_at_20_hits": [
    {"account_name": "Initech R&D", "mrr_column": "sfdc.Account.MRR__c", "value": 20, "verdict": "investigate"}
  ],
  "benfords_law_failures": [
    {"column": "sfdc.Account.Revenue_Rollup__c", "chi_squared_p": 0.00001}
  ]
}
```

This feeds `recommended_engagement_overrides.json:canonical_revenue_picks` at W7.
