"""The data contracts between win-loss-rewind phases, in one place.

Two contracts:

1. **Customer** (phase 0 -> every later phase). Input CSVs vary; every row is
   normalized to the same keys before anything reads it:

       customer_id, company, domain, crm_outcome, health, label, outcome

   `outcome` is the VERIFIED outcome every phase studies, one of
   OUTCOMES. It comes from an explicit `label` column when there is one
   (won / lost / churned / 10x), else from `health` (a churned or cancelled
   account is churned even if the CRM deal says won), else from the CRM
   outcome. `crm_outcome` keeps the CRM deal stage as it was.

2. **Archetype** (phase 3 -> phases 4-7). One record per archetype:

       {"_kind": "archetype", "id": int, "name": str, "member_count": int,
        "members": [{"customer_id": str, "holdout": bool, "outcome": str}], ...}

   `load_archetypes()` is the only reader. It also accepts the older shape
   (cluster_id + separate customer_assignment rows) and joins it, and it
   refuses an archetype with no members rather than letting a later phase
   silently do nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

#: The canonical customer CSV (START-HERE steps 5-10 all read this one file).
#: customers_from_dossiers.py writes it; the date columns anchor Phase 6.
CUSTOMER_COLUMNS = ["customer_id", "company_name", "domain", "outcome", "health", "label",
                    "close_date", "first_call_date", "churn_date", "lifetime_revenue",
                    "label_evidence"]

#: Verified outcomes. 10x is a good-fit outcome with exceptional value.
OUTCOMES = ("won", "healthy", "10x", "lost", "churned", "open", "unknown")
GOOD_FIT_OUTCOMES = frozenset({"won", "healthy", "10x"})
NOT_GOOD_FIT_OUTCOMES = frozenset({"lost", "churned"})

_ALIASES = {
    "won": "won", "closed won": "won", "closed-won": "won", "customer": "won",
    "active": "healthy", "healthy": "healthy", "expanding": "healthy",
    "10x": "10x", "10 x": "10x", "ten_x": "10x",
    "lost": "lost", "closed lost": "lost", "closed-lost": "lost",
    "churned": "churned", "churn": "churned", "cancelled": "churned",
    "canceled": "churned",
    # Unresolved states are kept, never turned into won or lost; Phase 6 and
    # the threshold calibration only count GOOD_FIT / NOT_GOOD_FIT outcomes.
    "open": "open", "prospect": "open", "in progress": "open", "pending": "open",
    "unknown": "unknown",
}


def _norm_outcome(value) -> str | None:
    v = " ".join(str(value or "").strip().lower().split())
    return _ALIASES.get(v)


def _first(row: dict, *keys) -> str:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def verified_outcome(row: dict) -> str:
    """label > health (churn beats a CRM 'won') > CRM outcome > unknown."""
    label = _norm_outcome(row.get("label") or row.get("verified_outcome"))
    if label:
        return label
    health = _norm_outcome(row.get("health"))
    if health == "churned":
        return "churned"
    crm = _norm_outcome(row.get("crm_outcome") or row.get("outcome"))
    if crm:
        return crm
    return health or "unknown"


def normalize_customer(row: dict) -> dict:
    """One input row -> the canonical customer record. Extra columns are kept."""
    out = dict(row)
    company = _first(row, "company", "company_name", "account_name", "name")
    out["company"] = company
    out["customer_id"] = _first(row, "customer_id", "account_id", "id") or company
    domain = _first(row, "domain", "website")
    out["domain"] = domain.removeprefix("https://").removeprefix("http://") \
                          .removeprefix("www.").rstrip("/")
    out["crm_outcome"] = _first(row, "crm_outcome") or _first(row, "outcome")
    out["health"] = _first(row, "health")
    out["label"] = _first(row, "label")
    out["outcome"] = verified_outcome(row)
    # Reference-date anchors (reference_dates.py) keep their canonical names.
    for k in ("close_date", "first_call_date", "churn_date", "renewal_date"):
        out[k] = _first(row, k) or None
    return out


# ------------------------------------------------------------------ archetypes

def load_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def archetype_record(summary: dict, member_rows: list[dict], holdout_ids: set) -> dict:
    """Build the canonical archetype record from a cluster summary."""
    rec = dict(summary)
    rec["_kind"] = "archetype"
    rec["id"] = int(summary.get("id", summary.get("cluster_id")))
    rec["cluster_id"] = rec["id"]
    rec["name"] = summary.get("name") or summary.get("archetype_name") or f"archetype-{rec['id']}"
    rec["members"] = [{"customer_id": str(r.get("customer_id")),
                       "holdout": str(r.get("customer_id")) in holdout_ids,
                       "outcome": r.get("outcome") or "unknown"} for r in member_rows]
    rec["member_count"] = len(rec["members"])
    return rec


def load_archetypes(path: Path, *, include_outliers: bool = False) -> list[dict]:
    """Every usable archetype with embedded members. Raises ValueError on a broken contract."""
    rows = load_jsonl(path)
    assignments: dict = {}
    for r in rows:
        if r.get("_kind") == "customer_assignment":
            assignments.setdefault(r.get("cluster_id"), []).append(r)
    out = []
    for r in rows:
        if r.get("_kind") not in (None, "archetype"):
            continue
        if "cluster_id" not in r and "id" not in r:
            continue
        a = dict(r)
        a["id"] = int(a.get("id", a.get("cluster_id")))
        a["name"] = a.get("name") or a.get("archetype_name") or f"archetype-{a['id']}"
        if not a.get("members"):
            joined = assignments.get(a.get("cluster_id", a["id"]), [])
            a["members"] = [{"customer_id": str(m.get("customer_id")),
                             "holdout": bool(m.get("holdout")),
                             "outcome": m.get("outcome") or "unknown"} for m in joined]
        excluded = a.get("_excluded_from_archetypes") or a.get("archetype_quality") == "outlier"
        if excluded and not include_outliers:
            continue
        if not a["members"]:
            raise ValueError(f"archetype {a['id']} has no members: the phase-3 output is "
                             f"broken; re-run segment.py")
        out.append(a)
    return out


def split_ids(archetype: dict) -> tuple[list[str], list[str]]:
    """(train_ids, holdout_ids) for one archetype."""
    train, hold = [], []
    for m in archetype.get("members") or []:
        cid = str(m.get("customer_id"))
        (hold if m.get("holdout") else train).append(cid)
    return train, hold


def training_view(archetype: dict) -> dict:
    """The archetype as hypothesis generation may see it: TRAINING members only.

    Holdout customers, their outcomes and any summary that counted them are
    removed, so the holdout can test the hypotheses honestly afterwards.
    """
    train_ids, _ = split_ids(archetype)
    keep = set(train_ids)
    view = {k: v for k, v in archetype.items()
            if k not in ("members", "outcome_breakdown", "top_3_member_companies")}
    view["members"] = [m for m in archetype.get("members") or []
                       if str(m.get("customer_id")) in keep]
    counts = {o: 0 for o in OUTCOMES}
    for m in view["members"]:
        counts[m.get("outcome") if m.get("outcome") in counts else "unknown"] += 1
    view["outcome_breakdown"] = counts
    view["member_count"] = len(view["members"])
    view["top_3_member_companies"] = [
        c for c in archetype.get("top_3_member_companies") or []
        if str(c.get("customer_id")) in keep]
    return view
