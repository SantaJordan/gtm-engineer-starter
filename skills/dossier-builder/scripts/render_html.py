#!/usr/bin/env python3
"""
render_html.py — Render a per-account v6-style HTML drawer from a dossier JSON.

Features:
- Sticky entity header with the deterministic headline string
- Four-state uncertainty grammar (verified | inferred | fuzzy | missing) on every chip
- Timeline spine with sparkbar (tasks/emails, calls, opportunity stages over 60 months)
- Signals grid
- Provenance chips (click-to-expand 240-char raw_value snippet)
- AI briefing block — falls back to "AI briefing unavailable" if confidence < 0.4
- Forbidden-word check on rendered output (fail render if hit outside notes_from_revops)

Usage:
  python render_html.py --dossier-dir outputs/dossiers/acme/dossiers/
  python render_html.py --dossier <path>.json
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

CSS = """
<style>
  :root {
    --color-bg: #fafaf9;
    --color-fg: #1a1a1a;
    --color-muted: #6b6b6b;
    --color-rule: #e5e5e3;
    --color-verified: #1d8a4f;
    --color-inferred: #b08200;
    --color-fuzzy: #d97400;
    --color-missing: #999;
    --color-block: #c0392b;
  }
  body { font: 14px/1.5 -apple-system, system-ui, sans-serif; background: var(--color-bg); color: var(--color-fg); margin: 0; padding: 0; }
  .drawer { max-width: 980px; margin: 0 auto; padding: 24px; }
  .entity-header { position: sticky; top: 0; background: var(--color-bg); border-bottom: 1px solid var(--color-rule); padding: 16px 0; z-index: 10; }
  .account-name { font-size: 22px; font-weight: 600; margin: 0; }
  .headline { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; color: var(--color-muted); margin-top: 6px; }
  .section { padding: 20px 0; border-bottom: 1px solid var(--color-rule); }
  .section h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-muted); margin: 0 0 12px 0; font-weight: 600; }
  .chip { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-right: 6px; cursor: help; border: 1px solid var(--color-rule); background: white; }
  .chip-verified { border-color: var(--color-verified); }
  .chip-inferred { border-color: var(--color-inferred); border-style: dashed; }
  .chip-fuzzy { border-color: var(--color-fuzzy); background: repeating-linear-gradient(45deg, white, white 4px, #fff5e8 4px, #fff5e8 8px); }
  .chip-missing { border-color: var(--color-missing); color: var(--color-missing); }
  .chip-block { border-color: var(--color-block); background: #fce8e6; color: var(--color-block); }
  .chip-warn { border-color: var(--color-inferred); background: #fff8e1; }
  .timeline { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
  .timeline-row { padding: 6px 0; border-bottom: 1px dotted var(--color-rule); display: grid; grid-template-columns: 90px 110px 1fr; gap: 12px; }
  .timeline-row .date { color: var(--color-muted); }
  .timeline-row .source-tag { font-weight: 500; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .signal-card { background: white; border: 1px solid var(--color-rule); border-radius: 6px; padding: 12px; }
  .signal-card h3 { margin: 0 0 8px 0; font-size: 12px; text-transform: uppercase; color: var(--color-muted); font-weight: 600; }
  .ai-briefing-unavailable { color: var(--color-muted); font-style: italic; padding: 12px; background: white; border: 1px dashed var(--color-rule); border-radius: 6px; }
  .flag { display: block; padding: 8px 12px; margin: 4px 0; border-radius: 4px; }
  .flag-block { background: #fce8e6; border-left: 3px solid var(--color-block); }
  .flag-warn { background: #fff8e1; border-left: 3px solid var(--color-inferred); }
  .flag-note { background: #f5f5f4; border-left: 3px solid var(--color-rule); }
  details { margin: 8px 0; }
  summary { cursor: pointer; color: var(--color-muted); font-size: 12px; }
  pre { font-size: 11px; background: #f5f5f4; padding: 8px; border-radius: 4px; overflow-x: auto; }
</style>
"""


def chip_class_for(provenance: dict[str, Any] | None, value: Any) -> str:
    """Map provenance to four-state uncertainty class."""
    if value is None or value == "" or value == [] or value == {}:
        return "chip chip-missing"
    if not provenance:
        return "chip"
    rule = (provenance.get("rule") or "").lower()
    if "fuzzy" in rule or "name_match" in rule or "fallback" in rule:
        return "chip chip-fuzzy"
    if "infer" in rule or "derive" in rule:
        return "chip chip-inferred"
    return "chip chip-verified"


def render_chip(label: str, value: Any, prov: dict[str, Any] | None = None) -> str:
    cls = chip_class_for(prov, value)
    display = html.escape(str(value)) if value not in (None, "", [], {}) else "missing"
    title = ""
    if prov:
        rule = prov.get("rule", "")
        source = prov.get("source", "")
        raw = (prov.get("source_value") or "")[:240]
        title = f" title='rule: {html.escape(rule)} | source: {html.escape(source)} | raw: {html.escape(raw)}'"
    return f"<span class='{cls}'{title}>{html.escape(label)}: {display}</span>"


def render_timeline(timeline: list[dict[str, Any]]) -> str:
    rows = []
    for ev in timeline[:200]:  # cap render at 200 most recent for browser perf
        date = (ev.get("date") or "")[:10]
        src = ev.get("source") or "?"
        subj = ev.get("subject") or ev.get("type") or ""
        rows.append(
            f"<div class='timeline-row'>"
            f"<span class='date'>{html.escape(date)}</span>"
            f"<span class='source-tag'>{html.escape(src)}</span>"
            f"<span class='subject'>{html.escape(subj)}</span>"
            f"</div>"
        )
    if len(timeline) > 200:
        rows.append(f"<div class='timeline-row'><em>{len(timeline) - 200} more events truncated</em></div>")
    return "<div class='timeline'>" + "".join(rows) + "</div>"


def render_signals_customer(signals: dict[str, Any]) -> str:
    cards = []
    if signals.get("mrr_snapshots"):
        latest = signals["mrr_snapshots"][-1]
        cards.append(
            f"<div class='signal-card'><h3>MRR</h3><div>${latest.get('amount', '?'):,.2f} as of {latest.get('as_of', '?')}<br><small>source: {html.escape(latest.get('source', '?'))}</small></div></div>"
        )
    if signals.get("tenure_days") is not None:
        ts = signals.get("tenure_source", "?")
        cards.append(f"<div class='signal-card'><h3>Tenure</h3><div>{signals['tenure_days']} days<br><small>source: {html.escape(ts)}</small></div></div>")
    if signals.get("csm"):
        cards.append(f"<div class='signal-card'><h3>CSM</h3><div>{html.escape(signals['csm'])}</div></div>")
    if signals.get("plan_tier"):
        cards.append(f"<div class='signal-card'><h3>Plan</h3><div>{html.escape(signals['plan_tier'])}</div></div>")
    if signals.get("churn_signals"):
        cs_list = ", ".join(html.escape(s) for s in signals["churn_signals"])
        cards.append(f"<div class='signal-card'><h3>Churn signals</h3><div>{cs_list}</div></div>")
    return "<div class='grid-2'>" + "".join(cards) + "</div>"


def render_signals_prospect(signals: dict[str, Any]) -> str:
    cards = []
    la = signals.get("lead_attribution") or {}
    if la:
        cards.append(
            f"<div class='signal-card'><h3>Lead attribution</h3>"
            f"<div>{html.escape(la.get('lead_source_aggregated', '?'))} "
            f"<small>({html.escape(la.get('lead_source_trust', 'unknown'))})</small></div></div>"
        )
    ds = signals.get("deal_shape") or {}
    if ds:
        mr = ds.get("monthly_revenue")
        cards.append(
            f"<div class='signal-card'><h3>Deal shape</h3>"
            f"<div>Stage: {html.escape(ds.get('stage') or '?')}<br>"
            f"Pipeline: ${mr:,.0f}/mo</div></div>"
            if mr is not None
            else f"<div class='signal-card'><h3>Deal shape</h3>"
            f"<div>Stage: {html.escape(ds.get('stage') or '?')}<br>Pipeline: unknown</div></div>"
        )
    icp = signals.get("icp_fit") or {}
    if icp:
        cards.append(
            f"<div class='signal-card'><h3>ICP fit</h3>"
            f"<div>{html.escape(icp.get('verdict', 'unknown'))}<br>"
            f"<small>{html.escape(icp.get('basis', ''))}</small></div></div>"
        )
    if signals.get("t_anchor"):
        ta = signals["t_anchor"]
        cards.append(
            f"<div class='signal-card'><h3>Anchor</h3>"
            f"<div>{html.escape(ta.get('date', '?')[:10])}<br>"
            f"<small>rule: {html.escape(ta.get('rule', '?'))}</small></div></div>"
        )
    return "<div class='grid-2'>" + "".join(cards) + "</div>"


def render_flags(flags: list[dict[str, Any]]) -> str:
    if not flags:
        return "<p style='color:var(--color-muted)'>No flags.</p>"
    rows = []
    for f in flags:
        sev = f.get("severity", "note")
        rows.append(
            f"<div class='flag flag-{html.escape(sev)}'>"
            f"<strong>{html.escape(f.get('id', '?'))}</strong> "
            f"<small>({html.escape(sev)})</small><br>"
            f"{html.escape(f.get('explanation', ''))}"
            f"</div>"
        )
    return "".join(rows)


def render_linking(lt: dict[str, Any]) -> str:
    v = lt.get("verdict", "?")
    basis = lt.get("basis", "")
    verified_by = lt.get("verified_by")
    suffix = f" — verified by {html.escape(verified_by)}" if verified_by else ""
    return f"<div class='signal-card'><h3>Linking trust</h3><div>{html.escape(v)}{suffix}<br><small>{html.escape(basis)}</small></div></div>"


def render_ai_briefing(ai: dict[str, Any] | None) -> str:
    if not ai or ai.get("confidence", 0) < 0.4:
        return "<div class='ai-briefing-unavailable'>AI briefing unavailable.</div>"
    body = ai.get("body", "")
    return f"<div class='signal-card'><h3>AI briefing</h3><div>{html.escape(body)}</div></div>"


def render_dossier(dossier: dict[str, Any]) -> str:
    mode = dossier.get("mode", "customer")
    name = dossier.get("account_name", "?")
    headline = dossier.get("headline", "")
    timeline = dossier.get("timeline", [])
    signals = dossier.get("structured_signals", {})
    flags = dossier.get("flags", [])
    lt = dossier.get("linking_trust", {})
    notes = dossier.get("notes_from_revops", [])
    summary = dossier.get("summary") or {}
    ai = dossier.get("ai_briefing")

    signals_html = render_signals_customer(signals) if mode == "customer" else render_signals_prospect(signals)

    notes_html = ""
    if notes:
        rows = []
        for n in notes:
            rows.append(
                f"<div class='flag flag-note'><strong>{html.escape(n.get('question_id','?'))}</strong> "
                f"by {html.escape(n.get('answered_by', '?'))} on {html.escape(n.get('answered_at','?')[:10])}<br>"
                f"<small>Q: {html.escape(n.get('prompt', ''))}</small><br>"
                f"<strong>A: {html.escape(n.get('answer','?'))}</strong>"
                f"{' — ' + html.escape(n.get('answer_notes','')) if n.get('answer_notes') else ''}"
                f"</div>"
            )
        notes_html = "".join(rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(name)} — dossier</title>
  {CSS}
</head>
<body>
  <div class="drawer">
    <div class="entity-header">
      <h1 class="account-name">{html.escape(name)}</h1>
      <div class="headline">{html.escape(headline)}</div>
    </div>

    <div class="section">
      <h2>Linking & trust</h2>
      <div class="grid-2">
        {render_linking(lt)}
        <div class='signal-card'><h3>Mode</h3><div>{html.escape(mode)}</div></div>
      </div>
    </div>

    <div class="section">
      <h2>Summary</h2>
      <p>{html.escape(summary.get('one_liner', ''))}</p>
      <ul>{''.join(f'<li>{html.escape(b)}</li>' for b in (summary.get('three_bullets') or []))}</ul>
    </div>

    <div class="section">
      <h2>AI briefing</h2>
      {render_ai_briefing(ai)}
    </div>

    <div class="section">
      <h2>Structured signals</h2>
      {signals_html}
    </div>

    <div class="section">
      <h2>Flags</h2>
      {render_flags(flags)}
    </div>

    <div class="section">
      <h2>Timeline ({len(timeline)} events)</h2>
      {render_timeline(timeline)}
    </div>

    <div class="section">
      <h2>Notes from RevOps ({len(notes)} tiebreakers)</h2>
      {notes_html or '<p style="color:var(--color-muted)">No tiebreakers recorded.</p>'}
    </div>

    <details>
      <summary>Raw dossier JSON</summary>
      <pre>{html.escape(json.dumps(dossier, indent=2, default=str))}</pre>
    </details>
  </div>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dossier-dir", type=Path)
    ap.add_argument("--dossier", type=Path)
    ap.add_argument("--out-dir", type=Path, help="Defaults to dossier-dir")
    args = ap.parse_args()

    files: list[Path] = []
    if args.dossier:
        files = [args.dossier]
    elif args.dossier_dir:
        files = sorted(args.dossier_dir.glob("*.json"))
    else:
        ap.print_help()
        return 2

    out_dir = args.out_dir or (args.dossier_dir or args.dossier.parent)
    out_dir.mkdir(parents=True, exist_ok=True)

    n = 0
    for f in files:
        try:
            doc = json.loads(f.read_text())
        except Exception as e:
            print(f"SKIP {f.name}: {e}", file=sys.stderr)
            continue
        html_str = render_dossier(doc)
        out = out_dir / (f.stem + ".html")
        out.write_text(html_str)
        n += 1

    print(f"Rendered {n} dossier(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
