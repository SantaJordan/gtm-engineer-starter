#!/usr/bin/env python3
"""Copy the fictional site snapshots into a run folder's page cache.

The `.example` domains in this demo do not exist on the internet, so the packet
builder cannot fetch them. This script writes each snapshot where the builder
looks first (`<run_dir>/page_cache/`), so `build_packets.py --offline` reads
them exactly as it would read pages it had fetched.

    python3 examples/northwind-demo/seed_page_cache.py out/northwind-run
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOTS = HERE / "site-snapshots"


def seed(run_dir: Path) -> int:
    cache = run_dir / "page_cache"
    cache.mkdir(parents=True, exist_ok=True)
    meta = cache / "meta.jsonl"
    lines = []
    for snap in sorted(SNAPSHOTS.glob("*.html")):
        domain = snap.name[: -len(".html")]
        html = snap.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        safe = re.sub(r"[^a-z0-9.\-_]", "_", domain)
        (cache / f"{safe}.html").write_text(html, encoding="utf-8")
        lines.append(json.dumps({
            "domain": domain, "fetched_at": "2026-10-01T00:00:00+00:00",
            "status": 200, "final_url": f"https://{domain}/", "redirect_chain": [],
            "title": title.group(1).strip() if title else "", "tls_names": [],
            "favicon_mmh3": None, "content_len": len(html.encode("utf-8")),
            "from_cache": False}))
    meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    n = seed(Path(sys.argv[1]))
    print(f"seeded {n} page(s) into {sys.argv[1]}/page_cache")
