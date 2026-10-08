#!/usr/bin/env python3
"""
Phase 3 — Auto-Segmentation.

Cluster customers on their Phase 2 signal panel (NOT on user-supplied industry
tags). Hold out 20% per cluster for downstream backcasting validation. Name
clusters via Claude the current Sonnet (wlr_config.py) using the prompt in
`prompts/archetype-namer.md`.

Outputs:
    outputs/<run-id>/archetypes.jsonl   machine-readable, includes holdout
    outputs/<run-id>/archetypes.md      human-readable

CRITICAL: outcome (won / lost / healthy / churned) is read from the customer
row only — it is *never* a clustering feature. The archetype must be
discoverable from observable signals; outcome is what we check it explains.

Usage:
    python segment.py --run-id 2026-05-05-acme \\
        [--archetype-cap 8] [--min-cluster-size 5] [--seed 42]

TODO: replace KMeans+silhouette with HDBSCAN once we accept the dep
(`pip install hdbscan`). HDBSCAN handles non-globular clusters and gives a
"noise" label natively. KMeans is a pragmatic v0 scaffold.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

# ------------------------------------------------------------------ paths

from wlr_config import repo_root  # noqa: E402
REPO_ROOT = repo_root()
from wlr_config import SONNET_MODEL  # noqa: E402
import wlr_schema  # noqa: E402
SKILL_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = SKILL_ROOT / "prompts" / "archetype-namer.md"


def outputs_dir(run_id: str) -> Path:
    return SKILL_ROOT / "outputs" / run_id


# ------------------------------------------------------------------ env

load_dotenv(REPO_ROOT / ".env")
load_dotenv(SKILL_ROOT / ".env")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


# ------------------------------------------------------------------ feature engineering

# Forbidden: anything derived from outcome. Asserted at runtime.
OUTCOME_FIELDS = {"outcome", "won", "lost", "healthy", "churned", "10x", "label",
                  "health", "close_date"}


def _safe_int(x: Any) -> int:
    try:
        if x is None:
            return 0
        if isinstance(x, bool):
            return int(x)
        return int(x)
    except (TypeError, ValueError):
        return 0


def _safe_float(x: Any) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def extract_features(panel_row: dict) -> dict[str, float]:
    """Flatten the signal panel into a numeric dict.

    Keys returned here become the feature space for KMeans. Document any
    non-obvious mapping inline. Outcome must NOT appear in this dict.
    """
    sp = panel_row.get("signal_panel") or {}
    feats: dict[str, float] = {}

    # ------- Firecrawl /map -------
    fc_map = (sp.get("firecrawl_map") or {}).get("data") or {}
    feats["fc_page_count"] = _safe_float(fc_map.get("page_count"))
    feats["fc_log_page_count"] = math.log1p(feats["fc_page_count"])
    feats["fc_has_careers"] = float(bool(fc_map.get("has_careers_page")))
    feats["fc_has_pricing"] = float(bool(fc_map.get("has_pricing_page")))
    feats["fc_has_investor"] = float(bool(fc_map.get("has_investor_page")))
    feats["fc_has_compliance"] = float(bool(fc_map.get("has_compliance_page")))

    # ------- Firecrawl /scrape (homepage) -------
    fc_scrape = (sp.get("firecrawl_scrape") or {}).get("data") or {}
    cy = fc_scrape.get("copyright_year")
    if cy:
        feats["fc_site_age_years"] = max(
            0.0, datetime.now().year - _safe_float(cy)
        )
    else:
        feats["fc_site_age_years"] = 0.0
    feats["fc_meta_desc_len"] = float(len(fc_scrape.get("meta_description") or ""))

    # ------- EXA -------
    exa = (sp.get("exa") or {}).get("data") or {}
    feats["exa_results_count"] = _safe_float(exa.get("count"))

    # ------- OpenWebNinja -------
    own = (sp.get("openweb_ninja") or {}).get("data") or {}
    feats["own_rating"] = _safe_float(own.get("rating"))
    feats["own_reviews"] = _safe_float(own.get("reviews_count"))
    feats["own_log_reviews"] = math.log1p(feats["own_reviews"])
    feats["own_has_phone"] = float(bool(own.get("phone")))

    # ------- Apify LinkedIn -------
    li = (sp.get("apify_linkedin") or {}).get("data") or {}
    feats["li_employees"] = _safe_float(li.get("employees_count"))
    feats["li_log_employees"] = math.log1p(feats["li_employees"])

    # ------- Serper News -------
    serper = (sp.get("serper_news") or {}).get("data") or {}
    feats["serper_news_count"] = _safe_float(serper.get("count"))

    # ------- Reconciliation flags as binary signals -------
    flags = panel_row.get("reconciliation_flags") or []
    flag_universe = (
        "high_arr_thin_site",
        "high_arr_solo_company",
        "no_domain_resolved",
        "all_enrichment_sources_failed",
    )
    for f in flag_universe:
        feats[f"flag_{f}"] = float(any(s.startswith(f) for s in flags))

    # Outcome guard
    for k in feats:
        assert not any(bad in k for bad in OUTCOME_FIELDS), \
            f"feature {k!r} leaks outcome info; refusing to cluster."

    return feats


def build_feature_matrix(rows: list[dict]) -> tuple[np.ndarray, list[str], list[dict]]:
    feature_dicts = [extract_features(r) for r in rows]
    keys = sorted({k for d in feature_dicts for k in d.keys()})
    matrix = np.array(
        [[d.get(k, 0.0) for k in keys] for d in feature_dicts],
        dtype=float,
    )
    if matrix.size == 0:
        return matrix, keys, feature_dicts
    scaler = StandardScaler()
    matrix = scaler.fit_transform(matrix)
    return matrix, keys, feature_dicts


# ------------------------------------------------------------------ clustering

def choose_k_via_silhouette(matrix: np.ndarray, k_min: int, k_max: int, seed: int) -> tuple[int, np.ndarray, float]:
    """Sweep k = k_min..k_max, return best-silhouette k + labels + score."""
    n = matrix.shape[0]
    k_max = min(k_max, max(2, n - 1))
    best = (k_min, None, -1.0)
    for k in range(max(2, k_min), k_max + 1):
        km = KMeans(n_clusters=k, random_state=seed, n_init=10)
        labels = km.fit_predict(matrix)
        if len(set(labels)) < 2:
            continue
        try:
            score = silhouette_score(matrix, labels)
        except Exception:
            continue
        if score > best[2]:
            best = (k, labels, float(score))
    if best[1] is None:
        # fallback: everything one cluster
        return 1, np.zeros(n, dtype=int), 0.0
    return best


# ------------------------------------------------------------------ archetype shaping

def cluster_summary(
    cluster_id: int,
    member_idx: list[int],
    rows: list[dict],
    feature_dicts: list[dict],
    feature_keys: list[str],
    matrix: np.ndarray,
    other_idx: list[int],
) -> dict:
    """Per-cluster aggregates: outcomes, top features, reps, disambiguators."""
    outcomes = {o: 0 for o in wlr_schema.OUTCOMES}
    for i in member_idx:
        o = (rows[i].get("outcome") or "unknown").lower()
        if o not in outcomes:
            o = "unknown"
        outcomes[o] += 1

    # Top distinguishing features: highest mean-z vs other clusters
    if member_idx and other_idx and matrix.size:
        in_mean = matrix[member_idx].mean(axis=0)
        out_mean = matrix[other_idx].mean(axis=0)
        gap = in_mean - out_mean
        # top 5 most-elevated features for this cluster
        top_high = np.argsort(-gap)[:5]
        top_low = np.argsort(gap)[:3]
        canonical = [
            {"feature": feature_keys[i], "in_cluster_z": float(in_mean[i]),
             "out_cluster_z": float(out_mean[i]), "gap": float(gap[i])}
            for i in top_high
        ]
        disambiguating = [
            {"feature": feature_keys[i], "in_cluster_z": float(in_mean[i]),
             "out_cluster_z": float(out_mean[i]), "gap": float(gap[i])}
            for i in top_low
        ]
    else:
        canonical, disambiguating = [], []

    # Three representative companies (closest to cluster centroid in feature space)
    reps: list[dict] = []
    if member_idx and matrix.size:
        sub = matrix[member_idx]
        centroid = sub.mean(axis=0)
        dists = np.linalg.norm(sub - centroid, axis=1)
        order = np.argsort(dists)[:3]
        for idx_within in order:
            r = rows[member_idx[int(idx_within)]]
            reps.append({
                "company": r.get("company"),
                "domain": r.get("domain"),
                "outcome": r.get("outcome"),
                "customer_id": r.get("customer_id"),
            })

    return {
        "cluster_id": cluster_id,
        "member_count": len(member_idx),
        "outcome_breakdown": outcomes,
        "canonical_features": canonical,
        "disambiguating_signals": disambiguating,
        "top_3_member_companies": reps,
    }


# ------------------------------------------------------------------ holdout

def assign_holdout(member_idx: list[int], seed: int, holdout_pct: float = 0.2) -> set[int]:
    """Return the row-indices held out from this cluster."""
    rng = random.Random(seed)
    shuffled = list(member_idx)
    rng.shuffle(shuffled)
    n_holdout = max(1, int(round(len(shuffled) * holdout_pct))) if len(shuffled) >= 5 else 0
    return set(shuffled[:n_holdout])


# ------------------------------------------------------------------ LLM naming

def load_namer_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Prompt missing: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


#: The only keys a naming answer may set. Numeric feature records
#: (`canonical_features`, `disambiguating_signals`) are never overwritten:
#: the model's feature list is kept separately as `named_features`.
NAMING_FIELDS = ("archetype_name", "one_liner", "named_features",
                 "why_this_cluster_is_different_from_others", "spot_check_quote")


def _unnamed(summary: dict, why: str) -> dict:
    return {"archetype_name": f"cluster-{summary['cluster_id']} (unnamed — {why})",
            "one_liner": "", "_naming_skipped": True, "_naming_error": why}


def parse_naming(text: str, summary: dict) -> dict:
    """The namer's YAML -> validated naming fields. Anything unusable -> unnamed."""
    import yaml  # noqa: PLC0415
    body = text or ""
    fence = re.search(r"```(?:yaml)?\s*\n(.*?)```", body, re.S)
    if fence:
        body = fence.group(1)
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        return _unnamed(summary, f"naming answer is not YAML: {exc.__class__.__name__}")
    if not isinstance(data, dict):
        return _unnamed(summary, "naming answer is not a YAML mapping")
    name = data.get("archetype_name")
    if not isinstance(name, str) or not name.strip():
        return _unnamed(summary, "naming answer has no archetype_name")
    out = {"archetype_name": name.strip(),
           "one_liner": str(data.get("one_liner") or "").strip()}
    feats = data.get("canonical_features")
    if isinstance(feats, list):
        out["named_features"] = [str(f) for f in feats][:5]
    why = data.get("why_this_cluster_is_different_from_others")
    if isinstance(why, list):
        out["why_this_cluster_is_different_from_others"] = [str(w) for w in why]
    if data.get("spot_check_quote"):
        out["spot_check_quote"] = str(data["spot_check_quote"])
    out["_raw_yaml"] = text
    return out


