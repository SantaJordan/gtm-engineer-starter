# Connect your context

Your AI assistant (Claude Code or Codex) only knows what you show it. The
fastest way to better output is not a better prompt. It is better context:
your calls, your CRM, your invoices, your notes.

This page covers four moves: tell the assistant what systems you have, pull
data out in bulk, say what output you want, and drop the data where the wiki
can use it.

## 1. Tell the assistant your systems

Start every new project by writing down, in plain words, where your data lives.
Put it in the project's `CLAUDE.md`. Claude Code reads this file at the start of
every session. Codex reads `AGENTS.md`, so keep a copy or a one-line pointer there.

A good "systems" section answers four questions per system:

| Question | Example |
|---|---|
| What is it? | "HubSpot is our CRM. Deals, contacts, companies." |
| How do we get data out? | "HubSpot export to CSV, or the HubSpot API with a private-app token." |
| What is the join key? | "Company domain. Deal records carry `associatedcompanyid`." |
| What should I not trust? | "The `industry` field is free text and mostly blank." |

The last question matters most. Every company has a field everyone knows is
wrong. Say so up front, and the assistant will not build on it.

## 2. Prefer command-line tools for bulk pulls

A **CLI** (command-line interface) is a program you run by typing a command,
like `gh` for GitHub. For pulling hundreds or thousands of records, a CLI or a
short script that calls the vendor's API is faster, cheaper and easier to repeat
than clicking through a web app, or asking the assistant to fetch records one at
a time through a chat connector.

Rules of thumb:
- **Under about 50 records:** asking the assistant to fetch them one by one is fine.
- **More than that:** ask it to write a script that calls the API directly, saves
  the raw result to a file, and prints a count. Run the script yourself.
- **Always save the raw pull first.** Analysis comes after, from the saved file.
  Then you can re-run the analysis without paying for the pull again.

Example prompt:

> Write a script that exports every closed-won and closed-lost deal from our CRM
> for the last two years to `wiki/raw/crm/deals-2026-10-01.csv`, with company
> domain, close date, amount and stage. Use the API key in `.env`. Print the row
> count when done. Do not analyze anything yet.

## 3. Describe the outputs you want

Tell the assistant what "done" looks like before it starts. Name the file, the
columns, and who will read it.

> When you finish, I want one CSV with one row per account: account name,
> domain, outcome (won / lost / 10X), and the one sentence of evidence for that
> label. A sales manager will read it, so no jargon.

## 4. Drop exports into `wiki/raw/`

`wiki/raw/` is the inbox. Anything you export goes there, unedited. It is in
`.gitignore`, so raw exports never reach GitHub; only the pages the AI writes
from them do (to your private repo):

```
wiki/raw/
  calls/        call transcripts (one file per call)
  crm/          CRM exports
  billing/      invoices, subscriptions
  notes/        your own notes, meeting summaries
```

Then ask the assistant to ingest it:

> Read everything new in `wiki/raw/` and update the wiki: one entity page per
> customer account, a concept page for any pattern you see across three or more
> accounts, and a line in `wiki/log.md` for each change. Follow
> `foundation/wiki-pattern.md`.

## 5. Keep `CLAUDE.md` thin

`CLAUDE.md` should point to things, not contain them. Use `@` imports (Claude
Code pulls the named file into context):

```markdown
## Context
@wiki/index.md
@foundation/wiki-pattern.md
```

A long `CLAUDE.md` is read on every message. A short one that points at the
wiki stays small and stays current, because the wiki does.

## Customer data stays private

Anything with customer names, emails, call recordings or revenue is private.
Raw exports stay out of git entirely: `wiki/raw/`, `private/`, `data/` and the
customer CSVs at the project root are in `.gitignore`. The wiki pages the AI
writes go to a **private** repo (see `push-to-github.md`). Remember that your AI
assistant's provider, and any API you configure, receive the text you send them.

## Further reading

- Jacob Dietl, [gtm-context-os-quickstart](https://github.com/jacob-dietle/gtm-context-os-quickstart): a fuller context setup for GTM teams.
- [Your context is the bottleneck, not your model](https://edge.blueprintgtm.com/p/your-context-is-the-bottleneck-not) (free article).
