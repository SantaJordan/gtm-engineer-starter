#!/usr/bin/env python3
"""
validate_schema.py — JSON-Schema gate + --no-client-leakage check + forbidden-word grep.

Three modes (combinable):

1. --dossier-dir <path> --mode <customer|prospect>
   Validate every <account_id>.json in dossier-dir against the corresponding
   schema. Exit 1 if any file fails.

2. --no-client-leakage --skill-path <path> [--engagement-file <path>]
   Grep the skill's own files (SKILL.md, references/, prompts/, schemas/, config/,
   scripts/) for any token in engagement.client_slug or engagement.tenant_domain.
   Exit 1 if any hit. Used at W0 and W6.

3. --forbidden-words <yaml> --dossier-dir <path>
   Grep every dossier JSON (and HTML if rendered) for forbidden words OUTSIDE
   the notes_from_revops block. Exit 1 if any hit.

Usage examples:

  # W0 leakage check
  python validate_schema.py \\
    --no-client-leakage \\
    --skill-path ./skills/dossier-builder \\
    --engagement-file outputs/dossiers/acme/engagement.json

  # W6 schema validation
  python validate_schema.py \\
    --dossier-dir outputs/dossiers/acme/dossiers/ \\
    --mode customer

  # W6 forbidden-word grep
  python validate_schema.py \\
    --forbidden-words config/forbidden-words.yaml \\
    --dossier-dir outputs/dossiers/acme/dossiers/
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml required. pip install pyyaml", file=sys.stderr)
    sys.exit(127)

try:
    import jsonschema
except ImportError:
    jsonschema = None  # schema validation only fails if requested without it


# Files inside the skill directory that are not subject to leakage check
LEAKAGE_EXEMPT = {"README.md", "examples"}


def cmd_no_client_leakage(skill_path: Path, engagement_file: Path | None) -> int:
    """Grep skill files for any token in engagement.{client_slug, tenant_domain}.
    Hits = leakage = exit 1."""
    if not engagement_file or not engagement_file.exists():
        # No engagement to check against — vacuously pass
        return 0

    eng = json.loads(engagement_file.read_text())
    tokens = set()
    if eng.get("client_slug"):
        tokens.add(eng["client_slug"].lower())
    for d in eng.get("tenant_domain") or []:
        tokens.add(d.lower())
    if not tokens:
        return 0

    hits: list[tuple[Path, str, int, str]] = []
    for f in skill_path.rglob("*"):
        if f.is_dir():
            continue
        rel = f.relative_to(skill_path)
        if any(part in LEAKAGE_EXEMPT for part in rel.parts):
            continue
        if rel.parts[0] in {"tests", "outputs"}:
            continue
        if f.suffix not in {".md", ".json", ".yaml", ".yml", ".py", ".sh"}:
            continue
        try:
            content = f.read_text(errors="ignore")
        except Exception:
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            lc = line.lower()
            for tok in tokens:
                if tok in lc:
                    hits.append((rel, tok, line_no, line.strip()[:200]))

    if hits:
        print(f"LEAKAGE: {len(hits)} hit(s) in skill files for engagement tokens", file=sys.stderr)
        for rel, tok, line_no, snippet in hits[:30]:
            print(f"  {rel}:{line_no}  token={tok!r}  {snippet}", file=sys.stderr)
        if len(hits) > 30:
            print(f"  ...{len(hits) - 30} more", file=sys.stderr)
        return 1
    return 0


def cmd_validate_dossier(dossier_dir: Path, mode: str, schema_dir: Path) -> int:
    """Validate every <id>.json against schemas/dossier.<mode>.schema.json."""
    if jsonschema is None:
        print("ERROR: jsonschema not installed. pip install jsonschema", file=sys.stderr)
        return 127

    schema_path = schema_dir / f"dossier.{mode}.schema.json"
    if not schema_path.exists():
        print(f"ERROR: schema not found: {schema_path}", file=sys.stderr)
        return 2
    schema = json.loads(schema_path.read_text())

    # Resolver so $ref can find sibling schemas in the same dir
    # Every sibling schema is registered by its $id, so a relative $ref
    # resolves locally and nothing is ever fetched over the network.
    from referencing import Registry, Resource   # ships with jsonschema >= 4.18
    resources = []
    for sp in schema_dir.glob("*.schema.json"):
        try:
            sd = json.loads(sp.read_text())
        except json.JSONDecodeError:
            continue
        if sd.get("$id"):
            resources.append((sd["$id"], Resource.from_contents(sd)))
    registry = Registry().with_resources(resources)
    validator = jsonschema.Draft202012Validator(schema, registry=registry)

    failures = []
    n_total = 0
    for f in sorted(dossier_dir.glob("*.json")):
        n_total += 1
        try:
            doc = json.loads(f.read_text())
            err = jsonschema.exceptions.best_match(validator.iter_errors(doc))
            if err is not None:
                raise err
        except jsonschema.ValidationError as e:
            failures.append((f.name, e.message))
        except json.JSONDecodeError as e:
            failures.append((f.name, f"JSON decode error: {e}"))

    if failures:
        print(f"VALIDATION: {len(failures)} of {n_total} dossiers failed schema", file=sys.stderr)
        for name, msg in failures[:20]:
            print(f"  {name}: {msg}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  ...{len(failures) - 20} more", file=sys.stderr)
        return 1
    print(f"OK: {n_total} dossiers schema-valid")
    return 0


def cmd_forbidden_words(forbidden_yaml: Path, dossier_dir: Path) -> int:
    """Grep dossiers for forbidden words OUTSIDE notes_from_revops blocks."""
    cfg = yaml.safe_load(forbidden_yaml.read_text())
    words = cfg.get("words", [])
    phrases = cfg.get("phrases", [])
    allowances = cfg.get("allowances", [])
    allowance_patterns = [re.compile(a["context"], re.IGNORECASE) for a in allowances]

    # Build word-boundary regex
    word_re = re.compile(
        r"\b(" + "|".join(re.escape(w) for w in words) + r")\b",
        re.IGNORECASE,
    ) if words else None

    phrase_re = re.compile(
        r"(" + "|".join(re.escape(p) for p in phrases) + r")",
        re.IGNORECASE,
    ) if phrases else None

    def check_text(text: str, location: str) -> list[tuple[str, str, str]]:
        hits = []
        # Strip allowance contexts before checking
        for pat in allowance_patterns:
            text = pat.sub("", text)
        if word_re:
            for m in word_re.finditer(text):
                hits.append((location, m.group(0), text[max(0, m.start()-40):m.end()+40]))
        if phrase_re:
            for m in phrase_re.finditer(text):
                hits.append((location, m.group(0), text[max(0, m.start()-40):m.end()+40]))
        return hits

    def walk(node, path: list[str]):
        # Skip notes_from_revops subtree entirely
        if path and path[-1] == "notes_from_revops":
            return []
        hits = []
        if isinstance(node, dict):
            for k, v in node.items():
                hits.extend(walk(v, path + [k]))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                hits.extend(walk(v, path + [str(i)]))
        elif isinstance(node, str):
            loc = ".".join(path)
            hits.extend(check_text(node, loc))
        return hits

    total_hits: list[tuple[str, str, str, str]] = []
    n_files = 0
    for f in sorted(dossier_dir.glob("*.json")):
        n_files += 1
        try:
            doc = json.loads(f.read_text())
        except Exception:
            continue
        for loc, word, snippet in walk(doc, []):
            total_hits.append((f.name, loc, word, snippet))

    if total_hits:
        print(f"FORBIDDEN: {len(total_hits)} hit(s) across {n_files} dossiers", file=sys.stderr)
        for name, loc, word, snippet in total_hits[:30]:
            print(f"  {name} {loc}: {word!r}  ...{snippet.strip()}...", file=sys.stderr)
        if len(total_hits) > 30:
            print(f"  ...{len(total_hits) - 30} more", file=sys.stderr)
        return 1
    print(f"OK: {n_files} dossiers free of forbidden words")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dossier-dir", type=Path)
    ap.add_argument("--mode", choices=["customer", "prospect"])
    ap.add_argument("--no-client-leakage", action="store_true")
    ap.add_argument("--skill-path", type=Path)
    ap.add_argument("--engagement-file", type=Path)
    ap.add_argument("--forbidden-words", type=Path)
    args = ap.parse_args()

    rc = 0

    if args.no_client_leakage:
        if not args.skill_path:
            print("ERROR: --skill-path required with --no-client-leakage", file=sys.stderr)
            return 2
        rc |= cmd_no_client_leakage(args.skill_path, args.engagement_file)

    if args.dossier_dir and args.mode:
        # Find schema dir as sibling
        if args.skill_path:
            schema_dir = args.skill_path / "schemas"
        else:
            # default: look for schemas/ next to this script's parent
            schema_dir = Path(__file__).parent.parent / "schemas"
        rc |= cmd_validate_dossier(args.dossier_dir, args.mode, schema_dir)

    if args.forbidden_words:
        if not args.dossier_dir:
            print("ERROR: --dossier-dir required with --forbidden-words", file=sys.stderr)
            return 2
        rc |= cmd_forbidden_words(args.forbidden_words, args.dossier_dir)

    if not (args.no_client_leakage or args.dossier_dir or args.forbidden_words):
        ap.print_help()
        return 2

    return rc


if __name__ == "__main__":
    sys.exit(main())