def name_cluster_via_llm(summary: dict, system_prompt: str, client=None) -> dict:
    """Name one cluster with the current Sonnet (wlr_config.py). Returns NAMING_FIELDS only.

    `client` is injectable (tests pass a fake). With no key, no SDK or a failed
    call, the cluster is left unnamed and the report still renders.
    """
    if client is None:
        if not ANTHROPIC_API_KEY:
            return _unnamed(summary, "ANTHROPIC_API_KEY missing")
        try:
            import anthropic  # local import so the script can `--help` without the dep
        except ImportError:
            return _unnamed(summary, "anthropic not installed")
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_msg = (
        "Name the following cluster. Output a single YAML block per the "
        "schema in the system prompt. No prose outside the YAML.\n\n"
        f"```yaml\n"
        f"cluster_id: {summary['cluster_id']}\n"
        f"member_count: {summary['member_count']}\n"
        f"outcome_breakdown: {json.dumps(summary['outcome_breakdown'])}\n"
        f"canonical_signals:\n"
    )
    for cs in summary.get("canonical_features", []):
        user_msg += (f"  - feature: {cs['feature']}\n"
                     f"    in_cluster_z: {cs['in_cluster_z']:.2f}\n"
                     f"    gap_vs_others: {cs['gap']:.2f}\n")
    user_msg += "top_3_member_companies:\n"
    for rep in summary.get("top_3_member_companies", []):
        user_msg += (f"  - name: {rep.get('company')}\n"
                     f"    domain: {rep.get('domain')}\n"
                     f"    outcome: {rep.get('outcome')}\n")
    user_msg += "disambiguating_signals_vs_other_clusters:\n"
    for ds in summary.get("disambiguating_signals", []):
        user_msg += f"  - {ds['feature']} (gap {ds['gap']:.2f})\n"
    user_msg += "```"

    try:
        resp = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(getattr(b, "text", "") or "" for b in (resp.content or [])
                       if getattr(b, "type", "text") == "text")
    except Exception as e:  # noqa: BLE001
        return _unnamed(summary, f"naming call failed: {e.__class__.__name__}")
    return parse_naming(text, summary)


