# ID system archaeology (W2.5)

> Loaded by `id-system-reconciler` agent. The job: prove or disprove the customer's claimed primary ID system per object class. Real RevOps environments have 3-7 competing primary ID systems and the one RevOps will name in W0 is frequently not the one their data actually pivots on.

## Why this wave exists

The auditor's W0 captures `claimed_primary_id_system` from the user. Naive auditors stop there. A robust auditor *tests the claim* before allowing W3 join inference to proceed.

A common failure mode (invented example): Head of RevOps says "Salesforce is our system of record." The audit finds that about half of the churned accounts have `Account.Id = NULL`, because they are tracked by `External_Id__c` on records migrated from another CRM. The claim was sincere but wrong, and accepting it would have poisoned every downstream join.

## What the W2.5 agent does

1. **Inventory every ID-shaped field across every table.** Pull from `scan/column_inventory.json` — the column-dictionary classifier already tagged ID-like columns and sub-classified them by format pattern (SFDC 15-char, SFDC 18-char, Stripe `cus_*`, HubSpot integer, NPI, DOT, DUNS, EIN).

2. **Build the bipartite link graph.** For each pair (system_A, system_B), what column in A purports to link to system B, and at what fill_rate × resolution_rate (% of non-null values that resolve to a valid row in B)?

3. **Score each candidate primary ID system** by:
   - **In-degree**: number of other systems pointing at this one's IDs.
   - **Universe coverage**: % of accounts (across all sources) for whom this system has a record.
   - **Stability**: no two rows share an ID; no NULL rows that should have IDs.
   - **Format validity**: SFDC 15-char IDs validate against the case-sensitive checksum that the 18-char extension provides; HubSpot IDs are integers; Stripe IDs are `cus_*`. Format violations indicate downstream import errors.

4. **Detect dual-canonical conflict.** Common pattern: SFDC is canonical for accounts, Stripe is canonical for paying customers. Some accounts exist in SFDC but not Stripe (prospects/lost). Some Stripe customers don't have an SFDC link (self-serve sign-ups). The agent surfaces this as a real architectural state, not a bug.

5. **Detect ghost IDs**:
   - SFDC 15-char IDs that don't validate against the case-sensitive checksum.
   - HubSpot integer IDs that look like SFDC IDs (cross-paste errors).
   - Numeric IDs in scientific notation (`1.23456E+19`) from Excel roundtrip.

6. **Detect ID-on-wrong-object errors.** `Contact.Account.Id` populated with what looks like an `Opportunity.Id` (different format). Common integration-tool misconfiguration symptom.

7. **Emit per-object canonical recommendation** with confidence:
   - `accounts: { canonical: 'sfdc.Account.Id', confidence: 'high', alternatives: ['stripe.Customer.id (paying subset, 70% coverage)'], evidence: {...} }`
   - `contacts: { canonical: 'sfdc.Contact.Id', confidence: 'medium', alternatives: ['legacy_crm.contact_id (migrated records, 40% coverage, last-modified-cluster <migration date>)'], evidence: {...} }`
   - `subscriptions: { canonical: 'stripe.Customer.id', confidence: 'high', alternatives: [], evidence: {...} }`
   - And so on per object class.

## The gate

Hard refusal to proceed to W3 if:
- Recommended `primary_id_system` differs from `claimed_primary_id_system` AND
- Confidence is `high` AND
- User has not explicitly accepted the recommendation or written an override into `notes_from_user[]`.

This forces the conversation. Silent acceptance of a wrong claim costs the live call.

## Output schema

`id_system_audit.json`:

```json
{
  "claimed_primary_id_system": "sfdc",
  "recommended_primary_id_system": "sfdc",
  "agreement_with_claim": true,
  "confidence": "high",
  "per_object_canonical": {
    "accounts": {
      "canonical": "sfdc.Account.Id",
      "confidence": "high",
      "alternatives": [],
      "evidence": {
        "in_degree": 5,
        "universe_coverage_pct": 0.94,
        "stability_score": 0.99,
        "format_validity_pct": 0.99
      }
    },
    "contacts": {...},
    "deals_opportunities": {...},
    "subscriptions": {...},
    "users_in_product": {...},
    "support_orgs": {...}
  },
  "id_chain_health": [
    {
      "from": "gong.call.account_id",
      "to": "sfdc.Account.Id",
      "fill_rate": 0.94,
      "resolution_rate": 0.6,
      "verdict": "broken_join_rate",
      "diagnosis": "Derived FK from Gong-SFDC integration; matches the ~40%-wrong-FK class. Investigate integration mapping rules."
    },
    {
      "from": "stripe.Customer.metadata.sfdc_id",
      "to": "sfdc.Account.Id",
      "fill_rate": 0.8,
      "resolution_rate": 0.99,
      "verdict": "healthy_but_partial_coverage",
      "diagnosis": "20% gap concentrated in customers created before <migration date>; likely from before a billing-system migration."
    }
  ],
  "dual_canonical_zones": [
    {
      "object": "paying_customers",
      "sfdc_only_count": 12,
      "stripe_only_count": 850,
      "both_count": 3400,
      "interpretation": "Stripe is canonical for the paying universe; SFDC is canonical for the sales universe. Treat as dual."
    }
  ],
  "ghost_id_findings": [
    {"field": "sfdc.Account.Id", "ghost_pattern": "scientific_notation", "count": 14}
  ],
  "id_on_wrong_object": [],
  "agent_notes": "..."
}
```

This file feeds `recommended_engagement_overrides.json:per_object_canonical_id` and `dual_canonical_zones` at W7. The dossier-builder's W0 reads these (once its `prompts/00-mode-select.md` is updated) and pre-fills the new structured `primary_id_system` shape, with the flat string preserved for back-compat.
