"""FastAPI entrypoint: JSON API + static RTL web UI.

Run: uvicorn app.main:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .comparative import CLONE_ORDER, Comparative
from .kb import KnowledgeBase
from .llm import GeminiRephraser
from .pipeline import SKIPPED, Pipeline, normalize_currency
from .prices import market_snapshot
from .retrieval import Retriever
from .topics import GROUP_LABELS, TOPICS

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Zakat AI — متخصص فقه الزكاة", version="0.1.0")

kb = KnowledgeBase()
retriever = Retriever(kb)
pipeline = Pipeline(kb, retriever)
comparative = Comparative(kb, retriever)
rephraser = GeminiRephraser()


class AskRequest(BaseModel):
    clone: str = Field(pattern="^(albani|ibn_uthaymeen|ibn_baz|maliki)$")
    message: str = ""
    topic: str | None = None
    answers: dict = Field(default_factory=dict)
    manual_prices: dict | None = None  # {"gold_per_gram": x, "silver_per_gram": y}
    use_llm: bool = True  # Gemini restatement of the composed answer (never changes the ruling/records)


class CompareRequest(BaseModel):
    message: str
    topic: str | None = None
    use_llm: bool = True


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "errors": kb.errors, "stats": kb.stats(),
            "llm": {"enabled": rephraser.enabled, "model": rephraser.model if rephraser.enabled else None}}


@app.get("/api/clones")
def clones() -> list[dict]:
    out = []
    for cid in CLONE_ORDER:
        m = kb.meta.get(cid)
        if m is None:
            continue
        out.append({
            "id": cid, "name_ar": m.name_ar, "name_en": m.name_en, "school": m.school,
            "description_ar": m.description_ar, "disclaimer_ar": m.disclaimer_ar,
            "methodology_ar": m.methodology_ar, "source_priority_ar": m.source_priority_ar,
            "records": len(kb.by_clone.get(cid, [])),
            "calc_params": m.calc_params.model_dump(),
        })
    out.append({
        "id": "comparative", "name_ar": "المقارن في فقه الزكاة (Zakat Comparative Scholar)", "name_en": "Zakat Comparative Scholar",
        "school": "مقارنة بين المناهج الأربعة",
        "description_ar": "يعرض ما وجده في مصادر كل منهج على حدة، ونقاط الاتفاق والاختلاف، وسبب الاختلاف إن نُصّ عليه؛ ولا يختار رأيًا.",
        "disclaimer_ar": "لا يصف رأيًا بأنه الأصح؛ الترجيحات تُنسب إلى قائلها.",
        "methodology_ar": [], "source_priority_ar": [], "records": 0, "calc_params": {},
    })
    return out


@app.get("/api/topics")
def topics() -> dict:
    return {
        "groups": GROUP_LABELS,
        "topics": [{"id": t.id, "label_ar": t.label_ar, "group": t.group, "calculable": t.calculable} for t in TOPICS],
    }


@app.get("/api/record/{record_id}")
def record(record_id: str) -> dict:
    rec = kb.get(record_id)
    if rec is None:
        raise HTTPException(404, "record not found")
    return rec.model_dump()


@app.get("/api/records/{clone}")
def records(clone: str) -> list[dict]:
    if clone not in kb.by_clone:
        raise HTTPException(404, "clone not found")
    return [{"id": r.id, "subtopic": r.subtopic, "tags": r.tags, "question": r.question,
             "verification_status": r.verification_status, "url": r.primary_source.url} for r in kb.by_clone[clone]]


@app.get("/api/bibliography/{clone}")
def bibliography(clone: str) -> list[dict]:
    return [s.model_dump() for s in kb.bibliography.get(clone, [])]


@app.get("/api/market")
async def market(currency: str = "USD") -> dict:
    try:
        return await market_snapshot(currency)
    except Exception as e:  # network failure: caller must fall back to manual prices
        raise HTTPException(503, f"market data unavailable: {e}") from e


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    answers = dict(req.answers)
    if "currency" in answers and answers["currency"] != SKIPPED:
        answers["currency"] = normalize_currency(answers["currency"])
        if answers["currency"] is None:
            answers.pop("currency")
    prepared = pipeline.prepare(req.clone, req.message, req.topic, answers)
    meta = kb.meta.get(req.clone)
    if prepared.topic is None:
        prepared.stage = "clarify"
        prepared.notes.append("لم أتعرف على موضوع السؤال؛ اختر الموضوع من القائمة أو أعد صياغة السؤال بذكر نوع المال.")
        return prepared.to_dict(meta)
    if prepared.stage == "clarify":
        return prepared.to_dict(meta)

    market_data = None
    market_error = None
    if prepared.answers.get("_want_calc") and prepared.topic.calculable and prepared.topic.id != "zakat_fitr":
        currency = prepared.answers.get("currency") or "USD"
        if req.manual_prices and req.manual_prices.get("gold_per_gram") and req.manual_prices.get("silver_per_gram"):
            market_data = {
                "currency": currency,
                "gold_per_gram": float(req.manual_prices["gold_per_gram"]),
                "silver_per_gram": float(req.manual_prices["silver_per_gram"]),
                "gold": {"source": "سعر أدخله المستخدم يدويًّا", "as_of": "بحسب المستخدم", "fetched_at": ""},
                "silver": {"source": "سعر أدخله المستخدم يدويًّا", "as_of": "بحسب المستخدم", "fetched_at": ""},
                "fx": None,
            }
        else:
            try:
                market_data = await market_snapshot(currency)
            except Exception as e:
                market_error = str(e)
    result = pipeline.answer(prepared, req.message, market_data, market_error)
    data = result.to_dict(meta)
    if req.use_llm:
        data["narrative"] = (await rephraser.narrate_answer(req.message, data)).to_dict()
    return data


@app.post("/api/compare")
async def compare(req: CompareRequest) -> dict:
    data = comparative.compare(req.message, req.topic)
    if req.use_llm:
        data["narrative"] = (await rephraser.narrate_compare(req.message, data)).to_dict()
    return data


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))
