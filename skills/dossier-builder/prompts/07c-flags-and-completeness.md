# Agent: flags-and-completeness-agent (W4c)

## ROLE

Compute paradox flags and the data-completeness score for each dossier. Pass-1 verbatim — flags are mechanical, evidence-required.

## INPUTS

- Per-account dossier (with timeline, structured_signals, linking_trust assembled)
- `config/dq-checks.yaml` (paradox family)

## FLAGS (each is `{id, severity, explanation, evidence}`)

Run all of these per dossier:

| id | trigger | severity |
|---|---|---|
| `mrr_pos_no_calls_180d` | customer mode AND mrr > 0 AND no `gong_call` event in last 180 days | warn |
| `customer_but_cancelled` | outcome=active AND any billing `cancelled` event in timeline | block |
| `cross_source_mrr_mismatch` | revenue.canonical disagrees with another source >10% | warn |
| `bulk_import_tenure_cluster` | this account's `tenure_source = bulk_import_uncertain` AND ≥1% of cohort shares the same `account_created_date` | warn |
| `name_test_fake_demo_regex` | account_name matches `(?i)\b(test\|fake\|demo\|sandbox\|qa\|\[internal\])\b` | block |
| `paradox_zero_engagement` | customer mode AND outcome=active AND total events < 3 in last 365d AND tenure > 90d | warn |
| `linking_trust_low` | linking_trust ∈ {low, indeterminate} | warn |
| `do_not_use_field_consumed` | provenance_index references any field in engagement.do_not_use_fields | block |
| `headline_contradiction` | headline contains both `Cancelled` and `> 0 Gong calls` for an account with cancellation_date < earliest gong_call.date | warn |
| `outcome_unknown` | outcome=unknown after all derivation rules ran | note |

Each flag includes evidence — at minimum the field path or event id that triggered it. No flag without evidence.

## COMPLETENESS SCORE

```python
critical = {
  "email": has_any_contact_with_email,
  "website": has_website,
  "name": has_name,
  "id": has_canonical_id
}
important = {
  "industry": has_industry,
  "company_age": has_account_created_date,
  "revenue": revenue.canonical is not None,
  "last_activity": has_event_within_730d
}
score = (sum(critical) * 2 + sum(important)) / (len(critical) * 2 + len(important))
```

`data_completeness = {critical, important, score: round(score, 2)}`.

## OUTPUT

Embed `flags[]` and `data_completeness` into the per-account dossier JSON.

## SUCCESS CRITERIA

- All output schema-valid.
- Every flag has evidence.
- Score in [0, 1].
- No forbidden words in `explanation` text.
