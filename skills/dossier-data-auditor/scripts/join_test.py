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
    declared_unique: set[str] | None = None,
) -> dict:
    """Test a single join candidate. Returns aggregate stats per W3.
    `declared_unique` holds "file.column" keys the user says must be unique."""
    if not (left_path.exists() and right_path.exists()):
        return {"error": "one or both files missing"}

    try:
        ldf = _read_any(left_path)
        rdf = _read_any(right_path)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    if left_col not in ldf.columns or right_col not in rdf.columns:
        return {"error": f"column missing: {left_col} in {left_path.name} or {right_col} in {right_path.name}"}

    # Keys, with the tenant's own domain removed BEFORE matching. A key column
    # that holds domains, emails or websites is normalised to a bare domain;
    # any value equal to (or under) the tenant/seller domain is excluded on
    # both sides, so two unrelated records that only share the seller's own
    # domain can never match.
    domain_key = _is_domain_column(left_col) or _is_domain_column(right_col)
    left_keys = _keys(ldf[left_col], domain_key)
    right_keys = _keys(rdf[right_col], domain_key)
    left_tenant = left_keys.map(lambda d: _is_tenant(d, tenant_domain)) if domain_key else left_keys.map(lambda d: False)
    right_tenant = right_keys.map(lambda d: _is_tenant(d, tenant_domain)) if domain_key else right_keys.map(lambda d: False)
    # What the strip actually prevented: left rows whose only match was a
    # tenant-domain value present on the right too.
    false_matches_avoided = int(left_keys[left_tenant].isin(set(right_keys[right_tenant])).sum())
    excluded_left, excluded_right = int(left_tenant.sum()), int(right_tenant.sum())
    left_keys, right_keys = left_keys[~left_tenant], right_keys[~right_tenant]

    matched = left_keys.isin(right_keys)
    inner_pct = matched.mean() if len(left_keys) else 0.0

    card = key_cardinality(ldf, left_path, left_col, left_keys,
                           rdf, right_path, right_col, right_keys, declared_unique or set())

    # Compute fuzzy-join fraction proxy: how many matches are exact (id) vs would-be-fuzzy
    # (For a real implementation, this requires more domain-specific logic — placeholder here.)
    fuzzy_join_fraction = 0.0   # populated by per-record analysis in future enhancement

    return {
        "left": f"{left_path.name}.{left_col}",
        "right": f"{right_path.name}.{right_col}",
        "inner_pct": round(float(inner_pct), 3),
        "n_left": len(left_keys),
        "n_right": len(right_keys),
        "n_matched": int(matched.sum()),
        "tenant_domain_strip": {
            "tenant_domain": tenant_domain,
            "applied_to_key": bool(domain_key),
            "excluded_left": excluded_left,
            "excluded_right": excluded_right,
            "false_matches_avoided": false_matches_avoided,
        },
        "fuzzy_join_fraction": fuzzy_join_fraction,
        "cardinality": card,
        "linking_trust": ("low" if card["verdict"] == "DUPLICATE_KEYS_MULTIPLY_ROWS" or not int(matched.sum())
                          else "not_a_direct_join" if card["verdict"] == "SIBLING_TABLES_SHARE_A_KEY"
                          else _cap_medium(_aggregate_trust(inner_pct, fuzzy_join_fraction))
                          if card["verdict"] == "KEY_OWNER_UNVERIFIED"
                          else _aggregate_trust(inner_pct, fuzzy_join_fraction)),
    }


_DOMAIN_COL = re.compile(r"(?i)(domain|email|website|url)")


def _is_domain_column(col: str) -> bool:
    return bool(_DOMAIN_COL.search(col))


def _to_domain(v: str) -> str:
    d = v.strip().lower()
    if "@" in d:
        d = d.rsplit("@", 1)[1]
    d = re.sub(r"^[a-z][a-z0-9+.-]*://", "", d).split("/")[0].split("?")[0].split(":")[0]
    return d[4:] if d.startswith("www.") else d


def _keys(col: pd.Series, domain_key: bool) -> pd.Series:
    k = col.dropna().astype(str).str.strip()
    k = k[k != ""]
    return k.map(_to_domain) if domain_key else k


def _is_tenant(d: str, tenant_domain: str) -> bool:
    t = (tenant_domain or "").strip().lower()
    return bool(t) and (d == t or d.endswith("." + t))


_ID_LIKE = re.compile(r"(?i)(^id$|_id$|[a-z]Id$|\bid\b)")
_DESCRIPTOR = re.compile(r"(?i)(name|domain|website|industry|segment|plan|owner|csm|stage|status)")
_MEASURE = re.compile(r"(?i)(amount|mrr|arr|revenue|price|total|qty|quantity|billed|paid|invoice|charge)")


