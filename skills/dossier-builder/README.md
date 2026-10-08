# dossier-builder

Client-agnostic, interactive workflow skill that builds per-account customer or prospect dossiers — unified timelines of every interaction across every system a company has, with provenance, linking_trust, and a deterministic headline string.

Designed to run live on a Zoom call with the customer's RevOps person. Fans out to one Opus agent per high-stakes concern (each data source, identity-linking, MRR/ARR, every key field, DQ triage with statistician+RevOps mind, three-perspective end-of-pipeline critique). Pauses between waves to surface outliers, ask tiebreaker questions, and walk RevOps through methodology.

## Quick start

Run from the project root (the folder with `START-HERE.md`):

```bash
bash skills/dossier-builder/scripts/new-engagement.sh acme-2026q2
# fill in mode + tenant_domain in outputs/dossiers/acme-2026q2/engagement.json (gitignored)
# then trigger the skill: "Let's build customer dossiers for acme"
```

## Architecture

- **SKILL.md** — wave orchestration + UX contract
- **references/** — methodology, prior-build lessons, schema, linking-trust rubric, verbatim discipline, trust-hierarchy fragments, critique trio, interactive panel template
- **schemas/** — JSON Schemas for every artifact (`source_event_envelope`, `link_evidence`, `dq_finding`, `systems_inventory`, `tiebreaker`, `dossier.customer`, `dossier.prospect`)
- **prompts/** — one file per fan-out agent (W0 mode-select through W6 render-handoff)
- **config/** — `dq-checks.yaml` (extensible), `forbidden-words.yaml`, `source-registry.yaml`, `outcome-enums.yaml`
- **scripts/** — `validate_schema.py`, `render_html.py`, `web_sanity_sample.py`, `new-engagement.sh`

## Validation

Earlier versions were checked by replaying frozen exports, which are private and not
shipped. What they taught is in
`references/lessons-from-prior-builds.md`. To try the skill on data you can see, use the
fictional corpus in `examples/northwind-demo/` at the repo root.

## Non-goals

- Not Pass-2 analysis (scoring, recommendations, churn-cause attribution)
- Not a daily-refresh cron
- Not a CRM writeback
- Not a BI dashboard
