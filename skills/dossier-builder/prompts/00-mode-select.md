# Wave: W0 — Mode + Engagement Context

This wave runs in the main thread (no sub-agent). Capture the engagement parameters that every downstream agent depends on.

## What to ask the human (using AskUserQuestion-style multi-choice where possible)

1. **Mode** — `customer` (post-sale dossiers) or `prospect` (pre-customer dossiers).
2. **Client slug** — short kebab-case identifier for `outputs/dossiers/<client_slug>/`. The skill itself never imports any client name; this is the only place it lands.
3. **Tenant domain** — the customer's own email domain (e.g., `contoso.example`, `northwind.example`). Used by every downstream agent to strip self-domain false-matches. **Mandatory; can be a list if the customer has multiple.**
4. **Anchor rules** (prospect mode only) — for `t_anchor`: default is `first_call_date OR opp_created_date`. Confirm or override.
5. **`do_not_use_fields[]`** — fields RevOps already knows are bad. Capture as `[{field_path, reason}]`. Common examples: SFDC `Opportunity.Amount` (deal value, not MRR), CRM `Account.Revenue_Rollup__c` (always zero), `LeadSource = ZoomInfo` (stale post-vendor-switch).
6. **Privacy flags** — anything to redact in the methodology notes at W6 (specific account names, vertical, exec names).

## Output

Write `outputs/dossiers/<client_slug>/engagement.json`:

```json
{
  "mode": "customer",
  "client_slug": "acme-2026q2",
  "tenant_domain": ["acme.example"],
  "primary_id_system": null,
  "anchor_rules": null,
  "do_not_use_fields": [
    {"field_path": "sfdc.Opportunity.Amount", "reason": "deal value, not MRR — per RevOps"}
  ],
  "privacy_flags": {
    "redact_account_names_in_methodology_notes": true,
    "redact_exec_names_in_methodology_notes": true
  },
  "created_at": "2026-05-01T..."
}
```

`primary_id_system` stays `null` until W1a discovers it.

## Validation

Run `scripts/validate_schema.py --no-client-leakage --skill-path .` immediately. This greps the skill's own files (SKILL.md, references/, prompts/, schemas/, config/) for any token in `client_slug` or `tenant_domain`. Hits BLOCK — they mean an earlier run leaked company-specific data into the skill itself, and we need to fix that before continuing.

## Pause and confirm

Print the captured engagement.json back to the human in the four-block panel format. They confirm or correct. Tiebreakers persist into `notes_from_revops[]`.

Once confirmed, proceed to W1a.
