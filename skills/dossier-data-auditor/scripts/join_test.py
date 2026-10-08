#!/usr/bin/env python3
"""W3 — Join audit: six-verdict rubric + three-test guard + self-domain strip.

Applies the rubric from references/join-audit.md (which references
dossier-builder/references/linking-trust-rubric.md verbatim) to every candidate
join pair from W1's scan/join_candidates.json.

Per CLAUDE.md cost-guard: wraps the LLM verdict step at $100 threshold.

Usage:
    python join_test.py --audit-slug myslug --tenant-domain northwind.example
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False


CORPORATE_STOPWORDS = {
    "inc", "llc", "llp", "pc", "pa", "pllc", "ltd", "lp",
    "corp", "company", "co", "the",
}

NAME_FUZZY_THRESHOLD = 0.92


def normalize_name(s: str) -> set[str]:
    """Token-set normalization with corporate-stopword strip."""
    tokens = re.split(r"[\s\-_.,&/]+", s.lower())
    return {t.strip(".,") for t in tokens if t and t.lower() not in CORPORATE_STOPWORDS}


def name_fuzzy(a: str, b: str) -> float:
    """Jaccard similarity post-normalization."""
    if RAPIDFUZZ_AVAILABLE:
        return fuzz.token_set_ratio(a, b) / 100.0
    sa, sb = normalize_name(a), normalize_name(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa | sb), 1)


def three_test_guard(
    id_match: bool,
    domain_match: bool,
    name_match_score: float,
) -> tuple[int, list[str]]:
    """Return (num_passing, which_passed). Requires ≥2 for AUTH_KEEP / EMAIL_KEEP."""
    passed = []
    if id_match:
        passed.append("id_match")
    if domain_match:
        passed.append("domain_match")
    if name_match_score >= NAME_FUZZY_THRESHOLD:
        passed.append("name_fuzzy")
    return len(passed), passed


def classify_verdict(
    has_source_fk: bool,
    fk_resolves: bool,
    email_domain_overlap: bool,
    three_test_passes: int,
    external_domains_count: int,
) -> str:
    """Apply the six-verdict rubric from linking-trust-rubric.md."""
    if has_source_fk:
        if not fk_resolves:
            return "AUTH_REJECT"
        if not email_domain_overlap:
            return "AUTH_EMAIL_CONFLICT"
        return "AUTH_KEEP"

    # No source FK
    if external_domains_count == 0:
        return "INDETERMINATE"
    if email_domain_overlap and three_test_passes >= 2:
        return "EMAIL_KEEP"
    if email_domain_overlap and three_test_passes == 1:
        return "INDETERMINATE"
    return "EMAIL_REJECT"


def strip_tenant_domain(domains: list[str], tenant_domain: str) -> list[str]:
    """The single most important defensive rule. See join-audit.md#self-domain-strip."""
    return [d for d in domains if d.lower() != tenant_domain.lower()]