# ------------------------------------------------------------------ output

def write_archetypes(rdir: Path, archetypes: list[dict], rows: list[dict],
                     labels: np.ndarray, holdout_set: set[int],
                     silhouette: float, k: int) -> None:
    out_jsonl = rdir / "archetypes.jsonl"
    out_md = rdir / "archetypes.md"

    # JSONL: canonical archetype rows (members embedded, see wlr_schema) +
    # per-customer assignment rows (kept for readers of the older shape).
    holdout_ids = {str(rows[i].get("customer_id")) for i in holdout_set}
    with out_jsonl.open("w") as f:
        for a in archetypes:
            members = [rows[i] for i in range(len(rows)) if int(labels[i]) == a.get("cluster_id")]
            rec = wlr_schema.archetype_record(a, members, holdout_ids)
            f.write(json.dumps(rec) + "\n")
        for i, row in enumerate(rows):
            f.write(json.dumps({
                "_kind": "customer_assignment",
                "customer_id": row.get("customer_id"),
                "company": row.get("company"),
                "outcome": row.get("outcome"),
                "cluster_id": int(labels[i]),
                "holdout": i in holdout_set,
            }) + "\n")

    # Markdown summary
    lines: list[str] = []
    lines.append(f"# Archetypes — Phase 3\n")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append(f"- KMeans k = **{k}**, silhouette = **{silhouette:.3f}**")
    lines.append(f"- Customers: {len(rows)}, holdout: {len(holdout_set)} (~20% per cluster)\n")
    lines.append("| ID | Name | Members | W/L/H/C | Quality |")
    lines.append("|----|------|---------|---------|---------|")
    for a in archetypes:
        ob = a.get("outcome_breakdown", {})
        wlhc = f"{ob.get('won',0)}/{ob.get('lost',0)}/{ob.get('healthy',0)}/{ob.get('churned',0)}"
        lines.append(f"| {a.get('cluster_id')} | {a.get('archetype_name','')} | "
                     f"{a.get('member_count',0)} | {wlhc} | {a.get('archetype_quality','ok')} |")
    lines.append("")

    for a in archetypes:
        lines.append(f"## Archetype {a.get('cluster_id')}: {a.get('archetype_name','(unnamed)')}\n")
        if a.get("one_liner"):
            lines.append(f"_{a['one_liner']}_\n")
        if a.get("_raw_yaml"):
            lines.append("```yaml")
            lines.append(a["_raw_yaml"].strip())
            lines.append("```\n")
        lines.append(f"- **Members:** {a.get('member_count',0)}")
        ob = a.get("outcome_breakdown", {})
        lines.append(f"- **Outcomes:** won={ob.get('won',0)} lost={ob.get('lost',0)} "
                     f"healthy={ob.get('healthy',0)} churned={ob.get('churned',0)} "
                     f"unknown={ob.get('unknown',0)}")
        lines.append(f"- **Quality:** `{a.get('archetype_quality','ok')}`")
        if a.get("archetype_quality") == "low":
            lines.append("  - Recommend: merge with closest archetype or drop. Insufficient "
                         "outcome variance (need ≥5 members across ≥2 outcomes).")
        lines.append(f"\n**Top distinguishing signals:**\n")
        for cs in a.get("canonical_features", []):
            lines.append(f"- `{cs.get('feature')}` — in-cluster z={cs.get('in_cluster_z',0):.2f}, "
                         f"gap vs others={cs.get('gap',0):.2f}")
        lines.append(f"\n**Representative members:**")
        for r in a.get("top_3_member_companies", []):
            lines.append(f"- {r.get('company','?')} — {r.get('domain','?')} "
                         f"({r.get('outcome','?')})")
        lines.append("")

    out_md.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ CLI

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 3 — cluster customers on signal panel features.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--run-id", required=True)
    p.add_argument("--archetype-cap", type=int, default=8,
                   help="max number of archetypes (k upper bound)")
    p.add_argument("--min-cluster-size", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def segment_rows(rows: list[dict], *, seed: int = 42, min_cluster_size: int = 5,
                 archetype_cap: int = 8, namer=None):
    """Cluster panels into archetypes. Returns (archetypes, labels, holdout_set, sil, k).

    `namer(summary) -> dict` names a cluster; None leaves it unnamed. Outcome
    counts and representative members in each summary come from TRAINING
    members only, so neither the namer nor any later phase sees holdout outcomes.
    """
    matrix, feature_keys, feature_dicts = build_feature_matrix(rows)
    k, labels, sil = choose_k_via_silhouette(matrix, k_min=2, k_max=archetype_cap, seed=seed)

    by_cluster: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        by_cluster.setdefault(int(lab), []).append(i)

    holdout_set: set[int] = set()
    for cid, members in by_cluster.items():
        if len(members) >= min_cluster_size:
            holdout_set |= assign_holdout(members, seed=seed + cid, holdout_pct=0.2)

    archetypes: list[dict] = []
    for cid in sorted(by_cluster.keys()):
        members = by_cluster[cid]
        others = [i for i in range(len(rows)) if i not in members]
        if len(members) < min_cluster_size:
            archetypes.append({
                "cluster_id": cid, "archetype_name": f"cluster-{cid} (outlier)",
                "archetype_quality": "outlier", "member_count": len(members),
                "outcome_breakdown": {}, "canonical_features": [],
                "disambiguating_signals": [], "top_3_member_companies": [],
                "_excluded_from_archetypes": True,
            })
            continue
        train_members = [i for i in members if i not in holdout_set]
        summary = cluster_summary(cid, train_members, rows, feature_dicts, feature_keys,
                                  matrix, others)
        summary["member_count"] = len(members)
        summary["train_count"] = len(train_members)
        ob = summary["outcome_breakdown"]
        nonzero = sum(1 for k_, v_ in ob.items() if k_ != "unknown" and v_ > 0)
        quality = "ok" if (len(members) >= 5 and nonzero >= 2) else "low"
        summary["archetype_quality"] = quality
        named = namer(summary) if namer else {}
        merged = {**summary, **{k: v for k, v in (named or {}).items()
                                if k in NAMING_FIELDS or k.startswith("_naming") or k == "_raw_yaml"}}
        merged.setdefault("archetype_quality", quality)
        archetypes.append(merged)
    return archetypes, labels, holdout_set, sil, k


def main() -> int:
    args = parse_args()
    rdir = outputs_dir(args.run_id)
    panels_path = rdir / "customer_panels.jsonl"
    if not panels_path.exists():
        print(f"ERROR: {panels_path} not found. Run enrich_panel.py first.", file=sys.stderr)
        return 2

    rows = [wlr_schema.normalize_customer(r) for r in wlr_schema.load_jsonl(panels_path)]
    print(f"[segment] loaded {len(rows)} customer panels from {panels_path}")
    if len(rows) < args.min_cluster_size * 2:
        print(f"ERROR: only {len(rows)} customers; need at least "
              f"{args.min_cluster_size * 2} to cluster meaningfully.", file=sys.stderr)
        return 2

    try:
        system_prompt = load_namer_prompt()
    except FileNotFoundError as e:
        print(f"WARN: {e}; archetypes will be unnamed.", file=sys.stderr)
        system_prompt = ""
    namer = (lambda summ: name_cluster_via_llm(summ, system_prompt)) if system_prompt else None

    archetypes, labels, holdout_set, sil, k = segment_rows(
        rows, seed=args.seed, min_cluster_size=args.min_cluster_size,
        archetype_cap=args.archetype_cap, namer=namer)
    print(f"[segment] best k={k}  silhouette={sil:.3f}")

    write_archetypes(rdir, archetypes, rows, labels, holdout_set, sil, k)
    n_low = sum(1 for a in archetypes if a.get("archetype_quality") == "low")
    n_outlier = sum(1 for a in archetypes if a.get("archetype_quality") == "outlier")
    print(f"[segment] wrote {rdir / 'archetypes.jsonl'} and {rdir / 'archetypes.md'}")
    print(f"  archetypes: {len(archetypes)} (ok={len(archetypes)-n_low-n_outlier} "
          f"low={n_low} outlier={n_outlier})")
    print(f"  holdout: {len(holdout_set)} customers (~20%/cluster, seed={args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
