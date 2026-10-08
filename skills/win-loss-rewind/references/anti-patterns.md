# Anti-Patterns — Findings to Reject

Patterns the skill must auto-reject. These are the failure modes that show up in nearly every retrospective deal-correlation analysis — they look like findings but aren't. Never ship.

## The cardinal sin: generic-SaaS findings

If a finding could equally describe any B2B SaaS company, it's not a finding. It's noise.

Examples that auto-fail:

- "Customers who churned stopped using the product before churning."
- "Customers who renewed used the product more than customers who churned."
- "Won deals had higher engagement than lost deals."
- "Champions who left the company correlated with churn."
- "Customers who hit 3+ stakeholders on calls renewed at higher rates."

These are tautologies dressed up as analysis. They tell the user nothing they can act on. The skill's purpose is to find *non-obvious* signal combinations specific to this customer set, in this archetype, with operationally observable precursors. If a finding survives Phase 6 lift testing but reads like one of the above, it's an artifact — not a discriminator.

## Lazy ICP descriptors

The whole point of the skill is to break these. Reject any archetype name or signal that reduces to:

- Employee count band ("50–1000 employees")
- Industry vertical alone ("B2B SaaS")
- Funding stage ("Series A through C")
- Geography alone ("North American companies")
- Generic adjectives ("growth-stage", "innovative", "modern", "tech-forward", "scaling")
- Department presence ("companies with sales teams", "companies with HR")

If a hypothesis from Phase 5 reads like the above, it failed Gate 1 of `prompts/hard-gates.md`. Kill it.

## Banned signal types (Gate 2)

Activity ≠ pain. These are auto-fail:

| Banned signal | Why it fails |
|---------------|--------------|
| Recently raised funding | Thousands of companies raise. No pain implied. |
| Hiring for [role type] | All scaling companies hire. |
| Growing headcount | Growth, not pain. |
| Expanding to new markets | Strategic, not pain. |
| M&A activity | Business development, not pain. |
| Job postings | Normal operation. |
| Tech stack includes [tool] | Use ≠ problem. |
| Company is [size] | Size alone proves nothing. |
| Industry is [sector] | Being in sector ≠ pain. |

The fix: signals must directly imply the pain, not the company's general activity level.

## Confounded signals

Signals that correlate with outcome only because of a third variable. Common cases:

### Pipeline-stage confounding
```
"Customers who reached the proposal stage closed at higher rates"
```
The proposal stage *is* close-correlation, not a discriminator. This is data trivia.

### CRM-hygiene confounding
```
"Customers with detailed CRM notes renewed more often"
```
Sloppy notes are the marker of an absent CSM — not the customer's fault. The CSM is the variable, not the customer.

### Sample-selection confounding
See `lift-and-holdout.md` § Selection effect. Common in B2B data: missing the day-7-churn cohort makes everything look better.

### Time-period confounding
Signal computed on data that was overwritten post-close. See `lift-and-holdout.md` § Outcome leakage.

## RETRO-ASSUMED-only findings (the hindsight leak)

```
"The customer's site had a compliance page before they bought"
   — checked only against TODAY'S site
```

A backcast claim whose only evidence is today's web is not a finding about the
past — it's today's web wearing a costume. Every pre-close claim must carry a
visibility tag (`references/time-travel.md`); a signal supported ONLY by
[RETRO-ASSUMED] cells never ships as firm, no matter how strong its
provisional lift looks. Phase 6 enforces this mechanically; if you see an
allegedly historical claim with no [ARCHIVED-BEFORE] capture and no
[RECORD-DATED-BEFORE] record behind it, it's this anti-pattern.

## Vague-window findings (near-worthless dating)

```
"They added the compliance role sometime between 2022 and 2024"
```

A signal dated only to a multi-year range gives a sales team nothing to act
on — the strike window is the deliverable. Findings must carry a bracket-dated
knowable-by window (absent in capture A, present in capture B → appeared in
(A, B]) and a strike window in days. If the archive can't bracket it tighter
than ~a year, report it as weak dating honestly and let the Cynical Buyer's
temporal-firmness axis price it accordingly.

## Correlation with no stated mechanism

A signal that survives lift but where nobody can state the causal chain —
pain → visible exhaust → detection, with the delay at each hop — is a
correlation wearing a lab coat. The Phase 6.7 knowability agent must be able
to write that chain; "the number went up together" is not a mechanism. Signals
with `knowability: cant_know` on the load-bearing rubric entry should not
anchor a discriminator.

## Aggregates as insights (Gate 3)

```
"Industry average is 12% margin"
"Companies in your space face compliance challenges"
"73% of sales teams use a CRM"
```

Auto-fail. These describe industries, not entities. The skill's job is per-company observability. Aggregates are allowed only as comparison points alongside a specific company's data ("Your facility's 14 violations vs county median of 8") — but they cannot be the insight.

## Generic narrative findings

In Phase 4 themes, these generic phrases are the writing-style equivalent of generic findings. Auto-flag with `anti_pattern_flag` and exclude from Phase 5 hypothesis input:

- "Customer wanted a better solution"
- "Customer felt the previous tool was inadequate"
- "Customer was looking to scale"
- "Customer needed a partner not a vendor"
- "Customer experienced operational pain points"
- "The buyer prioritized ROI"

If the only material from a customer's calls is generic like the above, the customer's `extraction_confidence` is too low to inform Phase 5. Exclude — don't let one bad customer write hypotheses for the archetype.

## What good looks like

Strong findings that survive these filters (invented examples; the lifts show the shape, not real results):

- "Customers in Archetype 2 (multi-state insurance brokers) all filed at least one new state appointment in the 6 months before close. Lift 4.2x train / 3.8x holdout. Source: state insurance department directories. Anti-match: brokers filing new appointments to *exit* a state — distinguished by directionality of appointments."
- "Customers in Archetype 5 (regional health systems) all had a CMS star rating *drop* in the 12 months before search. Lift 5.1x / 4.7x. Source: CMS Care Compare API, historical snapshots. Anti-match: systems with rating drops who already had a competing solution — flagged in Phase 4 themes."
- "Customers in Archetype 1 (post-SOC2 SaaS) had a careers page that added a 'Compliance' or 'GRC' role within 90 days of starting their search. Lift 3.6x / 3.4x. Source: Firecrawl careers crawl + role title extraction. Anti-match: companies adding the role to fill it from inside (no external posting)."

What makes these good: per-company observable, sourced from real APIs, specific lead times, explicit anti-matches, holdout-replicating lift.
