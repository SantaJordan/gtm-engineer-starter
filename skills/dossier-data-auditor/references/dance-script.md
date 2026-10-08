# The dance script — what to ask and what NOT to ask

> Loaded at W0. The audit is interactive. 8-11 potential pauses; most silent unless interesting. This file enumerates per-wave: what to ask, what gold-standard answers earn trust, and what mistakes are insulting and erode it.

## Tone principles

1. **Specificity beats vagueness.** "I see Account.Revenue_Rollup__c at 95% fill but the distribution is uniform 0-10,000, which looks like a placeholder, not actual revenue. Confirm we shouldn't use this?" beats "Is this column trustworthy?"
2. **Show before asking.** Render the data point first (a 5-row sample, a count, a histogram), then ask. Cold questions feel like interrogation.
3. **Never ask abstract preferences.** "Should we use ParentId?" is wrong. "I see 4,000 accounts with ParentId set and 1,000 with Ultimate_Parent__c also set. They disagree on 80 accounts. Show me the disagreement set?" is right.
4. **Acknowledge institutional memory.** When the user mentions a recent migration, integration swap, or RevOps person who left, log it to `audit_context.recent_migrations[]` and *use* it later: "This column was bulk-loaded on <bulk-load date>, which matches the CRM swap you mentioned at W0. Treat as dead, or do you have a fresher source?"
5. **Never preach.** Don't tell the RevOps person what they already know about their own data.

## Per-wave dance

### W0 — Audit context (always pause)

**Do ask**:
- "What ID system do you tell your team is canonical?"
- "If I asked your billing team, your CS team, and your marketing team that same question, would they give the same answer?" If no/dunno → flag `id_system_contested = true`.
- "Walk me through every field — on any object — that you've told the team NOT to use. Why? When did you tell them?"
- "Are there any data loads or migrations in the last 24 months I should know about? Acquisition, billing-system swap, CRM migration, enrichment refresh, anything?"
- "Who's the human source of truth on this data when something's weird — the person who knows where the bodies are buried?"
- "What's your expected cohort shape? How many accounts? Customers vs prospects? What's your rough MRR distribution?"

**Don't ask**:
- "What's your CRM?" (the scan will discover it)
- "What's a CSV?" (no.)
- "Are you familiar with data quality?" (condescending)

### W1 — Scan + auto-discovery (pause only if surprises)

**Do ask**:
- "I found N files: [list]. I'm reading them as [system inferences]. Is anything mis-labeled? Are you missing an export?"
- "I see Marketo / HubSpot Marketing / Eloqua data — is this still being used or is this from before you moved to [X]?"
- "Object X has N records and object Y has M records, but only K of object X has the FK to Y populated. Expected, or incomplete export?"

**Don't ask**:
- Detail-level questions about CSV format conventions.

### W1b — Name primitives (pause if clusters found)

**Do ask**:
- "5 accounts look like 'Apple' variants: [list with full normalized names]. All resolve to apple.com / linkedin.com/company/apple. Treat as one entity?"
- "Globex cluster: I see 4 'Globex' variants but they resolve to 4 different domains. Real subsidiaries, or merge candidates?"

### W2b — Domain audit (always pause)

**Do ask**:
- Show the worst 5 provider-disagreement domains. Let RevOps eyeball — these often reveal recently-renamed companies, M&A targets, parking situations RevOps recognizes immediately.
- "I see 40 accounts where the canonical domain is `gmail.com`. Legit SMB customers, or noise to filter?"
- "I see K accounts where Website disagrees with the modal email domain — show me 3 examples?"