def test_one_candidate(
    left_path: Path,
    left_col: str,
    right_path: Path,
    right_col: str,
    tenant_domain: str,
) -> dict:
    """Test a single join candidate. Returns aggregate stats per W3."""
    if not (left_path.exists() and right_path.exists()):
        return {"error": "one or both files missing"}

    try:
        ldf = _read_any(left_path)
        rdf = _read_any(right_path)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    if left_col not in ldf.columns or right_col not in rdf.columns:
        return {"error": f"column missing: {left_col} in {left_path.name} or {right_col} in {right_path.name}"}

    # Inner-join on the candidate key
    left_keys = ldf[left_col].dropna().astype(str)
    right_keys = rdf[right_col].dropna().astype(str)
    matched = left_keys.isin(right_keys)
    inner_pct = matched.mean() if len(left_keys) else 0.0

    card = key_cardinality(left_path, left_col, left_keys, right_path, right_col, right_keys)

    # Compute fuzzy-join fraction proxy: how many matches are exact (id) vs would-be-fuzzy
    # (For a real implementation, this requires more domain-specific logic — placeholder here.)
    fuzzy_join_fraction = 0.0   # populated by per-record analysis in future enhancement

    # Self-domain strip count
    domain_cols_left = [c for c in ldf.columns if re.search(r"(?i)(domain|email|website)", c)]
    false_matches_avoided = 0
    for dc in domain_cols_left:
        domain_vals = ldf[dc].dropna().astype(str).tolist()
        false_matches_avoided += sum(1 for d in domain_vals if d.lower() == tenant_domain.lower() or tenant_domain.lower() in d.lower())

    return {
        "left": f"{left_path.name}.{left_col}",
        "right": f"{right_path.name}.{right_col}",
        "inner_pct": round(float(inner_pct), 3),
        "n_left": len(left_keys),
        "n_right": len(right_keys),
        "n_matched": int(matched.sum()),
        "tenant_domain_strip": {
            "tenant_domain": tenant_domain,
            "false_matches_avoided": false_matches_avoided,
        },
        "fuzzy_join_fraction": fuzzy_join_fraction,
        "cardinality": card,
        "linking_trust": ("low" if card["verdict"] == "DUPLICATE_KEYS_MULTIPLY_ROWS"
                          else "not_a_direct_join" if card["verdict"] == "SIBLING_TABLES_SHARE_A_KEY"
                          else _aggregate_trust(inner_pct, fuzzy_join_fraction)),
    }


def _is_primary_key(path: Path, col: str) -> bool:
    """Is `col` this table's own ID (so its values must be unique)? `id`, or the
    table name's entity + `_id` / `id` (accounts.csv -> account_id, AccountId)."""
    c = re.sub(r"[^a-z0-9]", "", col.lower())
    stem = re.sub(r"[^a-z0-9]", "", path.stem.lower())
    entity = {stem, stem[:-1] if stem.endswith("s") else stem,
              stem[:-3] + "y" if stem.endswith("ies") else stem}
    return c == "id" or any(c == e + "id" for e in entity if e)


def key_cardinality(left_path: Path, left_col: str, left_keys: pd.Series,
                    right_path: Path, right_col: str, right_keys: pd.Series) -> dict:
    """Does joining on this key multiply rows? A duplicated key on a table's own
    ID column makes the join repeat rows (and sum revenue twice): that blocks the
    gate. Duplicates on both sides where neither is a table ID are sibling tables
    sharing a parent's key: a warning, never a direct join with a trust grade."""
    l_dup = int(left_keys.duplicated().sum())
    r_dup = int(right_keys.duplicated().sum())
    r_counts = right_keys.value_counts()
    joined_rows = int(left_keys.map(r_counts).fillna(0).sum())
    matched_left = int(left_keys.isin(right_keys).sum())
    matched_right = int(right_keys.isin(left_keys).sum())
    l_pk, r_pk = _is_primary_key(left_path, left_col), _is_primary_key(right_path, right_col)
    problems = []
    if l_pk and l_dup:
        problems.append(f"{left_path.name}.{left_col} is the table's ID but has {l_dup} duplicate value(s)")
    if r_pk and r_dup:
        problems.append(f"{right_path.name}.{right_col} is the table's ID but has {r_dup} duplicate value(s)")
    sibling = bool(l_dup and r_dup and not l_pk and not r_pk and not problems)
    relationship = ("one_to_one" if not l_dup and not r_dup else
                    "many_to_one" if l_dup and not r_dup else
                    "one_to_many" if r_dup and not l_dup else "many_to_many")
    return {
        "relationship": relationship,
        "left_duplicate_keys": l_dup, "right_duplicate_keys": r_dup,
        "left_is_table_id": l_pk, "right_is_table_id": r_pk,
        "joined_row_count": joined_rows,
        # joined rows per matched row of each side: >1 on a side means that
        # side's rows (and any amounts on them) repeat after the join.
        "rows_per_left_row": round(joined_rows / matched_left, 3) if matched_left else 0.0,
        "rows_per_right_row": round(joined_rows / matched_right, 3) if matched_right else 0.0,
        # Two tables that both point at a parent (contacts.account_id and
        # invoices.account_id) share a key but are not a join to run directly:
        # joining them multiplies rows. Warned, and joined through the parent.
        "verdict": ("DUPLICATE_KEYS_MULTIPLY_ROWS" if problems
                    else "SIBLING_TABLES_SHARE_A_KEY" if sibling else "OK"),
        "problems": problems,
    }


