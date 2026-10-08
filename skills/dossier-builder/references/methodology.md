# Methodology

The non-negotiables behind every agent in this skill. Read at W0; re-read whenever an agent's output starts to drift.

## The trust hierarchy

When two systems disagree about an account, prefer in this order:

1. **Customer actions** — product events, login data, completed jobs, payments processed, feature usage. The customer voted with their behavior.
2. **Customer voice** — Gong call transcripts, support ticket text, in-app survey responses, emails the customer wrote. They said it themselves.
3. **CRM-derived state** — opportunity stages, account types, lead-source classifications, CSM-entered notes. These reflect what reps recorded, which is one step removed from what happened.

This order is load-bearing. Sales reps mark deals "Customer" when commission triggers; product data shows the customer never logged in. The product is right. The dossier should reflect that — and the LLM-readable section labels in pull-prompts should make this explicit ("TRUST THESE: customer voice (Gong)" / "use cautiously: CRM").

## Said vs. did framing

When the dossier exposes a gap between sales-call commitments and observed behavior, frame as a contrast — not as a verdict. Example template (invented for illustration):

> "On the sales calls, the office manager described the scheduling pain clearly and agreed to a setup checklist. In practice the checklist was half done by the second training, and the kickoff was rescheduled twice. The product is being used; it's not abandoned (weekly logins, a new location added last month). The gap matches the handoff note 'needs hand-holding', not disengagement."

Said-vs-did is the most reusable narrative shape for retained-but-fragile customers. Keep it descriptive. Pass-1 names the gap; Pass-2 (a separate downstream skill) reasons about it.

## Anti-leakage anchoring (prospect mode)

For prospect dossiers, every event in the timeline must be filtered against `t_anchor` — the moment of first material engagement. Default rule:

```
t_anchor = first_call.date if any gong_call exists
        else opp.created_date
```

Two evidence blocks come out of this:

- `pre_first_call_evidence[]` — events strictly before `t_anchor`. This is what the rep had at the moment they engaged.
- `post_first_call_evidence[]` — events at or after `t_anchor`. This is what the engagement actually produced.

The split exists because prospect-side reasoning (next-best-action prompts, win-loss analysis) regularly leaks post-event evidence into pre-event predictions. Forcing the split at dossier-build time eliminates the entire class of bug.

Customer mode does not need this — customer dossiers are descriptive of the present, not predictive of the future.

## Pass-1 / Pass-2 split

This skill is **Pass-1 only**.

- **Pass-1** describes verbatim. Records what each system says. Resolves identity. Surfaces conflicts. Picks canonical sources for revenue and key fields. Carries provenance. Refuses to reason.
- **Pass-2** reasons. Scores. Recommends. Predicts. Attributes churn cause. Suggests next best action.

Pass-1 dossiers are the durable artifact. They get written once and consumed by many downstream Pass-2 jobs. Letting reasoning bleed into Pass-1 makes the artifact unstable — a single prompt change in the reasoning layer requires a full rebuild.

To enforce: the forbidden-word list in `config/forbidden-words.yaml` (`predict, suggest, recommend, score, potential, fit, ICP, likely, indicate`) is checked at render time for every visible field outside `notes_from_revops`. Hits fail the render.

## Conservative flagging, educate the reader

Two principles for every wave's pause panel:

**Conservative flagging.** When a record looks suspect, surface it. When a join is fuzzy, downgrade to `INDETERMINATE` and ask. Better to be slowed by a tiebreaker than to silently drop. Silent drops compound — by W4 the dossier is missing data the human would have caught at W2.

**Educate the reader.** RevOps is on a Zoom call with us. They are co-validators, not passive audience. Every gated wave shows: how I did it (so they understand), outliers (so they react), questions (so they decide), what I'll do next (so they know what they're approving). The four-block panel is in `references/interactive-pause-script.md`.

## Real-world anchoring

When something looks off — a household-name account at $20 MRR, an account with no website, a customer with no calls in 18 months — the dossier should anchor against real-world evidence before flagging. Web sample (W3c) handles this at cohort scale. Per-account, the AI-briefing block can include numbered citations to the underlying source records, but only if confidence ≥ 0.4. Below that threshold, render "AI briefing unavailable" verbatim. Never fabricate a narrative to fill a confidence gap.

## Three rules an agent should never violate

1. Don't trust a derived/fuzzy-joined system field without per-record verification. (Why: a warehouse account-id column derived by name-stem matching can be wrong on roughly 40% of records, attributing calls to the wrong accounts. The audit's six explicit verdicts mean no row gets through without one.)

2. Don't email-domain-join without first stripping the customer's own tenant domain. (Why: your own staff appear as "external participants" on calls, so an email-domain join matches them to every account they touched.)

3. Don't single-key-join without a 3-test guard. Same ID can collide. Same email can collide. Same phone can collide. Require ≥2 of {ID, domain, name fuzzy ≥0.92} to agree. (Why: a short legacy numeric ID can be shared by two unrelated companies in different states.)

These three rules eliminate the largest categories of dossier corruption seen in practice. They are mandatory for the identity-linking agent and recommended for any source-pull agent that performs cross-system enrichment.
