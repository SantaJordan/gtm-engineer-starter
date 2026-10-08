# Source-pull overlay: Salesforce (W2a)

Use on top of `02a-source-pull.template.md`. Salesforce is almost always the `primary_id_system`.

## SFDC-specific rules

### 1. Respect `engagement.do_not_use_fields[]` strictly

Common ones to NEVER read (verify per-engagement):
- `Opportunity.Amount` — deal value, not MRR. Use `Opportunity.Monthly_Revenue__c` (pipeline$) or `Opportunity.Incremental_MRR__c` (bookings$).
- `Account.Revenue_Rollup__c` (if engagement says so) — often always-zero.
- `Contract.*` — frequently deprecated in favor of SBQQ subscriptions.

If the engagement file marks a field do-not-use, do not query it. Period.

### 2. Probe optional fields with `describe()` at runtime

For any field listed in source-registry as `optional`, run `sf.describe()` against the relevant object first. If the field doesn't exist in this org, silently skip rather than raising `INVALID_FIELD`. Log to summary footer.

### 3. Pull these objects (customer mode)

- `Account` — name, type, industry, billing fields, custom MRR/ARR fields, all `*_trust` siblings, segmentation fields
- `Opportunity` — close-won opps for tenure derivation (`MIN(CloseDate WHERE IsWon)`); current open opps for prospect mode
- `OpportunityHistory` — stage transitions for the timeline
- `AccountHistory` — material changes (Type, Status, custom enums)
- `Contact` — primary contact + persona-relevant custom fields
- `Case` — open + closed in last N (configurable, default 730 days)
- `Task` — emails, calls, follow-ups; flag `is_gong_mirror` where `Task.CallObject == gong.call_id` (dedup at timeline assembly)
- `Event` — meetings; same Gong-mirror flag rule
- `EmailMessage` — RelatedToId-linked
- `SBQQ__Subscription__c` — active + past subscriptions for revenue + tenure (preferred over Contract)

### 4. Pull these objects (prospect mode — strips post-sale)

Same as above except:
- Skip `Case`, `Contract`, `SBQQ__Subscription__c`
- Add prospect-specific `Account.engagio__highintentkeywords_longtext__c`, `Last_Click_Campaign__c`, `Last_Touch_Sequence__c`, persona/MGE/MQL date fields on Contact

### 5. Identifier canonicalization

Strip all 18-char Account/Contact/Opportunity IDs to 15-char form for joining; record original 18-char in `provenance.source_value`.

### 6. Skip test/fake/demo accounts

Apply name-regex from `config/dq-checks.yaml`. Default: `(?i)\b(test|fake|demo|sandbox|qa|\[internal\])\b`. Account match → skip account + all related objects.

### 7. Skip Gong-mirror tasks at TIMELINE level (not pull level)

`Task.CallObject` matches a Gong `call_id` → emit, but flag `is_gong_mirror=true` in fields. Timeline-assembly agent dedupes at W4a; you preserve the full record because the SFDC mirror often has Outreach attribution metadata Gong doesn't have.

## Event row shape (per object)

Multiple sources, one schema. `source` field is the discriminator:
- `sfdc_task`, `sfdc_event`, `sfdc_email`, `case`, `subscription`, `contract`, `opportunity_created`, `stage_transition`, `account_created`

Each carries the SFDC object's verbatim fields under `fields`, with the SFDC ID as `raw_ref.id`.
