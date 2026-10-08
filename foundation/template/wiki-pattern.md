# Wiki pattern (Karpathy-style LLM Wiki, with lifecycle states)

Adopted from Andrej Karpathy's [LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
extended with lifecycle states from Jacob Dietl's
[gtm-context-os-quickstart](https://github.com/jacob-dietle/gtm-context-os-quickstart).
Use it in every project that has a `wiki/` folder.

**In one sentence:** you drop raw material (transcripts, exports, notes) into
`wiki/raw/`, and the AI writes and maintains a set of linked pages that summarize
what you know, so you never re-explain your business from scratch.

## The three layers

1. **Raw sources** (immutable). Articles, transcripts, scraped pages, PDFs, screenshots. Live in `wiki/raw/`. Never edit; only add.
2. **The wiki** (mutable, LLM-written). Summaries, entity pages, concept pages, syntheses, comparisons. Live in `wiki/entities/`, `wiki/concepts/`, `wiki/syntheses/`. Maintained entirely by the LLM.
3. **The schema** (this file + the project's `CLAUDE.md`). How the wiki is structured, what the conventions are, what workflows to follow.

## Karpathy's core rule

> **You never (or rarely) write the wiki yourself — the LLM writes and maintains all of it.**

Human role: sourcing, exploration, asking the right questions, dropping raw material into `wiki/raw/`.
LLM role: summarizing, cross-referencing, filing, bookkeeping, linting.

## The two navigation files

- **`wiki/index.md`** — content catalog. Every entity/concept/synthesis gets a one-line summary, organized by category. Always up to date.
- **`wiki/log.md`** — append-only chronological ledger. Every meaningful update gets one line, prefixed with date and entity type (e.g., `2026-05-24 entity Created [[acme-co]]`). Consistent prefixes so you can grep/parse it.

## Frontmatter on every wiki entity

```yaml
---
slug: unique-slug
type: entity | concept | synthesis
title: Human title
lifecycle: emergent | validated | canonical
valid_from: 2026-05-24
invalid_at: null              # set if superseded; never delete
sources: [raw/transcript-x.md, raw/dossier-y.md]   # what this was built from
links: [[other-slug-1]], [[other-slug-2]]          # cross-references
tags: [healthcare, mid-market, churn]
---
```

## Lifecycle states

- **`emergent`** — newly captured, single source, provisional. Treat as "we noticed this but haven't confirmed."
- **`validated`** — referenced or confirmed by 2+ independent sources/sessions. Reliable.
- **`canonical`** — explicitly promoted by the owner of the project. Authoritative.

## Bitemporal validity

"Bitemporal" means each page records two things: when a claim became true, and
when it stopped being true. Entities are **never deleted**. When something
becomes wrong, set `invalid_at: YYYY-MM-DD` and (if there's a replacement)
`superseded_by: [[new-slug]]`. This preserves the historical record and lets the
LLM reason about "what we thought then vs what we think now."

## Wiki-links

Use `[[slug]]` syntax (Obsidian-compatible). Tooling-light: just plain text, but enables future graph visualization, Obsidian, or any wiki-aware tool. Lint checks that every link target exists.

## Periodic linting (`/wiki-lint`)

Once per week (or before any big synthesis pass), run `/wiki-lint` (the skill is in `skills/wiki-lint/`):

- **Orphan pages** — entities with zero incoming `[[wiki-links]]`.
- **Missing cross-references** — entities whose body mentions another entity by name but doesn't `[[link]]` it.
- **Stale claims** — entities with `valid_from` > 90 days ago that have not been touched.
- **Contradictions** — two `validated` entities making opposing claims about the same subject.
- **Frontmatter conformance** — any entity missing required fields.

## Scale

At ~100 sources / ~hundreds of pages, the `index.md` catalog is enough — no embedding-based search needed. As a project grows past that, consider adding a local search index. Don't over-engineer early.

## Why this works

Humans abandon wikis because maintenance burden grows faster than value. The LLM doesn't get bored, doesn't forget cross-references, and can touch 15 files in one pass. The wiki is a **persistent, compounding artifact** — knowledge compiled once and kept current, not re-derived per query.
