"""Builds the `<knowledge>` block for a question from the `libya_zakat_fund` dataset only."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from app.kb import KnowledgeBase
from app.retrieval import Hit, Retriever
from app.topics import detect_topics

CLONE = "libya_zakat_fund"
EMPTY_BLOCK = "<knowledge>لا توجد مقاطع مطابقة</knowledge>"
_ARTICLE_NUM = re.compile(r"المادة\s*\(?\s*(\d+)")
_ARTICLE_PAGE = re.compile(r"المادة\s*\(?\s*(\d+)[^()\n]{0,25}?ص\s*\.?\s*(\d+)")
_FOLLOW_UP_STARTS = ("و", "وكم", "وما", "وهل", "وماذا", "طيب", "يعني", "كم", "هل", "ولو", "وإذا", "واذا")


@dataclass
class Knowledge:
    block: str
    hits: list[Hit] = field(default_factory=list)
    query: str = ""

    @property
    def citations(self) -> list[dict]:
        return citations_for(self.hits)


def _article_of(hit: Hit) -> tuple[str, str]:
    src = hit.record.sources[0] if hit.record.sources else None
    return (getattr(src, "chapter", "") or "", str(getattr(src, "page", "") or "")) if src else ("", "")


def citations_for(hits: list[Hit], reply: str | None = None) -> list[dict]:
    """[{article, page}] for the hits; when a reply is given, keep only the articles it actually cites."""
    seen: list[dict] = []
    for h in hits:
        for src in h.record.sources or []:
            c = {"article": getattr(src, "chapter", "") or "", "page": str(getattr(src, "page", "") or "")}
            if c["article"] and c not in seen:
                seen.append(c)
    if reply is not None:
        # only articles the reply actually cites; a reply with no article reference (greeting, confirmation) gets none
        cited = set(_ARTICLE_NUM.findall(reply))
        pages: dict[str, set[str]] = {}
        for art, pg in _ARTICLE_PAGE.findall(reply):
            pages.setdefault(art, set()).add(pg)
        picked = []
        for c in seen:
            m = _ARTICLE_NUM.search(c["article"])
            if not m or m.group(1) not in cited:
                continue
            if pages.get(m.group(1)) and c["page"] not in pages[m.group(1)]:
                continue  # the reply named specific pages of this article: drop the other pages
            picked.append(c)
        return picked[:6]
    return seen


def is_follow_up(message: str) -> bool:
    m = message.strip()
    return len(m) < 25 or m.split()[0] in _FOLLOW_UP_STARTS if m else False


class KnowledgeIndex:
    """Loaded once at startup; `build()` is cheap (BM25 over 45 records)."""

    def __init__(self, kb: KnowledgeBase | None = None):
        self.kb = kb or KnowledgeBase()
        self.retriever = Retriever(self.kb)

    def search(self, query: str, k: int = 5) -> list[Hit]:
        topics = detect_topics(query, 1)
        topic_id = topics[0][0].id if topics else None
        relevant = lambda h: getattr(h, "lexical", h.score) > 0 or getattr(h, "matched", False)  # drop pure tie-breaker scores
        hits = [h for h in self.retriever.search(CLONE, query, topic_id, k=k) if relevant(h)]
        if not hits and topic_id:
            hits = [h for h in self.retriever.search(CLONE, query, None, k=k) if relevant(h)]
        return hits

    def build(self, message: str, previous_questions: list[str] | None = None, k: int = 5) -> Knowledge:
        query = message.strip()
        prev = [q for q in (previous_questions or []) if q and q.strip()]
        if prev and is_follow_up(query):
            query = " ".join(prev[-1:] + [query])  # last question + this one = "last two user messages"
        hits = self.search(query, k=k)
        if not hits:
            return Knowledge(EMPTY_BLOCK, [], query)
        return Knowledge(format_block(hits), hits, query)


def format_record(hit: Hit) -> str:
    r = hit.record
    article, page = _article_of(hit)
    quote = (r.sources[0].quote if r.sources and getattr(r.sources[0], "quote", None) else r.scholar_statement) or ""
    body = f"السؤال: {r.question}\nالحكم: {r.ruling}\nالاقتباس الحرفي: «{quote.strip()}»"
    return f'<record id="{escape(r.id)}" article="{escape(article, {chr(34): "&quot;"})}" page="{escape(page)}">\n{escape(body)}\n</record>'


def format_block(hits: list[Hit]) -> str:
    return "<knowledge>\n" + "\n".join(format_record(h) for h in hits) + "\n</knowledge>"
