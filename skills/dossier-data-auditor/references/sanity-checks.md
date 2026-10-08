# Sanity checks (W5 fill-rate + W6 cross-field paradox)

Read at the start of W5/W6. These two waves catch problems that single-column or single-table checks miss.

## W5 — Fill rate cohort fitness

For every column the scan-agent classified, compute:
- **fill_rate**: non-null fraction
- **distinct_count**
- **null_count**
- **fill_by_segment**: if any `industry_like` or `geo_like` columns are present, slice fill rate by them

Assign a verdict per column:

| Verdict | Threshold | Meaning |
|---|---|---|
| `cohort_fit` | ≥95% | Critical analyses can use this column safely. |
| `segmentation_fit` | ≥80% | Per-segment analyses work; cohort-wide totals slightly skewed. |
| `caveat_only` | ≥30% | Any analysis using this column must label "based on X% of accounts." |
| `unfit` | <30% | Don't analyze. Don't segment. Don't trust as a join key. |

## W5 silent pass-through

When every column lands `cohort_fit` or `segmentation_fit`, W5 does not render a panel — it returns silent OK. Reduces panel fatigue for clean engagements.

Only when at least one critical-field column lands `unfit` or `caveat_only` does W5 surface a panel. The panel lists offending columns with their fill rates + the verdict + a per-segment fill matrix if relevant.

## W6 — Cross-field paradox detection

This is where data sanity actually lives. Single-field checks (fill, regex, distribution) miss the *pair-wise* inconsistencies that make a record obviously wrong to a human but invisible to a stat.

## The paradox catalog

Five families. Each has worked examples.

### Family 1: `employee_range_revenue_implausible`

The user's load-bearing example: "11–50 employees, $1M revenue" — borderline; "11–50 employees, $100K revenue" — clearly wrong.

Industry-median lookup table per NAICS top-level (`config/audit-checks.yaml#employee_range_revenue_implausible.table`):

```
Size bucket   Revenue range
1-10          ≤ $5M
11-50         $250K – $50M
51-200        $2M – $200M
201-1000      ≥ $10M
1001+         ≥ $50M
```

Rows outside these ranges by >5σ get flagged. Each flag carries a one-sentence explanation:

```
WARN  001XX000003abc..  Acme Roofing LLC   size=11-50, revenue=$95K (3.8σ below median)
WARN  001XX000003def..  Globex Health       size=11-50, revenue=$210M (4.2σ above median for size)
```

The second case isn't necessarily wrong (could be a regional Globex subsidiary with massive Medicare contracts), but it warrants verification.

### Family 2: `industry_revenue_implausible`

When `industry_like` and `revenue_like` are both present:
- For each industry, compute median revenue.
- Flag rows >4σ from their industry's median.

Example: A single-clinic FQHC reporting $500M revenue is almost certainly:
- A wrong-company join, OR
- A multi-clinic system mislabeled as single-clinic, OR
- Total Medicare-program payments confused with FQHC's own revenue.

W6 surfaces these for paradox-resolution via Parallel Task Core ($0.05/row, cited).

### Family 3: `geo_phone_mismatch`

For every row with both `geo_like` (HQ state) and `phone_like` columns:
- Look up the phone's area code.
- Compare to the HQ state.
- If they don't agree on ≥5% of records, the phone field is probably a personal contact (the rep's mobile) not the business HQ.

Cohort-level flag, not per-record. Surfaces in the W6 panel as: "8% of records have phone-area-code state ≠ HQ state. Likely the phone column is a rep's mobile, not the business HQ. Re-source or relabel."

### Family 4: `healthcare_npi_revenue_paradox`

For engagements in the healthcare vertical:
- Row claims to be a healthcare provider (industry: healthcare).
- Has revenue or Medicare-payment claims.
- But no NPI number in any column.

Almost certainly wrong: real provider rows have NPIs. Verify via CMS NPI registry lookup using Serper (the registry is public at npiregistry.cms.hhs.gov).

If the row's claimed account name returns no NPI match in the CMS registry: STRONG signal it's not a real provider. Surface as a paradox for resolution.

### Family 5: `id_format_inconsistent_within_column`

An ID column with mixed formats — SFDC 15-char Ids + SFDC 18-char Ids + a stray 12-char numeric — is almost always the result of partial migrations or import errors.

The audit detects format heterogeneity within a column and flags. Recommended action: normalize to 18-char (SFDC's canonical) before joining.

## Per-record paradox resolution (Parallel Task Core)

For each flagged paradox (cap 50 records / $15 budget per audit):
- Build a Parallel Task Core call with the row's full context + the specific paradox + a structured output schema demanding a citation URL.
- Wait for the response (~30s).
- Verdict: `confirmed_paradox` (the data is genuinely inconsistent — surface for user override or drop) / `resolved_innocent` (citation shows the data is plausibly correct — clear flag) / `unresolvable_via_web` (Parallel can't find sufficient evidence — leave flag, surface to user).

The cited prose from Parallel becomes the audit-trail artifact. Months later, when someone asks "why did we accept this row?", the answer is the Parallel citation URL.

## When the user pushes back

Per `skeptical-voice.md`: when the user says "those are agencies, $1M revenue is fine," the audit doesn't yield. It asks (invented numbers): "Median revenue for 11–50 firms in your dataset is $4M. Your flagged rows are at about $1M. That's 3σ low. What's the evidence those are agencies and not LLC shells?"

If the user can name evidence (e.g., "we manually classified these and they are agencies"), the audit accepts and persists `notes_from_user[]` with the user's reason. If not, the flag stays.

## What goes into the recommended_engagement_overrides

```json
{
  "fill_rate_unfit_columns": ["lead_source_v2", "industry_secondary"],
  "fill_rate_caveat_columns": ["company_revenue_estimate", "employee_count_inferred"],
  "paradox_flags_unresolved": [
    {
      "row_id": "001XX000003def..",
      "paradox": "employee_range_revenue_implausible",
      "values": {"employee_range": "11-50", "revenue": 95000},
      "user_override": null
    }
  ],
  "paradox_flags_with_user_override": [
    {
      "row_id": "001XX000003abc..",
      "paradox": "industry_revenue_implausible",
      "user_reason": "Verified manually — Globex subsidiary with regional Medicare contracts."
    }
  ]
}
```

## What this is NOT

- NOT a tool to fix bad data. The audit flags; the user (or upstream enrichment) fixes.
- NOT industry-aware beyond the NAICS top-level. Sub-industry medians would require either a paid data source or a much larger reference dataset. Out of scope for phase 1.
- NOT a generic anomaly detector. The checks are hand-curated based on common failure modes. Adding new checks is a deliberate design decision, not an ML-discovery step.
