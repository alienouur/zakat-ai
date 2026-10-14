"""FastAPI entrypoint: JSON API + static RTL web UI.

Run: uvicorn app.main:app --reload
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .comparative import CLONE_ORDER, Comparative
from .kb import KnowledgeBase
from .llm import GeminiRephraser
from .pipeline import SKIPPED, Pipeline, normalize_currency
from .prices import market_snapshot
from .retrieval import Retriever
from .scope import ScopeDecision, ScopeGuard
from .store import RateLimiter, Store, User
from .topics import GROUP_LABELS, TOPICS

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Zakat AI — متخصص فقه الزكاة", version="0.1.0")

kb = KnowledgeBase()
retriever = Retriever(kb)
pipeline = Pipeline(kb, retriever)
comparative = Comparative(kb, retriever)
rephraser = GeminiRephraser()
scope_guard = ScopeGuard(rephraser)
store = Store()

# Deployment settings (environment): REQUIRE_LOGIN=1 makes /api/ask and /api/compare need an account;
# INVITE_CODE restricts self-registration to people who know the code (empty = open registration);
# RATE_LIMIT_PER_MINUTE caps questions per user (or per IP for guests); COOKIE_SECURE=0 for plain-http dev.
def _env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


REQUIRE_LOGIN = _env_flag("REQUIRE_LOGIN", "1")
INVITE_CODE = os.environ.get("INVITE_CODE", "").strip()
COOKIE_NAME = "zakat_session"
COOKIE_SECURE = _env_flag("COOKIE_SECURE", "1")
limiter = RateLimiter(int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30")))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def current_user(request: Request) -> User | None:
    return store.user_for_session(request.cookies.get(COOKIE_NAME))


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "يلزم تسجيل الدخول")
    return user


def require_admin(request: Request) -> User:
    user = require_user(request)
    if not user.is_admin:
        raise HTTPException(403, "هذه الصفحة للمشرف فقط")
    return user


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=60 * 60 * 24 * 30, path="/")


def _gate(request: Request, user: User | None) -> None:
    """Login requirement + per-user/IP rate limit for the expensive endpoints."""
    if REQUIRE_LOGIN and user is None:
        raise HTTPException(401, "يلزم تسجيل الدخول لاستخدام النظام")
    key = f"u:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    if not limiter.allow(key):
        raise HTTPException(429, "عدد الطلبات كبير؛ انتظر دقيقة ثم أعد المحاولة")


def _save_turn(user: User | None, conversation_id: int | None, clone: str, message: str, response: dict) -> int | None:
    """Append the question and the full answer payload to the user's history (guests are never stored)."""
    if user is None:
        return None
    if conversation_id is not None and store.conversation_owner(conversation_id) != user.id:
        raise HTTPException(404, "المحادثة غير موجودة")
    if conversation_id is None:
        conversation_id = store.create_conversation(user.id, clone, message)
    if message:
        store.add_message(conversation_id, "user", {"text": message})
    store.add_message(conversation_id, "assistant", response)
    return conversation_id


class AskRequest(BaseModel):
    clone: str = Field(pattern="^(albani|ibn_uthaymeen|ibn_baz|maliki)$")
    message: str = ""
    topic: str | None = None
    answers: dict = Field(default_factory=dict)
    manual_prices: dict | None = None  # {"gold_per_gram": x, "silver_per_gram": y}
    use_llm: bool = True  # Gemini restatement of the composed answer (never changes the ruling/records)
    conversation_id: int | None = None  # continue an existing history thread (logged-in users)


class CompareRequest(BaseModel):
    message: str
    topic: str | None = None
    use_llm: bool = True
    conversation_id: int | None = None


class RegisterRequest(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)
    office: str | None = Field(default=None, max_length=120)
    invite_code: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class RoleRequest(BaseModel):
    role: str = Field(pattern="^(admin|user)$")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "errors": kb.errors, "stats": kb.stats(),
            "llm": {"enabled": rephraser.enabled, "model": rephraser.model if rephraser.enabled else None},
            "auth": {"require_login": REQUIRE_LOGIN, "invite_only": bool(INVITE_CODE)}}


# ----------------------------------------------------------------------------- accounts & sessions
@app.get("/api/auth/config")
def auth_config() -> dict:
    return {"require_login": REQUIRE_LOGIN, "invite_only": bool(INVITE_CODE), "registration_open": True}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    user = current_user(request)
    return {"user": user.to_dict() if user else None, "require_login": REQUIRE_LOGIN}


@app.post("/api/auth/register")
def auth_register(req: RegisterRequest, response: Response) -> dict:
    email = req.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "البريد الإلكتروني غير صالح")
    if INVITE_CODE and (req.invite_code or "").strip() != INVITE_CODE:
        raise HTTPException(403, "رمز الدعوة غير صحيح؛ اطلبه من مشرف النظام")
    if store.get_user_by_email(email):
        raise HTTPException(409, "هذا البريد مسجَّل من قبل؛ سجّل الدخول")
    user = store.create_user(email, req.name, req.password, req.office)
    _set_session_cookie(response, store.create_session(user.id))
    return {"user": user.to_dict()}


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, response: Response) -> dict:
    if not limiter.allow(f"login:{request.client.host if request.client else 'unknown'}"):
        raise HTTPException(429, "محاولات كثيرة؛ انتظر دقيقة")
    user = store.verify_password(req.email, req.password)
    if user is None:
        raise HTTPException(401, "البريد أو كلمة المرور غير صحيحة")
    _set_session_cookie(response, store.create_session(user.id))
    return {"user": user.to_dict()}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    store.delete_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.post("/api/auth/password")
