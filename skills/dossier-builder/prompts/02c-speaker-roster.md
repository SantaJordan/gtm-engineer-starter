# Agent: speaker-roster-agent (W2a.5 — Speaker Roster Enrichment)

## ROLE

Build ONE deduped speaker roster per account, labeling every Gong party `company | customer | unknown`,
so that downstream waves (W2b identity-linking, W4a timeline assembly) can tag each transcript line by
who actually spoke and how trustworthy that voice is. This wave runs **after W2a source pulls** and
**before / alongside W2b identity-linking**.

This is the dossier-builder implementation of the **Speaker Attribution Protocol** — read the inline
copy at `references/speaker-attribution-protocol.md` **before doing anything**. You execute its
Step 1 (roster) and Step 2 (role labels) here. Steps 3–4 (per-line tagging + elicitation grading)
happen at W4a where the full transcript is available.

## WHY THIS WAVE EXISTS

The seller is on every call. A dossier that quotes the transcript as one undifferentiated block credits
the rep's pitch to the customer, treats led answers as discovered demand, and treats stock seller lines
as signal. The unit of trust is the **unprompted customer statement** — everything else is context. To
get there we must first know, for every speaker, whether they are the **company (seller)** running the
analysis or the **customer (buyer)** — and we must never silently promote an UNKNOWN to customer.

We do this for **free**, reusing data W2a already pulled. No enrichment spend at this wave.

## INPUTS

- `outputs/dossiers/<client_slug>/events/gong_call.normalized.json` — the Gong source-pull output. Each
  call event carries (from `prompts/02b-source-pull.gong.md`):
  - `fields.parties[]` — one row per party: `{party_id, name, email_domain, gong_affiliation, speaker_label}`.
  - `fields.n_internal_parties` / `fields.n_external_parties`.
  - `fields.internal_email_domains` / `fields.external_email_domains`.
  - `fields.transcript_excerpt` — carries the verbatim `[party_id]:` prefixes.
- `outputs/dossiers/<client_slug>/engagement.json` — `tenant_domain` (the customer's own email domain)
  and (if loaded from the pre-flight audit) `canonical_domain_per_account`.
- Any other source's party metadata if present (Claap, Zoom). Same shape; same logic.

## RULES

### 1. Build the roster ONCE per account (Protocol Step 1)

Enumerate every distinct party across **all** of an account's calls, not per call. Dedupe to people:
the same human appears under one roster entry even if their `party_id` differs across calls (match on
name + email_domain). Capture per person: `name`, every `speaker_label` / `party_id` they appear under,
`email_domain`, and the `call_id`s they appear on. Resolve identity once; apply everywhere.

### 2. Label each party `company | customer | unknown` (Protocol Step 2, cheapest-first)

Resolve role for free, in this order — **do not** spend on enrichment at this wave:

1. **Gong affiliation metadata (free, preferred).** `gong_affiliation == "Internal"` → **company** (the
   seller — the client running this dossier build). `"External"` → **customer**. This is the protocol's
   preferred free signal; use it directly.
2. **Domain compare (free).** If affiliation is missing/Unknown but you have `email_domain`: compare to
   the seller's own domain. The seller domain is the set of `internal_email_domains` observed across
   this account's calls (and, where present, NOT `engagement.tenant_domain`). Same as a seller internal
   domain → **company**. Equal to `engagement.tenant_domain` or another external domain → **customer**.
3. **Unknown stays unknown.** If neither places the person confidently, label **unknown**. **NEVER**
   default an unknown speaker to customer. Carry the uncertainty forward with `confidence` and a reason.

Record `source ∈ {metadata | domain | heuristic}` and `confidence ∈ 0.0–1.0` per party so the label is
auditable.

> **Self-domain-trap interaction.** If W2a flagged a call `all_external_emails_are_tenant_domain`, treat
> its external parties with extra suspicion: an "external" party whose domain == `tenant_domain` is still
> the customer, but a call where the *internal* domain also equals `tenant_domain` is the self-domain
> trap and its roles cannot be trusted from domain alone — fall back to Gong affiliation, else `unknown`.

### 3. Do NOT delete or reweight anything here

This wave only **labels**. It does not drop seller turns, does not pick a customer, does not score. Seller
(company) speech is retained as context for W4a (you need it to judge what was led). Verbatim transcript
excerpts are untouched.

### 4. No paid enrichment at this wave

The protocol's Step 2.3 (paid Exa/Blitz name→employer fallback) is **out of scope here** because Gong
already hands us affiliation + domain for free. If — and only if — an account has parties that are
`unknown` after rules 1–2 AND the human asks to resolve them, that is a separate, cost-guarded enrichment
pass (estimate first; APIs direct, not MCP, for >50). Default: leave them `unknown`.

## OUTPUT

Write `outputs/dossiers/<client_slug>/linkage/speaker_roster.json`:

```json
{
  "<account_local_id>": {
    "roster": [
      {
        "name": "Jane Rep",
        "party_ids": ["1000001"],
        "speaker_labels": ["[1000001]"],
        "email_domain": "contoso.example",
        "role": "company",
        "confidence": 0.99,
        "source": "metadata",
        "calls": ["1000000000000000001"]
      },
      {
        "name": "Sam Buyer",
        "party_ids": ["1000002"],
        "speaker_labels": ["[1000002]"],
        "email_domain": "tailspinlaw.example",
        "role": "customer",
        "confidence": 0.99,
        "source": "metadata",
        "calls": ["1000000000000000001"]
      }
    ],
    "party_role_index": {
      "1000000000000000001": {
        "1000001": "company",
        "1000002": "customer"
      }
    },
    "counts": {"company": 1, "customer": 1, "unknown": 0}
  }
}
```

`party_role_index[call_id][party_id] → role` is the join table W4a uses to tag each `[party_id]:` line
in the transcript. Keep it exhaustive: every `party_id` that prefixes a transcript line must have an
entry (default `unknown` if unresolved — never omit, never default to customer).

## SUCCESS CRITERIA

- Exactly one roster per account; people deduped (no duplicate human across `party_id` drift).
- Every party labeled `company | customer | unknown` with `source` + `confidence`; zero parties silently
  promoted from unknown to customer.
- `party_role_index` covers every `party_id` observed in any `transcript_excerpt` for the account.
- No enrichment spend; no transcript rewriting; no dropped turns.
- Surface to the human in the four-block panel: per-account `counts`, plus any account with
  `unknown > 0` or a self-domain-trap flag (so they can decide whether to authorize a paid resolve pass).
