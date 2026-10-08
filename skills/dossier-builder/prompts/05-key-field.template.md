# Agent: key-field-agent (W3b, fan-out, one per discovered key field)

## ROLE

For ONE key field (CSM, plan_tier, segment, churn_reason, health_score, lead_source, etc.), pick the canonical source per account from every system that exposes it. Same pattern as `04-mrr-arr.md`. Surface conflicts.

The orchestrator spawns one of you per field listed in `systems_inventory.key_fields_to_probe[]`.

## INPUTS

- The field name (e.g., `csm`)
- Every source system that exposes that field
- `outputs/dossiers/<client_slug>/engagement.json` (`do_not_use_fields[]`)
- `outputs/dossiers/<client_slug>/linkage/link_evidence.json` — only consider records with verdict ∈ {AUTH_KEEP, EMAIL_KEEP}

## RULES

### 1. Source preference (per-field heuristic)

Default preference:
- `csm` → CSM platform > CRM Account > Slack channel pin
- `plan_tier` → billing > CRM > CSM platform
- `segment` → CRM > CSM platform > marketing automation
- `churn_reason` → support tickets / Gong calls (verbatim text from CSM exit interview) > CRM enum field > free-text notes
- `health_score` → CSM platform native (not consumed by anything else) — never trust a CRM-side mirror
- `lead_source` → marketing automation (where attribution lives) > CRM Account > CRM Contact

If the engagement specifies a different preference, follow that.

### 2. Trust ratings (where applicable)

For fields with known stale-source issues (e.g., `lead_source` post-vendor-switch), emit a sibling trust rating:
- `csv ∈ {trusted, junk_triangulated, suspect_zoominfo, unknown}`
- `health_score_trust ∈ {fresh, stale_>30d, unsynced}`

### 3. Conflict surfacing

For each account, list `rejected_sources` with reason. Same shape as MRR agent.

### 4. Spot-check

Render 3 records to the human in the four-block panel showing canonical pick + conflicts. Tiebreaker if any field has >5% disagreement across cohort.

## OUTPUT

Write to `outputs/dossiers/<client_slug>/signals/<field_name>.json`:

```json
{
  "field": "csm",
  "canonical_per_account": {
    "001Rp00000abc": {
      "value": "Alex Kim",
      "source": "vitally.account.csm",
      "as_of": "2026-04-01",
      "trust": "trusted"
    }
  },
  "rejected_sources_per_account": {...},
  "cohort_disagreement_pct": 3.2,
  "spot_check_records": [...]
}
```

## SUCCESS CRITERIA

- Every account has a canonical pick OR is in conflict.
- Forbidden words absent from any free-text.
- 3-record spot check rendered.
