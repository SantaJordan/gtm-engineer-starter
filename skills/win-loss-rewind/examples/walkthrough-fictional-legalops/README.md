# Fictional Walkthrough — LegalOps GRC SaaS

This example doubles as the end-to-end test fixture. The synthetic CSV at `customers.csv` describes 20 customers of a fictional GRC SaaS company. It contains **planted ground truth** and a **planted spurious correlation**. At 20 customers the holdout is too small to confirm either (see "Why this fixture is small"), so treat the planted signals as what a larger, real data set should show, not as what this run will prove.

## How to use

```bash
cd <skill-root>   # the directory containing SKILL.md

# 1. Phase 0
python scripts/run_init.py examples/walkthrough-fictional-legalops/customers.csv --adapter csv

# 2. Approve data_confidence.md, then run remaining phases
python scripts/orchestrate.py --run-id <id-from-step-1>

# 3. Score a held-out prospect
python scripts/score_prospect.py acmelegal.example
```

## The fictional company

A GRC SaaS that sells compliance automation. Two distinct customer archetypes in the data:

- **Archetype A — "Multi-state legal-ops shop"** (10 customers, IDs LR-001 through LR-010). Mid-size legal services firms operating in 2+ states. Buy when they expand and the operational compliance load gets unmanageable.
- **Archetype B — "In-house engineering shop with home-grown compliance tooling"** (10 customers, IDs LR-011 through LR-020). Tech-first companies that built their own compliance code and got tired of maintaining it.

## Planted ground truth

These are the discriminators the skill should recover from Phase 5 hypothesis generation + Phase 6 backcasting. Both reflect operationally-specific signals — not lazy-ICP descriptors.

### Archetype A discriminator (the skill should find this)

Won/healthy customers in Archetype A all share:

1. **Filed 2+ state-level professional service registrations in the prior 18 months.** Lost/churned customers in this archetype filed 0–1.
   - Source: state Secretary of State portals (per `references/signal-source-catalog.md` § "State open data portals").
   - Lead time: ~12 months (most recent filing typically lands ~6 months before search begins).
   - Expected lift: ~4x train, ~3.5x holdout.

2. **Posted a "Compliance Officer" or "GRC Lead" or "Director of Compliance" role within 6 months of close.** Lost/churned customers posted no compliance-titled role in the prior 24 months.
   - Source: Firecrawl careers crawl + role-title extraction.
   - Lead time: ~6 months.
   - Expected lift: ~3.5x train, ~3.2x holdout.

The combined recipe: `(state_registrations >= 2 in 18mo) AND (compliance_role_posted in 6mo)` should distinguish Archetype A wins from losses with very high precision.

### Archetype B discriminator (the skill should find this)

Won/healthy customers in Archetype B all share:

1. **Engineering activity dropoff in publicly visible compliance-tagged repos** in the 6–9 months before they entered the market. Specifically: their public GitHub orgs had compliance-related repos that went from ≥1 commit/week to ≤1 commit/quarter.
   - Source: GitHub API on company's public org. Documented in `signal-source-catalog.md` as a "missing source" — TODO to add to enrich_panel.py.
   - Lead time: ~6–9 months.
   - Expected lift: ~5x train, ~4.5x holdout.

2. **At least one engineering leadership LinkedIn job-change** to a non-compliance role in the 12 months before close — i.e., the engineer who was building compliance internally moved on, and no one replaced them on that work.
   - Source: Apify LinkedIn employee history (per `signal-source-catalog.md`).
   - Lead time: ~9 months.
   - Expected lift: ~3.8x train, ~3.5x holdout.

## Planted spurious signal (should DIE at holdout)

A coincidence in the training data: among the 12 won customers, 4 happen to have company names starting with vowels (Apex, Atlas, Echo, Oak). If a hypothesis like "company name starts with a vowel" gets generated and survives initial gates, it should:

