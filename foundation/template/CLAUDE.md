# {{company_name}} — GTM workspace

> Copy this folder to start a new project. Replace every {{placeholder}}.
> Claude Code reads this file at the start of every session. For Codex, copy it
> to AGENTS.md (or make AGENTS.md a one-line pointer to this file).

## Snapshot

- **Company:** {{company_name}} ({{company_domain}})
- **What we sell:** {{one sentence}}
- **Who buys it:** {{buyer title}} at {{kind of company}}
- **Goal for this project:** {{goal}}

## Our systems (where data lives)

| System | What it holds | How to get data out | Join key | Do not trust |
|---|---|---|---|---|
| {{CRM}} | accounts, deals | {{export / API}} | {{domain or id}} | {{field}} |
| {{call recorder}} | call transcripts | {{export / API}} | {{email domain}} | {{}} |
| {{billing}} | invoices | {{export / API}} | {{customer id}} | {{}} |

Our own email domain is `{{company_domain}}`. Never treat it as a customer domain
when joining records by email.

## Context

@wiki/index.md
@wiki-pattern.md

## How to work here

1. Read `wiki/index.md` before starting anything.
2. New raw material goes in `wiki/raw/`, unedited. It is never committed.
3. After any analysis, update the wiki (entities, concepts, syntheses) and add one
   line per change to `wiki/log.md`.
4. Bulk pulls (more than ~50 records) are scripts that call the API, not chat.
5. Before any paid run that could cost more than $100, estimate the cost and ask.

## Private by default

`wiki/raw/`, `private/`, `out/`, root CSVs and `.env` are gitignored. This repo is
private. Stage files by name after reading `git status`; never stage everything.
Run every command from the project root.
