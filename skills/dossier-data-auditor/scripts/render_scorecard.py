#!/usr/bin/env python3
"""
render_scorecard.py — W7: produce the terminal artifacts.
  scorecard.txt (fixed-width plaintext)
  scorecard.html (web-rendered same content)
  recommended_engagement_overrides.json (the load-bearing handshake — dossier-builder/SKILL.md:73-75 reads this)
  audit_findings.json (full machine-readable record)
  cost_summary.json (per-provider spend)

Usage:
    uv run render_scorecard.py --audit-slug <slug>
        [--tenant-domain ...] [--claimed-primary-id-system sfdc]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def safe_load(p: Path):
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def render_text(findings: dict) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("DOSSIER DATA AUDITOR — SCORECARD")
    lines.append(f"Audit slug: {findings.get('audit_slug')}")
    lines.append(f"Audited at: {findings.get('audited_at')}")
    lines.append("=" * 78)
    lines.append("")

    # W1: Scan
    files = findings.get("scan", {}).get("files_count", 0)
    cols = findings.get("scan", {}).get("column_count", 0)
    lines.append(f"W1 SCAN              files={files}  columns={cols}")

    # W2: Domain
    dom = findings.get("domain", {}) or {}
    if dom:
        lines.append(f"W2 DOMAINS           by_state={dom.get('by_state', {})}")

    # W3: Joins
    joins = (findings.get("join_verdicts", {}) or {}).get("summary", {})
    if joins:
        lines.append(f"W3 JOINS             reject={joins.get('reject_count', 0)}  "
                     f"trust={joins.get('trust_count', 0)}  "
                     f"demote={joins.get('demote_count', 0)}  "
                     f"pause={joins.get('pause_count', 0)}")

    # W4: Revenue
    rev = findings.get("revenue", {}) or {}
    if rev:
        n_clusters = len(rev.get("clusters", []))
        n_apple = len(rev.get("apple_at_20_hits", []))
        lines.append(f"W4 REVENUE           clusters_scored={n_clusters}  apple_at_20_hits={n_apple}")

    # W5: Column fitness (column_metrics is a list of per-column dicts)
    cf_list = findings.get("column_metrics", []) or []
    if cf_list:
        modal = sum(1 for c in cf_list if c.get("modal_spike", {}).get("flagged"))
        sci = sum(1 for c in cf_list if (c.get("scientific_notation_count") or 0) > 0)
        nulls = sum(1 for c in cf_list if len(c.get("null_encodings_observed") or []) > 1)
        lines.append(f"W5 COLUMN FITNESS    columns={len(cf_list)}  modal_spikes={modal}  sci_notation_cols={sci}  null_diverse={nulls}")

    # W5b: Contact fill
    cfa = findings.get("contact_fill_audit", {}) or {}
    if cfa and cfa.get("total_contacts"):
        lines.append(f"W5b CONTACT FILL     accts_with_contacts_pct={cfa.get('accounts_with_contacts_pct', 0)}  "
                     f"linkedin_fill={cfa.get('accounts_with_linkedin_pct', 0)}")

    # W5.5: Hierarchy
    h = findings.get("hierarchy", {}) or {}
    if h.get("hierarchy_fields_detected"):
        lines.append(f"W5.5 HIERARCHY       self_parent_count={h.get('self_parent_count', 0)}  "
                     f"dba_collisions={len(h.get('dba_collisions_among_siblings', []))}")

    # W6: Paradox + ICP
    p = findings.get("paradox", {}) or {}
    if p:
        icp = p.get("icp_cohort_sanity", {}) or {}
        lines.append(f"W6 PARADOX+ICP       icp_fit_pct={icp.get('in_icp_pct', 'n/a')}  "
                     f"verdict={icp.get('verdict', 'n/a')}")

    lines.append("")
    lines.append("=" * 78)
    lines.append(f"PRE-BUILD GO/NO-GO: {findings.get('verdict', 'PENDING')}")
    lines.append(f"NEXT STEP: {findings.get('next_step', 'Run dossier-builder with the recommended overrides.')}")
    lines.append("=" * 78)
    return "\n".join(lines)


def render_html(text: str) -> str:
    safe = text.replace("&", "&amp;").replace("<", "&lt;")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Audit scorecard</title>
<style>body{{font-family:'SF Mono','Menlo','Consolas',monospace;background:#fafafa;color:#222;padding:24px;}}
pre{{background:#fff;border:1px solid #e0e0e0;padding:18px;border-radius:6px;overflow:auto;}}</style>
</head><body><pre>{safe}</pre></body></html>"""


