#!/usr/bin/env bash
# new-engagement.sh <client_slug> [--project-root <path>]
# Scaffolds <project root>/outputs/dossiers/<client_slug>/ (gitignored) with the canonical layout.
# Run from the project root: bash skills/dossier-builder/scripts/new-engagement.sh <slug>

set -euo pipefail

# Resolve the PROJECT root (where START-HERE.md lives), so customer work lands
# inside the project and its .gitignore: --project-root wins, then the nearest
# parent of this script holding START-HERE.md, then the current directory's.
find_root() {
  local d="$1"
  while [[ "$d" != "/" && -n "$d" ]]; do
    if [[ -f "$d/START-HERE.md" ]]; then echo "$d"; return 0; fi
    d="$(dirname "$d")"
  done
  return 1
}
PROJECT_ROOT=""
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-root) PROJECT_ROOT="$2"; shift 2 ;;
    --project-root=*) PROJECT_ROOT="${1#*=}"; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [[ -z "$PROJECT_ROOT" ]]; then
  PROJECT_ROOT="$(find_root "$SCRIPT_DIR" || find_root "$PWD" || true)"
fi
if [[ -z "$PROJECT_ROOT" || ! -d "$PROJECT_ROOT" ]]; then
  echo "ERROR: cannot find the project root (no START-HERE.md above $SCRIPT_DIR or $PWD)." >&2
  echo "       Pass --project-root <path>." >&2
  exit 3
fi
PROJECT_ROOT="$( cd "$PROJECT_ROOT" && pwd )"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <client_slug> [--project-root <path>]" >&2
  exit 1
fi

CLIENT_SLUG="$1"
OUT_DIR="$PROJECT_ROOT/outputs/dossiers/$CLIENT_SLUG"

if [[ -e "$OUT_DIR" ]]; then
  echo "ERROR: $OUT_DIR already exists. Refusing to overwrite." >&2
  exit 2
fi

mkdir -p "$OUT_DIR"/{events,linkage,signals,dq,critique,dossiers}
touch "$OUT_DIR/tiebreakers.log.jsonl"

cat > "$OUT_DIR/engagement.json" <<EOF
{
  "mode": null,
  "client_slug": "$CLIENT_SLUG",
  "tenant_domain": [],
  "primary_id_system": null,
  "anchor_rules": null,
  "do_not_use_fields": [],
  "privacy_flags": {
    "redact_account_names_in_methodology_notes": true,
    "redact_exec_names_in_methodology_notes": true
  },
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "Scaffolded: $OUT_DIR"
echo "Next: fill engagement.json mode + tenant_domain, then continue with W1a."
