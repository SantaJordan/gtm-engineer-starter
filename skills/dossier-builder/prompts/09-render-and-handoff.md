# Wave: W6 — Render + Persistence + Handoff

This wave runs in the main thread (no sub-agent). Validate, render, package, and hand off. Terminal.

## STEPS

### 1. Schema-validate every dossier

```
python scripts/validate_schema.py \
  --dossier-dir outputs/dossiers/<client_slug>/dossiers/ \
  --mode <customer|prospect>
```

If any dossier fails: BLOCK. Do not proceed. Surface failures in the four-block panel and ask the human whether to drop the failing accounts or fix the upstream cause.

### 2. Forbidden-word grep

```
python scripts/validate_schema.py \
  --dossier-dir outputs/dossiers/<client_slug>/dossiers/ \
  --forbidden-words config/forbidden-words.yaml
```

Any hit BLOCKS. Forbidden words leaking into pass-1 output is a non-negotiable failure — fix the prompt that produced them, don't waive the check.

### 3. Render HTML

```
python scripts/render_html.py \
  --dossier-dir outputs/dossiers/<client_slug>/dossiers/ \
  --out-dir outputs/dossiers/<client_slug>/dossiers/
```

Renders one `<account_id>.html` per account. v6-style drawer:
- sticky entity header
- headline-as-truth (the deterministic string)
- four-state uncertainty grammar (verified | inferred | fuzzy | missing) with distinct visual states
- timeline spine + sparkbar series
- signals grid
- AI briefing block — fall back to verbatim "AI briefing unavailable" if confidence < 0.4 (never fabricate)
- "How we matched this data" 3-card grid (linking_trust + verdict counts + matching method)
- Provenance chips on every visible field — click to expand source value (240 char truncated)

### 4. Manifest

Write `outputs/dossiers/<client_slug>/manifest.json`:

```json
{
  "client_slug": "<...>",
  "mode": "customer",
  "schema_version": "dossier-builder/1.0.0",
  "rebuilt_at": "2026-05-01T...",
  "n_dossiers": 4000,
  "artifacts": [
    {"path": "engagement.json", "sha256": "..."},
    {"path": "systems_inventory.json", "sha256": "..."},
    {"path": "events/sfdc.normalized.json", "sha256": "..."},
    ...
  ],
  "agent_versions": {
    "system-discovery-agent": "<sha of prompt file at run time>",
    "identity-linking-agent": "<sha>",
    "mrr-arr-agent": "<sha>",
    ...
  },
  "tiebreakers_count": 12
}
```

### 5. Methodology notes (shareable write-up)

Write `outputs/dossiers/<client_slug>/methodology-notes.md` summarizing:

- Methodology used (trust hierarchy, self-domain strip, 6-verdict audit, anti-leakage anchoring) — anonymized.
- Top 5 surprises surfaced during the live run (specific accounts allowed if `privacy_flags.redact_account_names_in_methodology_notes = false`; generic patterns otherwise).
- Top 3 RevOps tiebreaker decisions and what they teach.
- Distributional snapshot (medians, p99/median ratio, fill-rate critical fields, fuzzy-join fraction).

Keep it publish-shape. Don't include client names if `privacy_flags.redact_account_names_in_methodology_notes` is true. Don't include exec names if `privacy_flags.redact_exec_names_in_methodology_notes` is true.

### 6. Tiebreakers log

`outputs/dossiers/<client_slug>/tiebreakers.log.jsonl` is append-only and was being written throughout the run. At W6, validate it's well-formed JSON-Lines. Each line is a `tiebreaker` record (schema: `tiebreaker.schema.json`).

### 7. Hand off

Print the final four-block panel:

```
=== W6: Render + persistence + handoff complete ===
HOW I DID IT
  • Validated 4,000 dossiers (all schema-valid).
  • Rendered HTML drawers for each.
  • Wrote manifest with SHA256 of every artifact.
  • Wrote methodology notes (anonymized).
  • Wrote tiebreakers log (12 decisions captured).
OUTLIERS I'M FLAGGING
  None.
QUESTIONS I NEED YOU TO ANSWER
  None.
WHAT I'LL DO NEXT
  Done. Output dir: outputs/dossiers/<client_slug>/. Sample dossier: dossiers/001XX0000000001AAA.html
```

## SUCCESS CRITERIA

- All dossiers schema-valid (no failures, or human explicitly accepted dropping failing ones).
- No forbidden words in any rendered HTML outside `notes_from_revops` blocks.
- Manifest written with SHA256s.
- methodology notes written.
- Final panel printed with absolute paths.
