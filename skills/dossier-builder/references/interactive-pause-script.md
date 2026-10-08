# Interactive pause script

The four-block panel template. Print this verbatim shape after every gated wave. The human (RevOps person on Zoom) reads it as the wave's recap; the structure is consistent so they learn to scan it.

## Template

```
=== W{N}{a-c}: {Wave name} complete ===

HOW I DID IT
  • {3-7 bullets, each one short. What sources, what rules, what was stripped.
     Cite verbatim numbers — "stripped 250 false matches", not "many false matches".}

OUTLIERS I'M FLAGGING
  • {3-8 specific records or patterns. Each cites an account_id and a one-sentence
     reason. Truncate to 8 — if there are more, surface "+N more, see <path>".
     Order by severity: blocks first, then warns, then notes.}

QUESTIONS I NEED YOU TO ANSWER
  Q1. {Specific question with options offered. Use AskUserQuestion-style
       multiple-choice for tiebreakers; free-text for narrative ones. Each
       question gets a question_id like W2b-Q1.}
  Q2. ...
  (Cap at 4-5 questions. If more are needed, ask the most blocking ones now;
   defer the rest to the next gate.)

WHAT I'LL DO NEXT
  {1-2 sentences naming the next wave and what gates on the human's answers.
   "Continue to W3a (MRR/ARR canonical pick), gated on Q1-Q3 above."}
```

## Persistence

Every Q gets persisted to `notes_from_revops[]` (and to `tiebreakers.log.jsonl` append-only) as a `tiebreaker` record:

```json
{
  "question_id": "W2b-Q1",
  "wave": "W2b",
  "prompt": "Account X: SFDC ID matches but billing email domain differs. Trust the SFDC link or reject?",
  "options_offered": ["keep", "reject", "escalate"],
  "answer": "reject",
  "answer_notes": "We had a misattributed merger here last quarter — different account.",
  "answered_by": "jdoe@customer.example",
  "answered_at": "2026-05-01T12:00:00Z",
  "applied_to": ["001Rp00000abc123"]
}
```

The dossier carries every tiebreaker that was applied to its records. The audit trail is the load-bearing artifact for "how did we decide this?" months later.

## Voice

Speak to the human like they are an intelligent peer who knows their own data better than you do. Don't apologize. Don't hedge. Don't overload with caveats — the four-block structure already does that work.

Good: "Stripped tenant domain `contoso.example` before email-domain joins (would have created 250 false matches)."
Bad: "I think there might have been some potential issues with the email domain matching, possibly..."

Good: "Q1. Tailspin Law shows MRR $1,200 in SFDC and $0 in Vitally. Which is authoritative?"
Bad: "There seems to be a discrepancy that I noticed which may or may not be important — what would you like to do?"

## When NO outliers / NO questions

If a wave produces clean outputs — no blocks, no warns — print the panel anyway with empty sections:

```
=== W3a: MRR/ARR canonical pick complete ===

HOW I DID IT
  • Picked SFDC Account.MRR__c as canonical for 4,000/4,000 accounts.
  • No source disagreed by >10%. No Apple-at-$20 outliers.

OUTLIERS I'M FLAGGING
  None.

QUESTIONS I NEED YOU TO ANSWER
  None.

WHAT I'LL DO NEXT
  Continue to W3b (per-key-field probes).
```

The empty sections are reassuring, not redundant. The RevOps person sees "I checked, here's what I checked, nothing weird" and proceeds with confidence.

## What NOT to put in the panel

- Apologies, hedges, or "I think" / "I'm not sure".
- Long explanations of methodology — those are in `references/methodology.md` for the agents, not the human.
- Code snippets, JSON blobs, raw schema. The panel is plain-English narrative; the JSON is in files for inspection.
- Promises about future waves' decisions. The next wave decides those; don't preview them.