**Don't ask**:
- "Should I use Serper or Exa?" (they don't care about your providers)
- "What's a domain?" (no)

### W2.5 — ID system reconciliation (always pause)

**Do ask** (HARD-GATED if recommendation ≠ claim at high confidence):
- "You told me at W0 that SFDC is your canonical. The data tells a different story: SFDC covers 90% of accounts; Stripe has 850 customers without SFDC linkage; HubSpot has 400 contacts that never made it to SFDC. The dual-canonical interpretation looks like SFDC = sales universe, Stripe = paying universe. Confirm or override?"

**Don't ask** if the audit agrees with the claim:
- A silent "audit confirms SFDC primary across all object classes" note in the panel is enough. Don't make the user re-confirm what they already said.

### W3 — Join inference (always pause, HARD GATE)

**Do ask**:
- Render the top 10 join concerns in a fixed-width table (from/to, verdict, confidence, evidence). Ask numbered tiebreaker questions:
  1. "The Gong→SFDC join via `call.account_id` resolves on only 60% of calls. Is Gong-to-SFDC integration currently broken, or has it always been spotty for old data?"
  2. "Stripe→SFDC join via `metadata.sfdc_id` has 80% coverage. The 20% gap is concentrated in customers created before <migration date>. Is that from before a billing-system migration?"

**Don't ask**:
- Abstract questions about the six-verdict rubric.
- "Do you understand what a foreign key is?"

### W4 — Revenue reconciliation (always pause)

**Do ask**:
- "I see 6 fields claiming to be MRR/ARR. [Render disagreement table.] Walk me through which one your CFO uses in the board deck."
- "[Apple-at-$20 hits, if any.] Are these real low-MRR accounts of a brand-name customer (their R&D arm using your tool) or join errors?"
- "I see K accounts at exactly $X MRR. Is that a packaged plan, or a placeholder that gets overwritten manually?"

### W5 — Column fitness (pause only if unfit/dead)

**Do ask**:
- Show specific dead columns with the metric that flagged them. "Account.Industry is 95% filled but 80% of values were written in a single import on <bulk-load date>. Do you have a fresher source, or treat as dead?"

### W5b — Contact fill audit (pause if poor)

**Do ask**:
- "1,800 accounts have zero contacts joined. Should the dossier-builder auto-run contact-finder on them, or ask per-account?"
- "Only 30% of contacts have LinkedIn URLs. Run linkedin-validator on a 100-row sample to grade what we do have? Adds ~$0.50 to audit cost."

### W5.5 — Hierarchy (pause if clusters found)

**Do ask**:
- "I see N orphan children with revenue but no calls, and M parents with calls but no revenue. Show me the cross-tab?"
- "Self-parent count: P accounts. Want to fix the data load, or should the dossier collapse those?"
- Per cluster: "PlanetExpressShipping rolls up to PlanetExpressHoldings. Treat as one logical account in dossiers, or keep separate?"

### W6 — Cohort sanity + paradox (always pause)

**Do ask**:
- ICP misses: "12 accounts that I think are NOT in your stated ICP based on web research. Eyeball them?"
- Cross-system paradoxes: "Q accounts marked `active` in CRM but with a billing cancellation event. Which truth wins?"
- Bulk-import-tenure cluster: "K accounts share identical CreatedDate of <one day> — looks like a one-time import. Should tenure for these be uncertain in the dossier?"

### W7 — Sign-off (always pause)

**Do ask**:
- Show every override about to be written to `recommended_engagement_overrides.json`. Render summary: "Will write: 40 do_not_use_fields, 3 rejected_joins, 6 canonical_revenue_picks, account_hierarchy with rollup_to_parent on 12 clusters, multi_domain_clusters with 8 entries, contact_fill_audit recommending auto-run on 1,800 accounts. Approve, edit, or cancel?"

## What every pause shares

The four-block panel template (`../dossier-builder/references/interactive-pause-script.md`):

```
HOW I DID IT
- [Concrete summary of what computation ran, with counts and methods]

OUTLIERS I'M FLAGGING
- [Specific issues, sorted by severity, with the metric value and threshold]

QUESTIONS I NEED YOU TO ANSWER
- [Numbered questions, each tied to a specific observation]

WHAT I'LL DO NEXT
- [What happens contingent on the user's answers — what gets written, what gets skipped]
```

Every block must be specific. Every flagged issue cites a metric. Every question references a concrete data point.
