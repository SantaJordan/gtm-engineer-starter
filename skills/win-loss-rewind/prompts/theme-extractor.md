# Phase 4 — Theme Extractor Prompt (Batch API)

You are extracting structured pain themes from one customer's full lifecycle, run in parallel via Anthropic Message Batches API. One request per customer. Sonnet 4.6 default.

## Inputs (per request)

```
customer:
  id: <id>
  company: <name>
  domain: <domain>
  outcome: won | lost | healthy | churned
  close_date: <iso>
  archetype_id: <int>           # from Phase 3
  archetype_name: <string>

signal_panel:                    # from Phase 2
  <kv pairs of observable signals>

reconciliation_flags:            # from Phase 0/2
  <flags where user labels disagreed with external reality>

call_transcripts:                # from Phase 1, audited + Phase 1.4 attributed
  - call_id: <id>
    started: <iso>
    role_in_lifecycle: <discovery | demo | negotiation | onboarding | qbr | renewal | churn_save | other>
    roster:                       # from Phase 1.4 — who is who on this call
      - name: <string>
        role: company | customer | unknown   # company = the seller; unknown is NEVER customer
    transcript:                   # role-tagged turns; head + tail if longer
      # every turn is prefixed with the speaker's employment tag:
      #   [company]                 <- the seller running this analysis (CONTEXT only)
      #   [customer · unprompted]   <- buyer raised it on their own, grade CONFIRMED by a human (strong signal)
      #   [customer · unprompted · provisional]  <- heuristic grade, NOT confirmed: weigh it like `led`
      #   [customer · led]          <- buyer said it only after the seller framed/asked it (DISCOUNT)
      #   [customer · seller_echo]  <- the seller's own stock line, parroted (EXCLUDE from demand)
      #   [unknown]                 <- could not be placed (NEVER treat as customer)
```

**This is the Speaker Attribution Protocol** (inline copy at `references/speaker-attribution-protocol.md`). The unit of trust is the **confirmed unprompted customer statement**. A `provisional` tier was set by a heuristic; never report a provisional line as verified customer-initiated evidence. Seller turns are kept so you can see what was led — they are never customer signal.

## Output (one JSON object per customer)

Return JSON only. No prose.

```json
{
  "customer_id": "<id>",
  "pain_language": [
    {
      "phrase": "<verbatim from transcript>",
      "speaker_role": "buyer | influencer | user | exec",
      "speaker_employment": "company | customer | unknown",
      "elicitation": "unprompted | led | seller_echo",
      "call_id": "<id>",
      "moment_in_call": "<iso or seconds>"
    }
  ],
  "problem_framing": {
    "what_they_said_was_broken": "<2 sentences in their words>",
    "underlying_pain": "<1 sentence — what was actually true under what they said>",
    "did_they_articulate_it_clearly": true | false
  },
  "temporal_markers": {
    "when_pain_emerged_per_their_account": "<iso or relative — null if not stated>",
    "when_they_started_searching_for_solution": "<iso or relative — null if not stated>",
    "lead_time_pain_to_search_days": <int or null>,
    "lead_time_search_to_close_days": <int or null>
  },
  "company_state_at_pain_emergence": [
    "<observable fact: e.g. 'just hired second compliance officer'>",
    "<observable fact: e.g. 'expanded from 1 to 3 states in prior 12 mo'>"
  ],
  "outcome_explanation": {
    "if_won_why_they_chose_us": "<short — null if not won>",
    "if_lost_why_they_didnt": "<short — null if not lost>",
    "if_churned_why_they_left": "<short — null if not churned>",
    "if_healthy_what_keeps_them": "<short — null if not healthy>"
  },
  "anti_pattern_flags": [
    "<flag if extracted theme is generic-SaaS, e.g. 'their reason was \"price\" with no specificity'>"
  ],
  "extraction_confidence": 0.0
}
```

## Critical rules

1. **`pain_language` must be verbatim.** Don't paraphrase customer phrases — the downstream hypothesis generator depends on the exact language. If you can't find any verbatim pain phrases in the transcript, return an empty array and lower `extraction_confidence`.

   **1a. Extract pain ONLY from `[customer]` turns.** A `[company]` turn is the seller talking — it is context, never demand. Never put a seller line in `pain_language`. A line tagged `[unknown]` is a speaker we could not place; do NOT treat it as customer — leave it out of `pain_language` (you may note it in `problem_framing` only if clearly relevant, flagged as unplaceable).

   **1b. Stamp `speaker_employment` and `elicitation` on every `pain_language` item**, copied from the turn's tag. `speaker_employment` must be `customer` for any real pain (see 1a). `elicitation` is the tier on the turn: `unprompted`, `led`, or `seller_echo`.

   **1c. EXCLUDE `seller_echo` from demand.** A `[customer · seller_echo]` line is the seller's own pitch parroted back — it discriminates nothing. Do not list it as pain. If you reference it at all, it is context only.

2. **`underlying_pain` is your interpretation, but be honest about it.** If the customer said "we needed better reporting" but the actual pain was "the CFO yelled at the COO over a missed quarterly review", capture both: their account in `what_they_said_was_broken`, your read in `underlying_pain`. Mark `did_they_articulate_it_clearly = false` if they didn't.

3. **Temporal markers are critical for Phase 5.** Without them, the hypothesis generator has no lead-time to backcast. If transcripts contain phrases like "we've been dealing with this for 18 months" or "we started looking last quarter", capture the dates. Compute `deal_close_date - date_search_started` for `lead_time_search_to_close_days`. Null is acceptable when the data isn't there — guess is not.

4. **`company_state_at_pain_emergence` is observable facts only**, not inferences. "Hired second compliance officer" — observable. "Was probably under regulatory pressure" — inference, leave out. The signal panel and transcripts are your evidence.

5. **`anti_pattern_flags` calls out generic findings.** If the only "reason" extracted is "price" or "fit" with no specificity, flag it. Don't invent specificity.

6. **`extraction_confidence` is honest — and discounts led/echoed pain.** 0.95 = transcripts were rich and the customer articulated clearly *on their own* (`unprompted`). 0.5 = thin transcripts or the customer was vague. <0.5 = should probably be excluded from Phase 5 signal generation for this customer. **Lower `extraction_confidence` when the customer's pain is mostly `led` or `seller_echo`** rather than `unprompted` — a pain the seller walked them into is weak evidence of real demand. If every pain phrase you found is `led`/`seller_echo`, the customer surfaced nothing on their own: keep the phrases (tagged honestly) but set `extraction_confidence` ≤ 0.4 and say so in `anti_pattern_flags`.

## Anti-patterns to refuse to write

- "Customer wanted a better solution" — meaningless
- "Customer felt the previous tool was inadequate" — no specifics
- "Customer was looking to scale" — what does that mean
- "Customer needed a partner not a vendor" — sales platitude

If the transcript only supports findings like these, return low confidence and minimal content. Better empty than fabricated.

## Tone

You are not writing for a human reader. You are writing structured input for Phase 5. Optimize for downstream queryability — exact phrases, dates, observable facts. No flourish.
