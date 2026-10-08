#!/usr/bin/env bash
# Bootstrap a new audit directory at <project root>/outputs/audits/<slug>/ (gitignored).
# Usage:  bash scripts/new-audit.sh <slug> <data_dir> <tenant_domain> [expected_icp]

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

SLUG="${1:?usage: bash skills/dossier-data-auditor/scripts/new-audit.sh <slug> <data_dir> <tenant_domain> [expected_icp] [--project-root <path>]}"
DATA_DIR="${2:?missing data_dir}"
TENANT_DOMAIN="${3:?missing tenant_domain}"
EXPECTED_ICP="${4:-unspecified}"

OUT_DIR="$PROJECT_ROOT/outputs/audits/$SLUG"

if [ -d "$OUT_DIR" ]; then
  echo "WARN: $OUT_DIR already exists. Continuing — will overwrite individual files but won't delete the dir."
fi

mkdir -p "$OUT_DIR/audit" "$OUT_DIR/scan"

python3 -c "
import json, time
ctx = {
    'audit_slug': '$SLUG',
    'data_dir': '$DATA_DIR',
    'tenant_domain': '$TENANT_DOMAIN',
    'expected_icp': '$EXPECTED_ICP',
    'priority_order': ['domain', 'joins', 'revenue', 'fill_rate', 'paradox'],
    'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
}
open('$OUT_DIR/audit_context.json', 'w').write(json.dumps(ctx, indent=2))
print(json.dumps(ctx, indent=2))
"

echo ""
echo "[new-audit] Created: $OUT_DIR"
echo "[new-audit] Run W1 next: python3 scripts/scan.py --data-dir '$DATA_DIR' --audit-slug '$SLUG'"
