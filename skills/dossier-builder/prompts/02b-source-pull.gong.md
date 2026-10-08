# Source-pull overlay: Gong (W2a)

Reads from Gong REST API. Use this overlay on top of `02a-source-pull.template.md`.

## Gong-specific rules

### 1. Use `/v2/calls/extensive` with `contentSelector.context = Extended`

This returns `context[].objects[].objectId` — the account ID Gong's own CRM integration recorded. **This is the authoritative join field.** Do not infer joins from email-domain matching at this stage — that's the linking agent's job.

### 2. Authoritative vs derived foreign-key field — preferences

Two FKs may be available:
- **Native** — `context.objects[].objectId` (or equivalent in `parties[].crmContactId`) returned by Gong's API. Authoritative.
- **Derived** — a downstream warehouse table like `warehouse.enriched_calls.crm_account_id` populated by a fuzzy name-stem match. Frequently wrong (often 40% or more). NEVER use as the join key.

If only the derived FK is available, emit the call with the derived value but mark `provenance.rule = "warehouse_derived_fk_fallback"` so W2b downgrades it.

### 3. Transcripts via `/v2/calls/transcript`

Pull transcripts separately. Cache as one JSON file per call_id at `data/gong_cache/transcripts/{call_id}.json`. Embed `transcript_excerpt` (first 600 chars) in the event row; full transcript stays in cache.

Pacing: 0.35s between calls, batch 100, retry on 429.

### 4. Speaker labels — preserve, and emit the party roster

Preserve verbatim `[party_id]:` prefixes in every `transcript_excerpt`. They're how W2a.5 (Speaker
Roster Enrichment) and W2b can verify external vs internal participants — **never strip or rewrite
them.**

In addition, emit a **per-call party roster** so W2a.5 has the raw material it needs. You already pull
`parties[]` from `/v2/calls/extensive` to compute `n_internal_parties` / `n_external_parties` /
`internal_email_domains` / `external_email_domains`. Do not throw that per-party detail away — emit one
roster row per party, keyed by the same `[party_id]` that prefixes the transcript lines:

```json
"parties": [
  {
    "party_id": "1000001",
    "name": "Jane Rep",
    "email_domain": "contoso.example",
    "gong_affiliation": "Internal",
    "speaker_label": "[1000001]"
  },
  {
    "party_id": "1000002",
    "name": "Sam Buyer",
    "email_domain": "tailspinlaw.example",
    "gong_affiliation": "External",
    "speaker_label": "[1000002]"
  }
]
```

Carry Gong's own `parties[].affiliation` value verbatim into `gong_affiliation` (Internal / External /
Unknown). This is the free metadata the Speaker Attribution Protocol's Step 2 prefers — do not collapse
it into a derived role here; the role label (`company | customer | unknown`) is assigned in W2a.5 so
the decision is auditable and consistent across all of an account's calls.

Pass-1 discipline still applies: this is verbatim extraction of what Gong returned, not inference. You
are copying party metadata + speaker labels through, not deciding who the customer is.

### 4a. Speaker Attribution Protocol — this is the seed for it

The party roster + `[party_id]` prefixes you emit here are the inputs to the **Speaker Attribution
Protocol** (`references/speaker-attribution-protocol.md`). W2a.5 builds the deduped account-level
roster from these per-call party rows; W4a (timeline assembly) then tags each transcript line
`is_customer_voice` + `elicitation` by joining on `[party_id]`. Your only job at this wave is to
preserve the join keys (`party_id` ↔ `[party_id]:` prefix) and the free affiliation metadata losslessly.

### 5. Tenant-domain note

Gong calls store `parties[].emailAddress`. The tenant-domain strip happens in W2b, not here — but flag any call where every external participant has `emailAddress` ending in `engagement.tenant_domain` with `provenance.note = "all_external_emails_are_tenant_domain"`. This is the self-domain-trap signature.

### 6. Drop suspicious-call audit (if applicable)

If an earlier audit for this company published `audit/suspicious_call_ids.json` (a drop-list of calls known to be misattributed), respect it. Skip those `call_id`s and log count.

## Event row shape

```json
{
  "account_local_id": "001Rp00000abc",
  "date": "2025-06-01T12:00:00Z",
  "source": "gong_call",
  "type": "Conference",
  "subject": "Contoso intro call: Sam Buyer",
  "raw_ref": {
    "system": "gong",
    "id": "1000000000000000001",
    "url": "https://us-XXXXX.app.gong.io/call?id=1000000000000000001"
  },
  "fields": {
    "duration_sec": 2700,
    "n_external_parties": 1,
    "n_internal_parties": 2,
    "external_email_domains": ["tailspinlaw.example"],
    "internal_email_domains": ["contoso.example"],
    "transcript_excerpt": "[1000001]: Good morning. This is Jane from...",
    "parties": [
      {"party_id": "1000001", "name": "Jane Rep", "email_domain": "contoso.example", "gong_affiliation": "Internal", "speaker_label": "[1000001]"},
      {"party_id": "1000002", "name": "Sam Buyer", "email_domain": "tailspinlaw.example", "gong_affiliation": "External", "speaker_label": "[1000002]"}
    ]
  },
  "provenance": {
    "rule": "gong_extensive_with_native_fk",
    "source": "gong.api.calls.extensive",
    "source_value": "context.objects[0].objectId=001Rp00000abc"
  }
}
```
