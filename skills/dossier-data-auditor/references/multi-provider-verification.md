# Multi-provider verification doctrine

> Loaded at W0, W2b, W6. The load-bearing claim: no single provider sees the whole web. Triangulate, don't trust.

## The Floqer principle

From the GTM-stack PDF: the play is to run multiple providers in parallel and merge results. Single-provider verdicts are accepted only when the cost of a second opinion is meaningfully larger than the cost of being wrong. For per-record domain canonicalization at corpus scale, Blitz (unlimited) makes the cost of a second opinion negligible — so we always get one.

## Merge rule (apply at every verification step)

| Result | Verdict | Action |
|---|---|---|
| ≥2 of N providers agree | `verified` | Use it. Record which providers contributed. |
| Providers disagree | `mismatch` | Surface to human. Don't pick a side silently. |
| All providers fail | `unverifiable` | Make no claim. Don't fabricate. Drop record from the join cohort or mark indeterminate. |

Confidence weighting is equal across providers on the first audit. Over multiple engagements, the auditor can learn per-provider accuracy by use case and reweight (out of scope for v1).

## Provider routing table

Refer to `config/provider-routing.yaml` for the full routing table. Summary:

| Use case | Primary | Secondary | Last-resort |
|---|---|---|---|
| Per-record domain canonicalization (W2a/b) | Blitz (unlimited) | OpenWebNinja AI Mode | Firecrawl render → DiscoLike `/match` |
| Domain state — parked / dead / acquired / tenant-subdomain (W2b) | Firecrawl render + DNS | OpenWebNinja AI Mode | — |
| Multi-domain consolidation (W2c) | Blitz companies search | OpenWebNinja AI Mode | DiscoLike `/subsidiaries` |
| Brand vs legal-name (W1b) | Blitz companies search per variant | OpenWebNinja AI Mode | Exa Answer |
| 200-row ICP cohort sample (W6) | Parallel.ai Task Groups (Basis confidence) | Exa Agent beta on `<0.6` rows | Perplexity Sonar Pro |
| Per-record paradox resolution (W6) | Parallel.ai Task Core | Exa Agent beta on disputed rows | — |
| Contact LinkedIn validation (W5b) | `linkedin-validator` skill → RapidAPI LinkedIn underneath | — | — |
| SERP tie-breaker (W2b) | Serper + Exa | — | — |

## Why Blitz first

Blitz is flat-rate on some plans. For per-record questions at corpus scale (5K-50K accounts), this matters more than nominal accuracy: it lets the auditor call Blitz on every record rather than on a 50-row sample, which catches per-record failures that sampling misses.

## Why Exa Agent (beta) instead of Perplexity for W6 secondary

Parallel.ai and Perplexity both ground on live web crawls. When Parallel returns a low-confidence row, asking Perplexity for a second opinion uses the same methodology and frequently reproduces the same wrong answer.

Exa Agent runs primarily off Exa's semantic index. When Parallel is unsure, Exa Agent is the *structurally different* lens — disagreements between Parallel and Exa are real signal, not methodology echo.

Mandatory: pin `Exa-Beta: agent-2026-05-07` in the client header. If Exa rolls the beta forward without an explicit migration, fail loudly rather than silently changing request/response shape.

## Graceful degradation

If a provider's env key is missing, fall through to the next-tier provider for that use case. Hard-fail (with surfaced error) only when BOTH primary and secondary are missing for a critical use case:

- `domain_canonicalization_per_record` requires Blitz OR OpenWebNinja
- `icp_cohort_sample_200` requires Parallel OR Exa
- `per_record_paradox` requires Parallel OR Exa

Critical-use-case absence is a W0 hard refusal — the audit cannot reliably ground its W2b/W6 verdicts without one of these working.

## Cost ceilings

Per `references/multi-provider-verification.md` (this file) + the $100 rule (below):

- W3 LLM verdict step: $100 ceiling (triggers cost-guard).
- W6 ICP cohort sample: ~$5-8 (200 rows × $25/1K Core) + ~$2-3 escalation slice (Exa Agent on low-confidence).
- W6 per-record paradox: $15 hard cap (50 rows × $0.025).
- W5b email verification: $0.45 ceiling (500-row MillionVerifier sample).
- Domain canonicalization full corpus: $0 (Blitz unlimited) + ~$2-5 OWN AI Mode for ~200-record cross-check tier.

Total typical-engagement audit cost: $15-30.
