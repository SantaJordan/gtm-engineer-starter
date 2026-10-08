# Canonical dossier schema

Read at W4 before timeline assembly. The JSON Schema files in `schemas/dossier.{customer,prospect}.schema.json` are authoritative; this file explains the shape narratively.

## Top-level keys (both modes)

```
account_id, schema_version, rebuilt_at, mode,
account_name, account_name_provenance, outcome,
headline, summary,
timeline[], structured_signals,
linking_trust, data_completeness, flags,
provenance_index, notes_from_revops
```

## What each key is for

- **`account_id`** — canonical primary-id-system value. Every dossier is named `{account_id}.json`.
- **`schema_version`** — `dossier-builder/1.0.0`. Bump on breaking changes; downstream readers gate on this.
- **`rebuilt_at`** — ISO 8601, set at W6 render time.
- **`mode`** — `customer` or `prospect`. Schema branches here.
- **`account_name`** + **`account_name_provenance`** — name plus where it came from. Provenance pattern repeats for every visible field via `provenance_index`.
- **`outcome`** — mode-specific enum (see `config/outcome-enums.yaml`).
- **`headline`** — deterministic single-line summary string. Composed at W4c by `headline-agent` using only typed fields. Missing fields are spelled out verbatim ("MRR unknown") rather than omitted.
- **`summary`** — three-bullet narrative summary. Pass-1 verbatim — no inference. The headline is mechanical; the summary names the shape ("paying but silent customer", "open prospect with multi-stakeholder traction", etc.) without crossing into reasoning.
- **`timeline[]`** — sorted array of `source_event_envelope` rows. The single chronological spine. Every event has `{date, source, type, subject, raw_ref, fields, provenance}`.
- **`structured_signals`** — mode-specific aggregates. See below.
- **`linking_trust`** — `{verdict, basis, verified_by?, verified_at?}`. Set at W2b.
- **`data_completeness`** — fill-rate snapshot at this account. Critical fields (>95% target), important fields (>80% target), score 0..1.
- **`flags[]`** — paradox flags from W4c. Each is `{id, severity, explanation, evidence}`.
- **`provenance_index`** — map of `field_path` → `{value, rule, source, raw_ref}` for every visible field. The render uses this to attach provenance chips.
- **`notes_from_revops[]`** — array of `tiebreaker` records. Every human decision captured in a four-block panel persists here. Append-only audit trail.

## Customer-mode `structured_signals`

```
mrr_snapshots[], arr_now, tenure_days, tenure_source,
cases_open, cases_total, subscription_state, contracts[],
churn_signals[], csm, plan_tier, health_score
```

Notes:
- `mrr_snapshots[]` is an array because revenue moves over time. Each snapshot has `{as_of, amount, source, breakdown_by_product?}`. Display the most recent in the headline, but keep history.
- `tenure_source` is an enum from a cascade — `opportunity_won` > `opportunity_any` > `account_created` > `billing_first_charge`. `bulk_import_uncertain` is a sentinel the flags-agent picks up.
- `churn_signals[]` is verbatim signal labels from source systems (e.g. `csm_flagged_at_risk`, `mrr_decline_3mo_>10pct`). Pass-1 doesn't compute new signals; it surfaces what sources expose.

## Prospect-mode `structured_signals`

```
t_anchor, lead_attribution, campaign_attribution, deal_shape,
funnel_position, icp_fit, pre_first_call_evidence[], post_first_call_evidence[],
n_calls_so_far, multi_stakeholder
```

Notes:
- `t_anchor` is REQUIRED. Anti-leakage filtering depends on it.
- `lead_attribution.lead_source_trust` is the trust rating sibling — never trust the raw lead source without this.
- `deal_shape.monthly_revenue` is pipeline-$. `deal_shape.incremental_mrr` is bookings-$. Never use a CRM `Opportunity.Amount`-style field — that's deal value, not MRR.
- `pre_first_call_evidence[]` and `post_first_call_evidence[]` are projections of `timeline[]` — every event in one or the other, none dropped, none in both.
- `icp_fit.basis` should cite the W3c web sample if the account was in it.

## Headline format (deterministic)

Customer mode:
```
{Customer|Cancelled|Paying-silent} · ${mrr}/mo MRR · {tenure_months}mo tenure ({tenure_source}) · {n_gong_calls} Gong calls {linking_phrase} · {csm_or_unassigned}
```

Prospect mode:
```
Prospect · {stage} · ${monthly_revenue}/mo pipeline · {n_calls_so_far} call{s} · ICP {fit|borderline|not_fit} · {lead_source_aggregated} ({lead_source_trust})
```

Missing fields render verbatim: `MRR unknown`, `tenure unknown`, `0 Gong calls`, `unassigned`. Never omit. The reader needs to see what's missing as part of the picture.

## Things that are NOT in the dossier

- No predicted churn risk. (Pass-2.)
- No "next best action" recommendation. (Pass-2.)
- No competitive analysis. (Pass-2.)
- No fit score. (Pass-2.)
- No win-probability number. (Pass-2.)

If you find yourself wanting one of these, it belongs in a separate skill that reads dossiers as input.
