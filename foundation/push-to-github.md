# Compound with GitHub

**Git** keeps a full history of every file in a folder. **GitHub** stores that
history online, so it is backed up and you can work from more than one computer.
Together they make your wiki and scripts compound: each week's work builds on the
last, and nothing is lost when a laptop dies.

## Who receives your data

Be clear about where things go:

- **GitHub** stores everything you push, including its full history. A private
  repo is visible only to people you invite (and to GitHub itself).
- **Your AI assistant's provider** (Anthropic for Claude Code, OpenAI for Codex)
  receives whatever files and text the assistant reads.
- **Model and data APIs you configure** (TypeSafe or OpenRouter for Jev, Anthropic,
  Firecrawl, Exa, DataForSEO, Blitz and the rest in `TOOLS.md`) receive the text
  you send them: company names, domains, page text, sometimes transcripts.

Nothing in this repo sends data anywhere you have not configured, but "private"
means "not public", not "never leaves your machine".

## What goes in git, and what never does

| Goes in your private repo | Never goes in git (already in `.gitignore`) |
|---|---|
| `CLAUDE.md`, `wiki-pattern.md` | `.env` (keys) |
| `wiki/index.md`, `wiki/log.md`, `wiki/entities/`, `concepts/`, `syntheses/` | `wiki/raw/` (raw exports: CRM, calls, billing) |
| your own scripts and notes without raw data | `private/`, `out/`, `outputs/`, `dossiers/`, `packets/` |
| | `companies.csv`, `customers.csv`, `icp.json` at the project root |

The wiki pages the AI writes summarize your customers, so they are still
customer data. That is why the repo is private.

## Create the private repo

You need the GitHub CLI, `gh` (install from https://cli.github.com, then run
`gh auth login` once). From your project root:

```bash
git remote rename origin starter     # if you cloned the starter: keep it as "starter"
gh repo create my-gtm-project --private --source=. --remote=origin
```

`--private` is the important word.

## Every time you save: review, stage by name, check, push

Never stage "everything". Look first, then add files by name.

```bash
git status                          # 1. read the list. Stop if .env, wiki/raw/,
                                    #    private/ or a customer CSV appears.
git add CLAUDE.md wiki/entities/ wiki/index.md wiki/log.md   # 2. stage by name
git diff --cached --stat            # 3. exactly what will upload
git commit -m "What changed, in plain words"
git push
```

To prove the ignore rules work before your first push:

```bash
git check-ignore -v .env wiki/raw/example.csv private/example.txt companies.csv
```

Each path should print the `.gitignore` line that hides it.

## Before you EVER make something public

Making a repo public publishes its whole history, not just today's files. A key
you deleted last month is still in the history.

1. Search the current files for customer names, emails, IDs and keys.
2. Search the history too:
   ```bash
   git log -p | grep -i -E "your-customer-name|@yourcustomer\.com|sk-|_KEY="
   ```
3. If anything turns up, do not flip the switch. Start a fresh repo from a clean
   copy of the files instead, and rotate (replace) any key that was ever committed.

## Working with the AI on git

Ask the assistant to commit for you, but read its plan first:

> Show me `git status` and the exact list of files you will stage, by name. Do not
> stage anything under wiki/raw/, private/, out/ or .env, and no CSV at the root.