def build_overrides(findings: dict, args) -> dict:
    """The load-bearing handshake schema. dossier-builder/SKILL.md:73-75 reads this.
    Old keys preserved; new keys are additive."""
    revenue_picks = {}
    do_not_use_fields = []
    for cluster in (findings.get("revenue", {}) or {}).get("clusters", []):
        cid = cluster.get("cluster")
        pick = cluster.get("canonical_pick")
        if cid and pick:
            revenue_picks[cid] = pick
        # Each rejected candidate column → do_not_use_fields[] (format {field_path, reason})
        for cand in cluster.get("candidate_columns", []):
            if cand.get("rejected"):
                # Find the reason (first negative factor)
                neg_factors = [f for f in cand.get("factors", []) if isinstance(f, list) and len(f) > 1 and f[1] < 0]
                reason = ", ".join(f[0] for f in neg_factors) if neg_factors else "scored below threshold"
                do_not_use_fields.append({"field_path": cand["field_path"], "reason": reason})

    rejected_joins = []
    for j in ((findings.get("join_verdicts", {}) or {}).get("joins", []) or []):
        if j.get("reconciled_final") == "REJECT":
            rejected_joins.append({
                "from": f"{j['source']['file']}::{j['source']['column']}",
                "to": f"{j['target']['file']}::{j['target']['column']}",
                "reason": j.get("hat1_diagnostic_class") or j.get("hat4_rationale", "")[:200],
                "verdict": j.get("hat1_verdict"),
            })

    multi_tenant_blocklist = []
    sdg = (findings.get("join_verdicts", {}) or {}).get("self_domain_guard", {}) or {}
    for item in sdg.get("multi_tenant_blocklist", []):
        multi_tenant_blocklist.append(item.get("domain"))

    canonical_domain_per_account = {}
    for f in findings.get("domain_state_findings", []) or []:
        key = f.get("account_row_key")
        if key and f.get("canonical_domain"):
            canonical_domain_per_account[key] = {
                "domain": f["canonical_domain"],
                "state": f.get("state", "unknown"),
                "confidence": f.get("confidence", "low"),
            }

    multi_domain_clusters = (findings.get("multi_domain_clusters", {}) or {}).get("clusters", [])

    name_clusters = (findings.get("name_clusters", {}) or {}).get("clusters", [])
    account_name_resolutions = {
        c["cluster_id"]: {
            "display_name": c.get("recommended_display_name"),
            "legal_name": c.get("recommended_legal_name"),
        }
        for c in name_clusters
    }

    # Hierarchy block: loaded under findings["hierarchy"]
    hierarchy = findings.get("hierarchy") or {}
    account_hierarchy_out = None
    if hierarchy.get("hierarchy_fields_detected"):
        account_hierarchy_out = {
            "parent_map": hierarchy.get("parent_map_sample", {}),
            "self_parent_count": hierarchy.get("self_parent_count", 0),
            "dba_collisions": hierarchy.get("dba_collisions_among_siblings", []),
            "rollup_directive": None,  # filled by W5.5 sub-agent
        }

    id_audit = findings.get("id_system_audit") or {}

    return {
        # Existing keys (preserved for back-compat)
        "do_not_use_fields": do_not_use_fields,
        "tenant_domain": args.tenant_domain or [],
        "primary_id_system": args.claimed_primary_id_system,
        "rejected_joins": rejected_joins,
        "canonical_revenue_picks": revenue_picks,
        "per_account_linking_trust": findings.get("per_account_linking_trust", {}),
        # New additive keys
        "per_object_canonical_id": id_audit.get("per_object_canonical") if id_audit else None,
        "dual_canonical_zones": id_audit.get("dual_canonical_zones", []) if id_audit else [],
        "account_hierarchy": account_hierarchy_out,
        "name_clusters": name_clusters,
        "account_name_resolutions": account_name_resolutions,
        "canonical_domain_per_account": canonical_domain_per_account,
        "domain_state_blocklist": findings.get("domain_state_blocklist", []),
        "multi_domain_clusters": multi_domain_clusters,
        "multi_tenant_join_blocklist": multi_tenant_blocklist,
        "column_fitness_verdicts": findings.get("column_fitness_verdicts", {}),
        "column_coercion_rules": findings.get("column_coercion_rules", {}),
        "null_sentinels_per_column": findings.get("null_sentinels_per_column", {}),
        "treat_as_suspect_fields": findings.get("treat_as_suspect_fields", []),
        "contact_fill_audit": findings.get("contact_fill_audit"),
        "user_stated_expectations": findings.get("user_stated_expectations"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--tenant-domain", action="append", default=[])
    parser.add_argument("--claimed-primary-id-system", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    audit_dir = out_dir / "audit"
    scan_dir = out_dir / "scan"
    audit_dir.mkdir(parents=True, exist_ok=True)

    findings = {
        "audit_slug": args.audit_slug,
        "audited_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scan": {
            "files_count": len(safe_load(scan_dir / "files_inventory.json") or []),
            "column_count": len(safe_load(scan_dir / "column_inventory.json") or []),
        },
        "domain_state_findings": safe_load(out_dir / "domain_state_findings.json") or [],
        "multi_domain_clusters": safe_load(out_dir / "multi_domain_clusters.json") or {},
        "id_system_audit": safe_load(out_dir / "id_system_audit_raw.json") or {},
        "join_verdicts": safe_load(audit_dir / "join_verdicts.json") or {},
        "revenue": safe_load(audit_dir / "revenue_findings.json") or {},
        "column_metrics": safe_load(out_dir / "column_metrics_pentad.json") or {},
        "contact_fill_audit": safe_load(audit_dir / "contact_fill_audit.json") or {},
        "hierarchy": safe_load(audit_dir / "account_hierarchy.json") or {},
        "paradox": safe_load(audit_dir / "paradox_findings.json") or {},
        "name_clusters": safe_load(out_dir / "name_clusters.json") or {},
    }

    # Determine overall verdict
    join_summary = (findings.get("join_verdicts", {}) or {}).get("summary", {})
    if join_summary.get("reject_count", 0) > 0:
        findings["verdict"] = "NO_GO"
        findings["next_step"] = "Resolve rejected joins (W3 hard-gate) before running dossier-builder."
    elif join_summary.get("pause_count", 0) > 0:
        findings["verdict"] = "REVIEW_REQUIRED"
        findings["next_step"] = "Address pending PAUSE_HUMAN_REQUIRED verdicts before W4."
    else:
        findings["verdict"] = "GO"
        findings["next_step"] = "Run dossier-builder with the recommended overrides."

    text = render_text(findings)
    (audit_dir / "scorecard.txt").write_text(text)
    (audit_dir / "scorecard.html").write_text(render_html(text))
    (audit_dir / "audit_findings.json").write_text(json.dumps(findings, indent=2, default=str))

    overrides = build_overrides(findings, args)
    (audit_dir / "recommended_engagement_overrides.json").write_text(json.dumps(overrides, indent=2, default=str))

    # Cost summary stub (real cost tracking lives in the providers' clients)
    cost = {
        "total_usd": 0.0,
        "by_provider": {},
        "note": "Cost tracking depends on provider clients persisting usage. Stub for now.",
    }
    (audit_dir / "cost_summary.json").write_text(json.dumps(cost, indent=2))

    print(text)


if __name__ == "__main__":
    main()