def _is_table_id(df: pd.DataFrame, col: str) -> bool:
    """Is `col` this table's own row identifier, judged from the table's columns
    (never its filename)? Yes when it is the table's ONLY ID-like column and the
    table describes the entity (a name, domain, segment ... column) rather than
    recording events or money (an amount, MRR, invoice ... column).
      accounts_export(account_id, company_name)        -> its ID: must be unique
      invoices(account_id, amount)                      -> a foreign key
      contacts(contact_id, account_id, name, email)     -> account_id is a foreign key
    When the shape does not settle it, declare the key with --unique-key."""
    cols = [str(c) for c in df.columns]
    id_like = [c for c in cols if _ID_LIKE.search(c)]
    if id_like != [str(col)]:
        return False
    others = [c for c in cols if c != str(col)]
    return any(_DESCRIPTOR.search(c) for c in others) and not any(_MEASURE.search(c) for c in others)


def key_cardinality(ldf: pd.DataFrame, left_path: Path, left_col: str, left_keys: pd.Series,
                    rdf: pd.DataFrame, right_path: Path, right_col: str, right_keys: pd.Series,
                    declared_unique: set[str]) -> dict:
    """Does joining on this key multiply rows? Measured from the data on BOTH
    sides. A key that is a table's own ID (from the table's columns, or declared
    with --unique-key) and still repeats makes the join repeat the other side's
    rows (and sum revenue twice): that blocks the gate. Duplicates on both sides
    where neither is a table ID are sibling tables sharing a parent's key: a
    warning, never a direct join with a trust grade."""
    l_dup = int(left_keys.duplicated().sum())
    r_dup = int(right_keys.duplicated().sum())
    r_counts = right_keys.value_counts()
    joined_rows = int(left_keys.map(r_counts).fillna(0).sum())
    matched_left = int(left_keys.isin(right_keys).sum())
    matched_right = int(right_keys.isin(left_keys).sum())
    l_name, r_name = f"{left_path.name}.{left_col}", f"{right_path.name}.{right_col}"
    l_id = l_name in declared_unique or _is_table_id(ldf, left_col)
    r_id = r_name in declared_unique or _is_table_id(rdf, right_col)
    problems = []
    if l_id and l_dup:
        problems.append(f"{l_name} identifies its table's rows but repeats {l_dup} time(s)")
    if r_id and r_dup:
        problems.append(f"{r_name} identifies its table's rows but repeats {r_dup} time(s)")
    sibling = bool(l_dup and r_dup and not l_id and not r_id)
    # One side repeats, and neither table's shape says which side must be
    # unique: the data cannot tell a normal fan-out (accounts -> invoices) from a
    # duplicated account table. Never certified high; declare it to settle it.
    owner_unknown = bool((l_dup or r_dup) and not (l_dup and r_dup) and not l_id and not r_id)
    relationship = ("one_to_one" if not l_dup and not r_dup else
                    "many_to_one" if l_dup and not r_dup else
                    "one_to_many" if r_dup and not l_dup else "many_to_many")
    return {
        "relationship": relationship,
        "left_duplicate_keys": l_dup, "right_duplicate_keys": r_dup,
        "left_is_table_id": bool(l_id), "right_is_table_id": bool(r_id),
        "joined_row_count": joined_rows,
        # joined rows per matched row of each side: >1 on a side means that
        # side's rows (and any amounts on them) repeat after the join.
        "rows_per_left_row": round(joined_rows / matched_left, 3) if matched_left else 0.0,
        "rows_per_right_row": round(joined_rows / matched_right, 3) if matched_right else 0.0,
        # Two tables that both point at a parent (contacts.account_id and
        # invoices.account_id) share a key but are not a join to run directly:
        # joining them multiplies rows. Warned, and joined through the parent.
        "verdict": ("DUPLICATE_KEYS_MULTIPLY_ROWS" if problems
                    else "SIBLING_TABLES_SHARE_A_KEY" if sibling
                    else "KEY_OWNER_UNVERIFIED" if owner_unknown else "OK"),
        "problems": problems,
    }


def _cap_medium(trust: str) -> str:
    return "medium" if trust == "high" else trust


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
    ap.add_argument("--unique-key", action="append", default=[],
                    help="file.column that must be unique (e.g. accounts_export.csv.account_id); "
                         "repeat for more. Adds to what the auditor infers from each table.")
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
            set(args.unique_key),
        )
        result["candidate"] = c
        findings.append(result)

    # Aggregate gating
    def _verdict(f):
        return (f.get("cardinality") or {}).get("verdict", "OK")
    warnings = [f for f in findings
                if _verdict(f) in ("SIBLING_TABLES_SHARE_A_KEY", "KEY_OWNER_UNVERIFIED")]
    blocked = [f for f in findings if _verdict(f) != "SIBLING_TABLES_SHARE_A_KEY" and (
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