- Show inflated lift on the training set due to the coincidence.
- Collapse to lift ≈ 1.0 on the 20% holdout.
- Get killed by the holdout-replication test in Phase 6.

If this signal makes it past Phase 6 into `discriminator.md`, the skill has a bug. (Realistically, the hypothesis-generator's hard gates should also reject "company name starts with a vowel" at Gate 2 — it doesn't prove pain — so the more likely place this gets killed is Phase 5. But Phase 6's holdout test is the safety net.)

## What "good" looks like in the output

`outputs/<run-id>/discriminator.md` should contain (something like):

```markdown
## Archetype: The two-state legal-ops shop with no in-house engineer

To identify a good-fit prospect like this archetype, score on:

| Signal | Source | Field | Threshold | Weight | Lift (train / holdout) |
|--------|--------|-------|-----------|--------|------------------------|
| State professional registrations filed in prior 18mo | state SoS portal | filings.type | >=2 | 0.55 | 4.1 / 3.6 |
| Compliance/GRC role posted on careers page in prior 6mo | Firecrawl careers crawl | careers.role_titles | match keyword | 0.45 | 3.5 / 3.2 |

Combined fit score = 0.55 * I(reg) + 0.45 * I(role). Threshold for "looks-like-archetype": fit_score >= 0.60.
```

And:

```markdown
## Archetype: The in-house engineering shop tired of maintaining home-grown compliance

| ... | ... | ... | ... | ... | ... |
| Compliance-repo commit cadence dropoff | GitHub API | commits/week per repo | drop >80% | 0.60 | 5.0 / 4.4 |
| Compliance-engineer LinkedIn role change in prior 12mo | Apify LinkedIn | employees.history | true | 0.40 | 3.7 / 3.5 |
```

## What "bad" looks like

If the skill ships any of these, it has a bug:

- "Customers in this archetype have higher ARR than non-customers" — circular, ARR is in the input.
- "Customers in this archetype operate in B2B" — Gate 1 violation.
- "Companies whose name starts with a vowel" — should die at holdout (or earlier).
- "Companies that closed in Q4" — close_date is in the input, this is leakage.
- "Companies above 50 employees" — lazy ICP, Gate 1 violation.

## Why this fixture is small

20 customers is enough to:
- Form 2 distinct clusters at Phase 3 (10 members each, well above the 5-member minimum).
- Hold out 2 per cluster (20% of 10). That is below the 5-customer minimum a
  holdout needs (`MIN_HOLDOUT_N` in `scripts/backcast_validate.py`), so Phase 6
  reports every signal as "insufficient evidence" and the run ends with "no
  reliable discriminator found". That is the correct result for 20 customers.
- Exercise every phase end to end. The model and enrichment calls are billed;
  the batch step prints its own estimate before it submits.

This walkthrough has no saved enrichment: its fictional `.example` domains do
not exist online, so live enrichment finds nothing for them. For a run that
recovers a planted signal with no API at all, use the Northwind replay
(`examples/northwind-demo/replay.py` at the repo root), which ships recorded
raw vendor responses for a 300-account cohort.

It is **not** statistically powerful — the 95% CIs will be wide. That's fine; this fixture exists to verify the **mechanics** of the pipeline, not to discover real-world signals.

For real data, you want ≥50 customers per archetype and a 12-month minimum lookback in Gong.

## Expected outcome under time travel (v1.1+) — everything "unverifiable" is CORRECT

The fixture's domains are fictional, so archive.org has no captures of them.
Phase 6's firm pass will therefore mark every hypothesis `unverifiable` (no
[ARCHIVED-BEFORE] or [RECORD-DATED-BEFORE] evidence can exist) and Phase 7
will report "no discriminator found" unless run with `--allow-provisional`.
**That is the leakage protection working, not a regression** — fictional
companies have no provable past. To exercise the full firm path end-to-end,
use `--allow-provisional` for mechanics testing, or run against real domains.
