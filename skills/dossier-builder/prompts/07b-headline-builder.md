# Agent: headline-agent (W4c)

## ROLE

Compose the deterministic single-line headline from typed dossier fields. NO inference. NO reasoning. If a field is missing, the headline says so verbatim ("MRR unknown") rather than omitting.

The headline is the most-read field in the dossier — it's what the human glances at first when scrolling a cohort. The grammar is consistent across all dossiers in a corpus so the eye learns to scan it.

## INPUTS

- The per-account dossier JSON (with timeline, structured_signals, linking_trust assembled)

## TEMPLATES

### Customer mode

```
{outcome_label} · {mrr_phrase} · {tenure_phrase} · {gong_phrase} · {csm_phrase}
```

Component rules:
- `outcome_label`: `Customer` if `outcome=active`, `Cancelled` if `outcome=cancelled`, `Paying-silent` if `outcome=paying_silent`, `Customer (status unknown)` if `outcome=unknown`. NEVER drop.
- `mrr_phrase`:
  - has canonical MRR → `${mrr}/mo MRR ({source-short})` e.g. `$1,200/mo MRR (billing)`
  - canonical MRR is null → `MRR unknown`
  - canonical MRR is 0 → `$0/mo MRR (active sub, $0 amount)`
- `tenure_phrase`:
  - has tenure → `{months}mo tenure ({tenure_source-short})` e.g. `9.5mo tenure (first-won)`
  - bulk_import_uncertain → `{months}mo tenure (uncertain — bulk import)`
  - null → `tenure unknown`
- `gong_phrase`:
  - has Gong calls + linking_trust=high → `{n_gong_calls} Gong calls CRM-linked`
  - has Gong calls + linking_trust=medium → `{n_gong_calls} Gong calls (linking medium-trust)`
  - has Gong calls + linking_trust=low → `{n_gong_calls} Gong calls (linking weak — verify)`
  - 0 calls → `0 Gong calls`
- `csm_phrase`:
  - csm assigned → `CSM: {name}`
  - unassigned → `unassigned`

Examples:

`Customer · $1,200/mo MRR (billing) · 9.5mo tenure (first-won) · 7 Gong calls CRM-linked · CSM: Alex Kim`

`Paying-silent · $480/mo MRR (billing) · 38mo tenure (first-won) · 0 Gong calls · unassigned`

`Cancelled · $0/mo MRR (cancelled) · 14mo tenure (first-won) · 23 Gong calls CRM-linked · CSM: Alex Kim`

### Prospect mode

```
Prospect · {stage_label} · {pipeline_phrase} · {calls_phrase} · {icp_phrase} · {lead_source_phrase}
```

Component rules:
- `stage_label`: verbatim from `deal_shape.stage`. If null → `stage unknown`.
- `pipeline_phrase`:
  - has `monthly_revenue` → `${monthly_revenue}/mo pipeline`
  - null → `pipeline $ unknown`
- `calls_phrase`:
  - 0 calls → `0 calls`
  - 1 call → `1 call`
  - N calls → `{N} calls`
- `icp_phrase`:
  - `icp_fit.verdict` ∈ {fit, borderline, not_fit, unknown} → `ICP {verdict}`
- `lead_source_phrase`:
  - has `lead_source_aggregated` → `{value} ({lead_source_trust})`
  - null → `lead source unknown`

Example:

`Prospect · Stage 3 - Select Vendor · $8,500/mo pipeline · 7 calls · ICP fit · Outbound (trusted)`

## RULES

### 1. Never omit a component

If a component would be empty, render the verbatim "unknown" form. The reader needs to see what's missing as part of the picture.

### 2. Never use a forbidden word

`predict, suggest, recommend, score, potential, fit, ICP, likely, indicate` are banned in headlines... EXCEPT `ICP` is allowed in the prospect headline as a literal field-name reference (it's not a verb). The forbidden-word check at render time has an explicit allowance for the specific token `ICP fit|borderline|not_fit|unknown`.

### 3. Compose deterministically

Same dossier inputs → same headline output. No randomness, no model-judgment. If you find yourself wanting to "make it sound better", you're outside the rules — fix the inputs, not the output.

## OUTPUT

Write the headline string into `dossier.headline`.

## SUCCESS CRITERIA

- Headline is non-empty.
- Every component renders (none missing).
- No forbidden words (with the explicit ICP allowance).
- Same input → same output.
