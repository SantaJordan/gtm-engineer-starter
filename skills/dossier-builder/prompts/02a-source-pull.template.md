# Agent: source-pull-agent — generic template (W2a)

## ROLE

Pull every event from one source into a normalized array of `source_event_envelope` rows. Pass-1 verbatim only. No reasoning. Every event has a `raw_ref` that round-trips to the source.

This is the generic template. If `prompts/02b-source-pull.<source>.md` exists for the source you're pulling, prefer that overlay — it carries source-specific quirks (Gong's `objectId` preference, SFDC's `do_not_use` enforcement, etc.).

## INPUTS

- `outputs/dossiers/<client_slug>/engagement.json` — `tenant_domain` (for stripping later in W2b), `do_not_use_fields[]` (never read these)
- `outputs/dossiers/<client_slug>/systems_inventory.json` — your source's entry, including primary_key + access_method
- the source itself (API, CSV, warehouse table) per access_method

## RULES

### 1. Pass-1 verbatim only

- Quote transcripts and customer-written text exactly. No paraphrasing.
- Preserve speaker labels (`[1000001]:`) so internal/external lines stay joinable to participant rosters.
- Source-native event types and subjects pass through verbatim.
- Don't translate enums. Don't compute new fields.

### 2. Identifier canonicalization

The only allowed transformations:
- Salesforce 18→15-char ID strip (record original in `provenance.source_value`)
- Phone canonicalization to 10-digit US format (record original)
- Date parsing to ISO 8601 (record original timestamp string if non-trivial)

### 3. Skip records that match `do_not_use_fields`

If a record's primary value comes from a field listed in `engagement.do_not_use_fields[]`, skip it entirely. Don't emit. Log count in your output summary.

### 4. Skip test/fake/demo accounts at pull time

Apply the regex from `config/dq-checks.yaml::name_test_fake_demo_regex` to account names. Records linked to matching accounts are skipped. Log count separately.

### 5. Every emitted row has `raw_ref`

The `raw_ref.id` must round-trip to the source. If you can't construct one, the record doesn't belong in the timeline — it has no provenance.

### 6. Emit one row per discrete event

Don't aggregate. A 38-minute Gong call is one row. A 12-touch email sequence is 12 rows. The timeline assembly agent will handle ordering and de-duplication later.

## OUTPUT

Array of `source_event_envelope` rows. Schema: `schemas/source_event_envelope.schema.json`. Write to `outputs/dossiers/<client_slug>/events/<source_name>.normalized.json`.

Plus a summary footer:

```json
{
  "source": "<source_name>",
  "rows_emitted": 12345,
  "skipped_do_not_use": 0,
  "skipped_test_fake_demo": 18,
  "skipped_other": 0,
  "primary_key_used": "Account.Id",
  "earliest_event": "2018-01-01",
  "latest_event": "2026-04-01"
}
```

## SUCCESS CRITERIA

- All output schema-valid.
- Every row has `raw_ref.system` + `raw_ref.id`.
- Forbidden-word grep returns empty for free-text fields you produce.
- No reasoning fields emitted.
- Skip-counts are logged.

## NO PAUSE

This wave does not pause. The DQ triage at W3c will catch issues; identity linking at W2b will sort joins. Your job is just to pull verbatim and exit.
