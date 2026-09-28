"""Retrieval layer: one BM25 index per clone (datasets never mix) with subtopic/tag boosting.

If an OpenAI-compatible embedding endpoint is configured, a hybrid score
(BM25 + cosine) is used; otherwise pure lexical BM25 on normalised Arabic tokens.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from .kb import SOURCE_PRIORITY, KnowledgeBase, Record
from .normalize import tokenize
from .topics import TOPIC_BY_ID

GENERIC_TAGS = {"zakat", "khilaf", "ruling", "contemporary"}
BROAD_TAGS = {"nisab", "hawl"}


def specific_tags(topic_id: str) -> set[str]:
    """Tags that identify a topic's records; broad tags (nisab/hawl) only count for the nisab/hawl topics."""
    topic = TOPIC_BY_ID.get(topic_id)
    if topic is None:
        return set()
    tags = set(topic.tags) - GENERIC_TAGS
    if topic.id not in ("nisab", "hawl", "mustafad"):
        tags -= BROAD_TAGS
    tags.add(topic.id)
    return tags


@dataclass
class Hit:
    record: Record
    score: float
    matched_topic: bool
    lexical: float = 0.0


class Retriever:
    def __init__(self, kb: KnowledgeBase, embedder=None):
        self.kb = kb
        self.embedder = embedder
        self._index: dict[str, tuple[BM25Okapi, list[Record]]] = {}
        self._vectors: dict[str, list[list[float]]] = {}
        self.rebuild()

    def rebuild(self) -> None:
        self._index.clear()
        for clone, recs in self.kb.by_clone.items():
            corpus = [tokenize(r.search_text()) for r in recs]
            if corpus:
                self._index[clone] = (BM25Okapi(corpus), recs)
        if self.embedder is not None:
            for clone, (_, recs) in self._index.items():
                try:
                    self._vectors[clone] = self.embedder.embed([r.search_text() for r in recs])
                except Exception:
                    self._vectors.pop(clone, None)

    def search(self, clone: str, query: str, topic_id: str | None = None, k: int = 5) -> list[Hit]:
        if clone not in self._index:
            return []
        bm25, recs = self._index[clone]
        q_tokens = tokenize(query)
        scores = list(bm25.get_scores(q_tokens)) if q_tokens else [0.0] * len(recs)
        max_s = max(scores) if scores and max(scores) > 0 else 1.0

        vec_scores = None
        if clone in self._vectors and self.embedder is not None:
            try:
                qv = self.embedder.embed([query])[0]
                vec_scores = [_cos(qv, v) for v in self._vectors[clone]]
            except Exception:
                vec_scores = None

        topic = TOPIC_BY_ID.get(topic_id) if topic_id else None
        wanted_tags = specific_tags(topic_id) if topic_id else set()
        hits: list[Hit] = []
        for i, rec in enumerate(recs):
            lexical = scores[i] / max_s
            s = lexical
            if vec_scores is not None:
                s = 0.5 * s + 0.5 * max(vec_scores[i], 0.0)
            matched = False
            if topic is not None:
                if rec.subtopic == topic.id:
                    s += 1.0
                    matched = True
                elif wanted_tags & set(rec.tags):
                    s += 0.35
                    matched = True
            # authoritative sources rank slightly higher on ties (spec section 19)
            s += 0.02 * (7 - SOURCE_PRIORITY.get(rec.primary_source.source_type, 6))
            if rec.verification_status == "verified":
                s += 0.05
            if s > 0.05:
                hits.append(Hit(rec, s, matched, lexical))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
