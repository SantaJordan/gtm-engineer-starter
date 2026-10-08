#!/usr/bin/env python3
"""Tufte fixed-width panel renderer.

Used by every wave to emit the four-block panel:
    HOW I DID IT
    OUTLIERS I'M FLAGGING
    QUESTIONS I NEED YOU TO ANSWER
    WHAT I'LL DO NEXT

Plus utility renderers for tables (with right-aligned numerics + currency).

Banned phrases are checked at emit time and raise ValueError. See
../references/skeptical-voice.md.

Usage as a library:
    from render_panel import Panel, Table

    p = Panel(wave="W2", title="Domain audit complete")
    p.how_i_did_it = [
        "Canonicalized 50,000 domains across 11 columns.",
        "DNS-resolved all (free).",
    ]
    p.section("DOMAIN TRUST DISTRIBUTION", Table(
        headers=["Verdict", "Count", "Pct", "Cost"],
        rows=[...],
        alignments=["left", "right", "right", "right"],
    ))
    p.outliers = [...]
    p.questions = [
        ("Q1", "2,800 unresolved domains. Drop, enrich, or keep?", "enrich (recommended)"),
    ]
    p.what_next = "Continue to W3, gated on Q1-Q3."
    print(p.render())
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


BANNED_PHRASES = [
    r"\bgreat\b",
    r"\bamazing\b",
    r"\bperfect\b",
    r"\bawesome\b",
    r"\bexcellent\b",
    r"looks good",
    r"looks great",
    r"that('s| is) a great idea",
    r"that('s| is) a fantastic point",
    r"i think there might be",
    r"it could potentially",
    r"\bpossibly\b",
    r"maybe consider",
    r"have you thought about",
    r"nobody talks about this",
]
BANNED_RE = re.compile("|".join(BANNED_PHRASES), re.IGNORECASE)

# Truncation widths
MAX_DOMAIN_CHARS = 24
MAX_ID_CHARS = 12
MAX_NAME_CHARS = 18


class SycophancyError(ValueError):
    """Raised when banned phrase detected in panel output."""


def _check_no_banned_phrases(text: str) -> None:
    """Scan text for banned-phrase matches. Raise SycophancyError if found."""
    m = BANNED_RE.search(text)
    if m:
        raise SycophancyError(
            f"Banned phrase {m.group(0)!r} detected in panel output. "
            f"Rewrite per references/skeptical-voice.md."
        )


def truncate_domain(d: str) -> str:
    return d if len(d) <= MAX_DOMAIN_CHARS else d[: MAX_DOMAIN_CHARS - 2] + ".."


def truncate_id(i: str) -> str:
    return i if len(i) <= MAX_ID_CHARS else i[: MAX_ID_CHARS - 2] + ".."


def truncate_name(n: str) -> str:
    return n if len(n) <= MAX_NAME_CHARS else (n.split()[0] + "..")[: MAX_NAME_CHARS]


def format_count(n: int | float) -> str:
    """Comma-separated when >999."""
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,.1f}"
    return f"{int(n):,}"


def format_pct(p: float) -> str:
    """1 decimal place unless exactly 0% or 100%."""
    if p == 0:
        return "0.0%"
    if p == 1.0:
        return "100.0%"
    return f"{p * 100:.1f}%"


def format_usd(amount: float) -> str:
    """$1,200 / $48K / $1.2M shorthand."""
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        if amount >= 10_000:
            return f"${amount / 1_000:.0f}K"
        return f"${amount:,.0f}"
    return f"${amount:.2f}"


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]
    alignments: list[str] = field(default_factory=list)  # 'left' | 'right' per column

    def render(self) -> str:
        """Return a fixed-width table as multi-line text."""
        if not self.rows:
            return ""

        n_cols = len(self.headers)
        if not self.alignments:
            self.alignments = ["left"] * n_cols

        # Compute column widths
        widths = []
        for i, h in enumerate(self.headers):
            w = len(h)
            for row in self.rows:
                if i < len(row):
                    w = max(w, len(str(row[i])))
            widths.append(w)

        lines = []
        # Header
        header_parts = []
        for i, h in enumerate(self.headers):
            if self.alignments[i] == "right":
                header_parts.append(h.rjust(widths[i]))
            else:
                header_parts.append(h.ljust(widths[i]))
        lines.append("  " + "   ".join(header_parts))

        # Rows
        for row in self.rows:
            parts = []
            for i, cell in enumerate(row):
                if i >= n_cols:
                    break
                cell_str = str(cell)
                if self.alignments[i] == "right":
                    parts.append(cell_str.rjust(widths[i]))
                else:
                    parts.append(cell_str.ljust(widths[i]))
            lines.append("  " + "   ".join(parts))

        return "\n".join(lines)


@dataclass
class Section:
    title: str
    table: Table | None = None
    bullets: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class Panel:
    wave: str  # e.g., "W2"
    title: str
    how_i_did_it: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    outliers: list[str] = field(default_factory=list)  # short bullet strings
    questions: list[tuple[str, str, str | None]] = field(default_factory=list)  # (qid, text, recommendation)
    what_next: str = ""

    def section(self, title: str, table: Table | None = None, bullets: list[str] | None = None,
                note: str | None = None) -> None:
        self.sections.append(Section(title=title, table=table, bullets=bullets or [], note=note))

    def render(self) -> str:
        lines = []
        framing = f"=== {self.wave}: {self.title} ==="
        lines.append(framing)
        lines.append("")

        # HOW I DID IT
        lines.append("HOW I DID IT")
        if self.how_i_did_it:
            for b in self.how_i_did_it:
                lines.append(f"  - {b}")
        else:
            lines.append("  (no methodology notes)")
        lines.append("")

        # Optional intermediate sections (tables, etc.)
        for s in self.sections:
            lines.append(s.title)
            if s.table:
                lines.append(s.table.render())
            if s.bullets:
                for b in s.bullets:
                    lines.append(f"  - {b}")
            if s.note:
                lines.append(f"  {s.note}")
            lines.append("")

        # OUTLIERS
        lines.append("OUTLIERS I'M FLAGGING")
        if self.outliers:
            for o in self.outliers:
                lines.append(f"  - {o}")
        else:
            lines.append("  None.")
        lines.append("")

        # QUESTIONS
        lines.append("QUESTIONS I NEED YOU TO ANSWER")
        if self.questions:
            for qid, text, rec in self.questions:
                lines.append(f"  {qid}. {text}")
                if rec:
                    lines.append(f"        Recommend: {rec}")
        else:
            lines.append("  None.")
        lines.append("")

        # WHAT'S NEXT
        lines.append("WHAT I'LL DO NEXT")
        lines.append(f"  {self.what_next or '(no next step set)'}")

        rendered = "\n".join(lines)
        _check_no_banned_phrases(rendered)
        return rendered


def render_quick_table(headers: list[str], rows: Iterable[list], alignments: list[str] = None) -> str:
    """One-shot table renderer for use outside of a Panel."""
    return Table(headers=headers, rows=list(rows), alignments=alignments or []).render()


# ───────── Self-test ─────────

if __name__ == "__main__":
    import sys

    # Demo a W2 domain-audit panel
    p = Panel(wave="W2", title="Domain audit complete")
    p.how_i_did_it = [
        "Canonicalized 50,000 domains across 11 columns.",
        "DNS-resolved all (free).",
        "Multi-provider spot-check on 50 random: Serper + Exa + Firecrawl + Linkup.",
        "Detected parked-page fingerprints on 23 domains.",
    ]
    p.section(
        title="DOMAIN TRUST DISTRIBUTION",
        table=Table(
            headers=["Verdict", "Count", "Pct", "Cost"],
            rows=[
                ["verified", format_count(46000), format_pct(0.920), "-"],
                ["unresolved (DNS fail)", format_count(2800), format_pct(0.056), "-"],
                ["parked / for sale", format_count(100), format_pct(0.002), "-"],
                ["mismatched (providers)", format_count(100), format_pct(0.002), format_usd(0.30)],
                ["multi-collapse merged", format_count(1000), format_pct(0.020), format_usd(2.10)],
                ["---", "---", "---", "---"],
                ["", format_count(50000), format_pct(1.0), format_usd(2.40) + " total"],
            ],
            alignments=["left", "right", "right", "right"],
        ),
    )
    p.outliers = [
        "001XX000003.. roofing-deals.example - parked: 'this domain is for sale'",
        "001XX000003.. yourcompany.com - generic placeholder",
    ]
    p.questions = [
        ("Q1", "2,800 unresolved domains. Drop, enrich via Blitz (~$2.80), or keep low?", "enrich"),
        ("Q2", "23 parked domains. Block from build, or override?", "block"),
    ]
    p.what_next = "Continue to W3 (join inference), gated on Q1-Q2."

    try:
        out = p.render()
        print(out)
    except SycophancyError as e:
        print(f"PANEL REJECTED: {e}", file=sys.stderr)
        sys.exit(1)

    # Sanity test: banned phrase rejection
    bad = Panel(wave="W2", title="test")
    bad.how_i_did_it = ["Looks great!"]
    try:
        bad.render()
        print("ERROR: banned phrase check failed to fire", file=sys.stderr)
        sys.exit(1)
    except SycophancyError:
        print("\n[self-test] banned-phrase check fired correctly")