def auth_change_password(req: PasswordChangeRequest, request: Request, response: Response, user: Annotated[User, Depends(require_user)]) -> dict:
    if store.verify_password(user.email, req.current_password) is None:
        raise HTTPException(401, "كلمة المرور الحالية غير صحيحة")
    store.change_password(user.id, req.new_password)
    _set_session_cookie(response, store.create_session(user.id))
    return {"ok": True}


# ----------------------------------------------------------------------------- history
@app.get("/api/history")
def history_list(user: Annotated[User, Depends(require_user)]) -> list[dict]:
    return store.list_conversations(user.id)


@app.get("/api/history/search")
def history_search(q: str, user: Annotated[User, Depends(require_user)]) -> list[dict]:
    if len(q.strip()) < 2:
        return []
    return store.search_messages(user.id, q)


@app.get("/api/history/{conversation_id}")
def history_get(conversation_id: int, user: Annotated[User, Depends(require_user)]) -> dict:
    conv = store.get_conversation(conversation_id, user.id)
    if conv is None:
        raise HTTPException(404, "المحادثة غير موجودة")
    return conv


@app.patch("/api/history/{conversation_id}")
def history_rename(conversation_id: int, req: RenameRequest, user: Annotated[User, Depends(require_user)]) -> dict:
    if not store.rename_conversation(conversation_id, user.id, req.title):
        raise HTTPException(404, "المحادثة غير موجودة")
    return {"ok": True}


@app.delete("/api/history/{conversation_id}")
def history_delete(conversation_id: int, user: Annotated[User, Depends(require_user)]) -> dict:
    if not store.delete_conversation(conversation_id, user.id):
        raise HTTPException(404, "المحادثة غير موجودة")
    return {"ok": True}


# ----------------------------------------------------------------------------- admin
@app.get("/api/admin/users")
def admin_users(_: Annotated[User, Depends(require_admin)]) -> list[dict]:
    return store.list_users()


@app.get("/api/admin/stats")
def admin_stats(_: Annotated[User, Depends(require_admin)]) -> dict:
    return {**store.stats(), "kb": kb.stats(), "llm_enabled": rephraser.enabled}


@app.patch("/api/admin/users/{user_id}")
def admin_set_role(user_id: int, req: RoleRequest, admin: Annotated[User, Depends(require_admin)]) -> dict:
    if user_id == admin.id:
        raise HTTPException(400, "لا يمكنك تغيير صلاحيتك بنفسك")
    if store.get_user(user_id) is None:
        raise HTTPException(404, "المستخدم غير موجود")
    store.set_role(user_id, req.role)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: Annotated[User, Depends(require_admin)]) -> dict:
    if user_id == admin.id:
        raise HTTPException(400, "لا يمكنك حذف حسابك بنفسك")
    store.delete_user(user_id)
    return {"ok": True}


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


def _scope_response(decision: ScopeDecision, clone_id: str, name_ar: str, disclaimer_ar: str = "") -> dict:
    """Uniform payload for blocked requests: nothing is retrieved, priced, or narrated."""
    return {
        "stage": "out_of_scope",
        "clone": {"id": clone_id, "name_ar": name_ar, "disclaimer_ar": disclaimer_ar},
        "topic": None,
        "scope": decision.payload(),
        "answers": {}, "questions": [], "sections": [], "citations": [], "calculation": None,
        "not_found": False, "notes": [], "records": [],
    }


@app.post("/api/ask")
async def ask(req: AskRequest, request: Request) -> dict:
    user = current_user(request)
    _gate(request, user)
    answers = dict(req.answers)
    if "currency" in answers and answers["currency"] != SKIPPED:
        answers["currency"] = normalize_currency(answers["currency"])
        if answers["currency"] is None:
            answers.pop("currency")
    meta = kb.meta.get(req.clone)
    decision = await scope_guard.classify(req.message, topic_hint=bool(req.topic), continuation=bool(answers))
    # follow-ups (clarification answers) continue the same thread; only the first message is stored as the user's question
    first_turn = not answers
    user_text = req.message if first_turn else ""

    def finish(payload: dict) -> dict:
        payload["conversation_id"] = _save_turn(user, req.conversation_id, req.clone, user_text, payload)
        return payload

    if decision.blocked:
        return finish(_scope_response(decision, req.clone, meta.name_ar if meta else req.clone, meta.disclaimer_ar if meta else ""))
    prepared = pipeline.prepare(req.clone, req.message, req.topic, answers)
    if prepared.topic is None:
        prepared.stage = "clarify"
        prepared.notes.append("سؤالك عن الزكاة لكن لم أتعرف على نوع المال أو المسألة؛ اختر الموضوع من القائمة أو أعد صياغة السؤال بذكر نوع المال.")
        return finish(prepared.to_dict(meta))
    if prepared.stage == "clarify":
        return finish(prepared.to_dict(meta))

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
    return finish(data)


@app.post("/api/compare")
async def compare(req: CompareRequest, request: Request) -> dict:
    user = current_user(request)
    _gate(request, user)
    decision = await scope_guard.classify(req.message, topic_hint=bool(req.topic))
    if decision.blocked:
        data = _scope_response(decision, "comparative", "المقارن في فقه الزكاة")
    else:
        data = comparative.compare(req.message, req.topic)
        if req.use_llm:
            data["narrative"] = (await rephraser.narrate_compare(req.message, data)).to_dict()
    data["conversation_id"] = _save_turn(user, req.conversation_id, "comparative", req.message, data)
    return data


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0f6b5c"/>'
               '<text x="32" y="46" font-size="40" font-family="serif" font-weight="700" fill="#fff" text-anchor="middle">ز</text></svg>')
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
