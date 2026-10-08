# Critique trio rubric

Three independent perspectives run in parallel at W5. Each gets its own context window. None sees the others' output until the orchestrator unifies them. Read at W5 before spawning.

The model for this is Claude Code's plan-mode three-perspective auto-review: independent eyes catch things a single reviewer misses because they're optimizing for different qualities.

## Perspective 1: Statistician

Lives in the cohort. Looks at distributions, ratios, outliers, fill rates, temporal clusters. Does not look at individual narratives.

Allowed flags:
- distributional anomalies (`MRR p99/median > 50×`, suspicious round-number clusters, ARR slope spikes)
- fill-rate violations (any critical field below 95% across the cohort)
- temporal clusters (>5% of accounts share an identical date — bulk-import fingerprint)
- variance issues (a "key field" that has near-zero variance is probably a populated default, not real data)
- correlation collapses (two signals that are supposed to be independent showing >85% Jaccard agreement on flagged accounts)

Output: list of `{statistic, observed, expected, severity, evidence_record_ids[]}`.

Forbidden: per-account narrative judgments. Forbidden: predicting outcomes. Forbidden: recommendations.

## Perspective 2: RevOps practitioner

Lives in the business operations reality. Looks at whether the dossier matches how a real RevOps team would actually run.

Allowed flags:
- field meanings that don't match real-world business semantics (e.g. CRM `Amount` used for MRR — that's deal value, not recurring)
- segmentation that contradicts how the company actually goes to market
- missing fields a QBR would expect
- cohort definitions that include out-of-ICP accounts
- churn-driver categories that don't match what CSMs actually report
- accounts whose `outcome` value contradicts a billing-system signal in the same dossier
- "would I trust this in a QBR?" — overall coherence question

Output: list of `{coherence_issue, observed, expected, severity, evidence_record_ids[]}`.

Forbidden: scoring fit/risk. Forbidden: recommendations. Forbidden: claiming knowledge of the customer's business beyond what's in the dossier.

## Perspective 3: Outlier hunter

Lives at the extremes. Surfaces the 5 most surprising records in the cohort and either defends each or flags it.

Allowed flags:
- the highest-MRR account that has zero Gong calls
- the lowest-MRR account that has more Gong calls than median
- the account with the longest tenure that has no signs of recent activity
- the most-churned vertical's healthiest account
- the youngest account that's already classified `paying_silent`
- any account where the `headline` reads like a contradiction (`Customer · $0/mo MRR · 7 Gong calls`)

For each: either defend ("this is plausible because the dossier shows X — see record_id") or flag ("this is suspicious because the dossier shows X but the joining method is fuzzy — see linkage").

Output: 5 records, each `{record_id, headline, defense_or_flag, severity, evidence}`. Always exactly 5 — pick the most surprising even if all 5 turn out to be plausible.

Forbidden: scoring. Forbidden: recommendations. Forbidden: claims about prediction.

## Unification

The orchestrator merges the three lists into `must_fix_before_ship.json`:

```
{
  "must_fix": [
    {
      "from": "statistician|revops|outlier_hunter",
      "issue": "...",
      "severity": "block|warn|note",
      "evidence": [...],
      "suggested_action": "..."
    }
  ]
}
```

`severity = block` items halt W6 render until the human resolves them in the four-block panel. `warn` and `note` items are surfaced for the human's awareness but don't block.

## Severity rubric (consistent across all three)

- **block** — corpus is wrong in a way that would mislead any downstream user. Examples: cohort fill rate < 95% on a critical field, MRR field consumed from a Do-Not-Use source, >5% of records have `INDETERMINATE` linking, any forbidden word in any rendered field.
- **warn** — flag worth surfacing but the dossier can ship if the human says "noted, fine for now". Examples: distributional outlier on a single record, RevOps coherence quibble on a single account, fuzzy-join fraction 22% (just over the 20% target).
- **note** — informational. Logged in `critique/<perspective>.json` but not surfaced in the four-block panel. Examples: a flag that's purely descriptive, a sub-cohort observation.

## What this is NOT

This is not a Pass-2 review. The critics describe what's in the dossier; they do not score it. The Pass-2 skill (separate downstream) is where actual analysis lives.

## Perspective 4: Reader (blind-retell traversability — added v1.1)

Lives in the artifact alone. A stand-in for the model or rep who will traverse the dossier cold.
Receives NO build context, NO other critiques, NO engagement config — file paths only. For each
sampled dossier it retells the customer's story in ≤5 sentences BEFORE critiquing (fresh-eyes
confusion captured, not rationalized), then lists rep questions the dossier cannot answer, counts
noise events, and checks the HTML renders what the JSON holds.

Allowed flags:
- story unrecoverable or actively misleading from the artifact alone
- real signal drowned by repeated/sentinel/empty/code-only rows (report noise share)
- reader cannot tell who the human is or how to reach them
- headline counts the timeline cannot support (empty voicemails counted as calls; future
  registrations counted as attendance)
- date/label disagreement (final status on a start-dated row; end-before-start spans)
- render drops fields the JSON carries (a collapsed raw-JSON dump is not "rendered")

Output: retells + per-dossier `{unanswerable[], contradictions[], noise_share, render_losses[]}`
+ corpus verdict `{stories_recoverable, stories_blocked, median_noise_share, traversability}`.

Forbidden: correctness-vs-source disputes (statistician/RevOps), business semantics (RevOps),
cohort-context defenses (outlier-hunter), scores/recommendations (not pass-1).

Fail rule: traversability = fail when >10% of the sample is blocked or median noise share > 0.5 —
and a fail blocks render like any `block` finding. After fixes: re-run on the SAME sample ids,
report before/after. Improvement is measured, never asserted.

Origin: at a █████ build, the statistician reproduced every corpus aggregate exactly while
timelines were unreadable walls of near-identical cancellation rows; the human caught it in a
five-minute skim of one dossier. Data-correctness critics do not catch legibility failures.