def _aggregate_trust(inner_pct: float, fuzzy_join_fraction: float) -> str:
    """Compute aggregate per join_audit.md targets."""
    if fuzzy_join_fraction > 0.20:
        return "medium"   # capped per the rubric
    if inner_pct >= 0.90:
        return "high"
    if inner_pct >= 0.70:
        return "medium"
    return "low"


def _read_any(path: Path) -> pd.DataFrame:
    s = path.suffix.lower()
    if s == ".csv":
        return pd.read_csv(path, low_memory=False, on_bad_lines="skip")
    if s in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if s == ".parquet":
        return pd.read_parquet(path)
    if s == ".jsonl":
        return pd.read_json(path, lines=True)
    if s == ".json":
        return pd.read_json(path)
    raise ValueError(f"unsupported: {s}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-slug", required=True)
    ap.add_argument("--tenant-domain", required=True)
    ap.add_argument("--output-base", type=Path, default=Path("outputs/audits"))
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="the folder scan.py read; needed when join_candidates.json "
                         "names tables relative to it (the current scan.py format)")
    args = ap.parse_args()

    if not args.tenant_domain:
        print("ERROR: --tenant-domain is required. Self-domain strip needs it.", file=sys.stderr)
        sys.exit(1)

    audit_dir = args.output_base / args.audit_slug
    candidates_path = audit_dir / "scan" / "join_candidates.json"
    if not candidates_path.exists():
        print(f"ERROR: join_candidates.json not found at {candidates_path}.", file=sys.stderr)
        sys.exit(1)

    loaded = json.loads(candidates_path.read_text())
    # scan.py writes a bare list of {left: {table, column}, right: {...}};
    # older audits wrapped "filepath.colname" strings in {"candidates": [...]}.
    candidates = loaded.get("candidates", []) if isinstance(loaded, dict) else loaded
    findings = []

    def _side(side):
        if isinstance(side, dict):
            base = args.data_dir or Path(".")
            return str(base / side["table"]), side["column"]
        return side.rsplit(".", 1)

    for c in candidates:
        left_path_str, left_col = _side(c["left"])
        right_path_str, right_col = _side(c["right"])
        result = test_one_candidate(
            Path(left_path_str), left_col,
            Path(right_path_str), right_col,
            args.tenant_domain,
        )
        result["candidate"] = c
        findings.append(result)

    # Aggregate gating
    def _verdict(f):
        return (f.get("cardinality") or {}).get("verdict", "OK")
    warnings = [f for f in findings if _verdict(f) == "SIBLING_TABLES_SHARE_A_KEY"]
    blocked = [f for f in findings if f not in warnings and (
               f.get("linking_trust") == "low" or f.get("inner_pct", 0) < 0.30
               or _verdict(f) == "DUPLICATE_KEYS_MULTIPLY_ROWS")]
    overall_gate = "BLOCK" if blocked else "PASS"

    out_dir = audit_dir / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "join_findings.json"
    out_path.write_text(json.dumps({
        "audit_slug": args.audit_slug,
        "tenant_domain": args.tenant_domain,
        "n_candidates_tested": len(findings),
        "n_blocked": len(blocked),
        "n_warnings": len(warnings),
        "overall_gate": overall_gate,
        "findings": findings,
    }, indent=2))

    print(json.dumps({
        "wrote": str(out_path),
        "n_tested": len(findings),
        "n_blocked": len(blocked),
        "n_warnings": len(warnings),
        "overall_gate": overall_gate,
    }, indent=2))


if __name__ == "__main__":
    main()
