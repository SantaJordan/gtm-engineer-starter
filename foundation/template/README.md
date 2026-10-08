# Project template

Your project is a clone of the starter repo, so the skills and scripts are
already there. From the project root (`~/gtm` in START-HERE.md), copy these in:

```bash
cp foundation/template/CLAUDE.md CLAUDE.md
cp foundation/wiki-pattern.md wiki-pattern.md
cp -R foundation/template/wiki wiki
mkdir -p private
```

Then open `CLAUDE.md` and replace every `{{placeholder}}`. Drop your first exports
into `wiki/raw/` and ask the assistant to ingest them (see
`foundation/connect-your-context.md`).

What is here:
- `CLAUDE.md`: the project's instructions to the AI. Keep it short.
- `wiki-pattern.md`: a copy of `foundation/wiki-pattern.md`.
- `wiki/`: `raw/` (your inbox, never edited, never committed), `entities/`,
  `concepts/`, `syntheses/` (written by the AI), `index.md` (catalog) and
  `log.md` (history).
- `.gitignore`: the same rules as the starter's own (keeps `.env`, `wiki/raw/`,
  `private/` and customer CSVs out of git). Use it if you ever start a project
  outside a clone of the starter.
- `.env.example`: key names only.
