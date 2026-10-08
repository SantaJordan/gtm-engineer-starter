---
name: wiki-lint
version: "1.0"
category: blueprint
tier: team
lifecycle: emergent
author: "Jordan Crawford / Blueprint GTM"
contributors: []
description: >
  Lint a project's wiki/ directory (Karpathy-style LLM Wiki + Dietl lifecycle
  states): catch orphan pages, missing cross-references, stale claims,
  contradictions, and frontmatter conformance issues. Surfaces problems;
  doesn't auto-fix substantive content.
triggers:
  - /wiki-lint
allowed-tools:
  - Read
  - Write
  - Bash(rg *)
  - Bash(grep *)
  - Bash(find *)
prerequisites:
  env: []
  skills: []
api-cost: "$0"
time-estimate: "2-4m"
status: active
valid_from: 2026-05-25
invalid_at: null
last-verified: 2026-06-02
requires-keys: []
---

# /wiki-lint

Periodic maintenance pass over any project's `wiki/` directory. The wiki only stays useful if Claude maintains it — this skill is what runs the maintenance.

See the project's `wiki-pattern.md` (in this starter: `foundation/wiki-pattern.md`; in a project made from the template: `wiki-pattern.md` at its root) for the canonical pattern: three layers (raw / wiki / schema), two nav files (`index.md` + `log.md`), four standard subdirs (`raw/` / `entities/` / `concepts/` / `syntheses/`), bitemporal validity, `[[wiki-link]]` cross-references.

## When to run

- Weekly on actively-changing wikis (client engagements mid-flight, a content sprint).
- Monthly on stable wikis (graduated client repos, deep reference docs).
- Always before a major synthesis pass — surface the contradictions / orphans first so the new synthesis works against a clean graph.

## What it checks

| Check | Description | Severity |
|-------|-------------|----------|
| **structure** | `wiki/` has `index.md`, `log.md`, and the four standard subdirs | error |
| **frontmatter** | every entity has `slug`, `lifecycle`, `valid_from`; values are valid (`emergent` / `validated` / `canonical`) | error |
| **bitemporal** | every entry marked `invalid_at` has a date in the past; entries with `superseded_by:` point at a real `[[slug]]` | error |
| **orphans** | entities with zero incoming `[[wiki-link]]` references | warning |
| **stale** | `lifecycle: emergent` with `valid_from` > 60 days ago and no second citation that would promote to `validated` | warning |
| **contradictions** | two `validated` (or `canonical`) entries that make opposing claims about the same subject — based on identical or near-identical claim text with different conclusions | error |
| **dangling links** | `[[slug]]` references to an entity that doesn't exist (typo or deletion) | error |
| **index drift** | `index.md` lists an entity that no longer exists, or omits one that does | warning |
| **log gaps** | `log.md` hasn't been appended to in 30+ days while entities have been modified | warning |
| **promotion candidates** | `emergent` entries referenced by 2+ other entities — should be promoted to `validated` | info |

## Run

```
/wiki-lint                   # current cwd's wiki/
/wiki-lint <path>            # a specific wiki dir
/wiki-lint --json            # machine-readable output
/wiki-lint --severity=error  # only fail / surface errors
```

## Procedure (the prompt)

1. **Read the contract.** Open `wiki/index.md`, `wiki/log.md`, and every entity file. Build an in-memory graph: nodes = entities, edges = `[[wiki-links]]`.
2. **Run each check** in the table above. Group findings by severity.
3. **For contradictions**, never auto-resolve. Show both entries side-by-side with the conflicting claim highlighted. Let the human adjudicate; the bitemporal pattern means the loser gets `invalid_at` set, not deleted.
4. **For orphans + stale + promotion candidates**, propose specific actions. Apply only with confirmation.
5. **For structure + frontmatter + bitemporal + dangling links**, these are mechanical. Offer to auto-fix where the fix is unambiguous (missing `valid_from` defaults to today; typo'd `[[slug]]` matched to the nearest real slug with fuzzy match — confirm each).
6. **Append a one-line entry** to `wiki/log.md`: `2026-05-25 maintenance /wiki-lint — N errors, M warnings surfaced; <decisions>`.

## Output shape

```
# /wiki-lint — wiki/ (../client-x)
# 47 entities, 23 concepts, 8 syntheses

ERRORS (3):
  [structure]     wiki/syntheses/ missing
  [frontmatter]   entities/acme-co.md — lifecycle: validatied (typo)
  [contradiction] concepts/icp-shape.md  AND  concepts/icp-shape-refined.md
                  both claim: "ICP is mid-market multi-state HMOs"
                  vs:         "ICP is single-state regional HMOs"
                  → adjudicate. mark one invalid_at.

WARNINGS (12):
  [orphan]    entities/old-vendor-vista.md — no inbound [[links]] in 90d
  [stale]     concepts/lift-curve-v1.md — emergent for 92d, no 2nd citation
  ...

INFO (4):
  [promotion] entities/acme-co.md — referenced by 4 other entities → bump validated?

Apply auto-fixes? (3 trivially fixable) (y/N/show)
```

## See also

- `wiki-pattern.md` (project root, or `foundation/wiki-pattern.md` in the starter) — the schema this lints against.

---

Methodology by Jordan Crawford / Blueprint GTM.
