# Verbatim discipline

The pass-1/pass-2 split is enforced through a small set of rules every source-pull agent and downstream agent must follow. Read before spawning any agent that produces dossier-ready output.

## Forbidden words (config/forbidden-words.yaml)

These words are not permitted in any pass-1 dossier output outside `notes_from_revops`:

```
predict, suggest, recommend, score, potential,
fit, ICP, likely, indicate
```

Also: any phrase that frames an event as a prediction or recommendation:
`will probably`, `is likely to`, `should consider`, `may want to`, `we recommend`, `the best next step`, `at risk of`.

If an agent finds itself wanting to use one of these words, the right action is almost always to describe the underlying observation more concretely. Instead of "likely churn risk" → "MRR declined 30% over the last 90 days; the CSM flagged 'at risk' last month".

## Verbatim quoting from sources

When a dossier includes a transcript snippet, support-ticket text, or sales-note paragraph, it must appear **exactly** as it does in the source — same words, same punctuation, same speaker labels. The render `provenance_index` carries the `raw_ref` so the chip expands to the exact source value (truncated to 240 chars).

If the model would normally summarize, paraphrase, or "clean up" the quote, it must instead truncate with `…` and emit the original. A spot-check on 30 random quotes per dossier batch is recommended in the v6-shape replay validation; one fabrication = batch fails.

## Source-system field passthrough

Pass-1 source-pull agents emit raw source fields verbatim under `fields`. Don't normalize, don't translate enums, don't compute derived values. The source's own `Stage_Develop_in__c` lands as `Stage_Develop_in__c`, not as `stage_one_entered_date` — that translation is the job of W4b/W4c with explicit provenance.

The exception: identifiers may be canonicalized (Salesforce 18→15-char strip, phone normalization). Every canonicalization records the original in `provenance.source_value`.

## What pass-1 IS allowed to do

- Filter records by verdict (drop `AUTH_REJECT`/`EMAIL_REJECT`).
- Sort timelines by date.
- Compute deterministic derived fields when explicitly defined (the `headline` template, `flags` from `mrr_pos_no_calls_180d`, etc.).
- Strip the tenant domain from email-join inputs.
- Pick a canonical source for a conflicted field via the rules in `04-mrr-arr.md` and `05-key-field.template.md` — and surface every rejected source.

## What pass-1 IS NOT allowed to do

- Reason about why an account is in the state it's in.
- Score risk, fit, or any other rating.
- Recommend a next step.
- Predict an outcome.
- Combine fields to infer a new fact (e.g., compute `engagement_score = ...`).
- Hide a contradiction. (Surface it via `flags[]` instead.)

## Why this matters

Pass-1 dossiers are a durable artifact consumed by many downstream Pass-2 jobs over weeks or months. If reasoning bleeds in, every dossier becomes invalidated whenever the reasoning prompt changes. By keeping pass-1 strictly descriptive:

- Dossiers are stable across reasoning-prompt iterations.
- Multiple Pass-2 skills can score the same dossier differently without rebuilding.
- The forbidden-word check at render time provides a cheap, mechanical guardrail against drift.
- Auditors can verify dossier facts against source systems without LLM context.

## Render-time enforcement

`scripts/render_html.py` greps the rendered HTML for any forbidden word outside the `notes_from_revops` block. Hits fail the render with a non-zero exit code and a list of offending field paths.

Don't disable this check. If you find yourself needing to disable it, the agent prompt is broken — fix the prompt, not the check.
