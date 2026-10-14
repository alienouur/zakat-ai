"""Fetch every source URL in the knowledge base and check the quoted text really appears there.

Usage: python scripts/verify_sources.py [clone]
Prints one line per source: OK / QUOTE_NOT_FOUND / HTTP <code> / ERROR.
Records whose sources fail should be downgraded to partially_verified or fixed.
"""
from __future__ import annotations

import asyncio
import html as html_mod
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.kb import KnowledgeBase  # noqa: E402
from app.normalize import normalize  # noqa: E402

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) zakat-kb-verifier/0.1"}


def _strip_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return normalize(html_mod.unescape(html))


async def check(client: httpx.AsyncClient, url: str, quote: str | None) -> str:
    try:
        r = await client.get(url, follow_redirects=True, timeout=30)
    except Exception as e:  # network errors
        return f"ERROR {type(e).__name__}"
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    if not quote:
        return "OK (no quote to check)"
    text = _strip_html(r.text)
    q = normalize(quote)
    # accept if the first 6 words of the quote appear contiguously
    words = q.split()
    probe = " ".join(words[:6]) if len(words) >= 6 else q
    return "OK" if probe in text else "QUOTE_NOT_FOUND"


async def main() -> None:
    kb = KnowledgeBase()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    tasks = []
    labels = []
    async with httpx.AsyncClient(headers=HEADERS) as client:
        for rec in kb.records:
            if only and rec.clone != only:
                continue
            for s in rec.sources:
                if s.url:
                    labels.append((rec.id, s.url))
                    tasks.append(check(client, s.url, s.quote))
        results = await asyncio.gather(*tasks)
    bad = 0
    for (rid, url), res in zip(labels, results, strict=True):
        if not res.startswith("OK"):
            bad += 1
        print(f"{res:22} {rid:45} {url[:90]}")
    print(f"\n{len(results)} sources checked, {bad} problems")


if __name__ == "__main__":
    asyncio.run(main())
