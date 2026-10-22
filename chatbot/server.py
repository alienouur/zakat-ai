"""FastAPI app: `uvicorn chatbot.server:app`."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.store import RateLimiter

from .config import settings
from .escalation import EscalationService
from .knowledge import KnowledgeIndex, citations_for
from .llm import GeminiChat
from .prompt import load_system_prompt, load_tool_definition, to_gemini_function_declaration
from .sessions import SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chatbot.server")

STATIC_DIR = Path(__file__).parent / "static"
_NO_ANSWER = re.compile(r"لا تتناول|لا تتضمن|لم تتناول|غير وارد|لا تذكر|لم أجد|لا يوجد في اللائحة|خارج (?:نطاق )?اللائحة|لم تحدد اللائحة|لا تنص اللائحة")
MSG_SESSION_FULL = "وصلت هذه المحادثة إلى حدها الأقصى (40 رسالة). اضغط «محادثة جديدة» لمواصلة الأسئلة."

app = FastAPI(title="مساعد صندوق الزكاة", version="1.0", docs_url=None, redoc_url=None)
if settings.allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"], allow_credentials=False)

# loaded once at startup; prompt files missing -> clear error before the server accepts traffic
SYSTEM_PROMPT = load_system_prompt()
TOOL = load_tool_definition()
knowledge = KnowledgeIndex()
sessions = SessionStore(settings.data_dir, settings.session_retention_days)
escalations = EscalationService(settings)
llm = GeminiChat(SYSTEM_PROMPT, to_gemini_function_declaration(TOOL), settings)
limiter = RateLimiter(settings.chat_rate_limit_per_minute)


class ChatIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1)


class ConsultIn(BaseModel):
    """Direct (model-free) path: the citizen sends a fatwa request to the Fund's Sharia advisors office from the UI form."""
    session_id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=2, max_length=120)
    contact_phone: str = Field(default="", max_length=40)
    contact_email: str = Field(default="", max_length=120)
    city_or_office: str = Field(default="", max_length=120)
    question: str = Field(min_length=10, max_length=4000)
    subject: str = Field(default="", max_length=90)
    related_articles: list[int] = Field(default_factory=list)


class DraftIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    question: str = Field(default="", max_length=2000)


def _last_model_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "model":
            t = "".join(p.get("text", "") for p in m.get("parts", []) if "text" in p and not p.get("thought")).strip()
            if t:
                return t
    return ""


def _no_answer(reply: str, knowledge_block: str) -> bool:
    # only when the reply itself says the regulation does not cover it (an empty retrieval alone also
    # happens for off-topic or junk input, which must not be offered as a fatwa request)
    return bool(_NO_ANSWER.search(reply))


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "unknown")


@app.post("/api/chat")
def chat(body: ChatIn, request: Request) -> dict:
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "الرسالة فارغة")
    if len(text) > settings.max_message_chars:
        raise HTTPException(413, f"الرسالة طويلة جدًا (الحد {settings.max_message_chars} حرفًا)")
    ip = _ip(request)
    if not limiter.allow(ip):
        raise HTTPException(429, "عدد الطلبات كبير؛ حاول بعد دقيقة")
    session = sessions.get_or_create(body.session_id)
    sid = session["session_id"]
    if len(session["messages"]) >= settings.max_session_messages:
        return {"session_id": sid, "reply": MSG_SESSION_FULL, "citations": [], "escalation": None, "limit_reached": True}

    kn = knowledge.build(text, session["questions"])
    result = llm.chat(session["messages"], f"{kn.block}\n\nسؤال المستخدم: {text}",
                      lambda name, args: escalations.send(args, session_id=sid, client_ip=ip))
    sessions.append(sid, result.new_messages, question=text)
    if result.error:
        log.info("chat %s ended with %s (%s)", sid[:8], result.stop_reason, result.error)
    return {
        "session_id": sid,
        "reply": result.reply,
        "citations": citations_for(kn.hits, result.reply) if result.stop_reason not in ("ERROR", "DISABLED", "REFUSAL") else [],
        "escalation": result.escalation,
        "limit_reached": len(session["messages"]) + len(result.new_messages) >= settings.max_session_messages,
        "no_answer": result.stop_reason == "STOP" and _no_answer(result.reply, kn.block),
    }


@app.post("/api/consult/draft")
def consult_draft(body: DraftIn, request: Request) -> dict:
    """Pre-writes the fatwa request from the conversation so the citizen only adds name + contact."""
    ip = _ip(request)
    if not limiter.allow(ip):
        raise HTTPException(429, "عدد الطلبات كبير؛ حاول بعد دقيقة")
    session = sessions.get(body.session_id) if body.session_id else None
    questions = list(session["questions"]) if session else []
    if body.question.strip() and body.question.strip() not in questions:
        questions.append(body.question.strip())
    fallback = {"subject": (questions[-1][:80] if questions else ""), "question": (questions[-1] if questions else ""),
                "missing_info": [], "related_articles": [], "drafted": False}
    if not questions:
        return fallback
    draft = llm.draft_fatwa_request(questions, _last_model_text(session["messages"]) if session else "")
    return {**draft, "drafted": True} if draft else fallback


@app.post("/api/consult")
def consult(body: ConsultIn, request: Request) -> dict:
    ip = _ip(request)
    if not limiter.allow(ip):
        raise HTTPException(429, "عدد الطلبات كبير؛ حاول بعد دقيقة")
    sid = body.session_id or "direct"
    session = sessions.get(body.session_id) if body.session_id else None
    context = ""
    if session and session["questions"]:
        context = "\n\nآخر أسئلة المستخدم في المحادثة:\n- " + "\n- ".join(session["questions"][-3:])
    out = escalations.send({
        "request_type": "consultation",
        "subject": (body.subject.strip() or body.question.strip().splitlines()[0])[:90],
        "user_name": body.name,
        "contact_phone": body.contact_phone,
        "contact_email": body.contact_email,
        "city_or_office": body.city_or_office,
        "file_or_receipt_number": "",
        "details": f"طلب فتوى من واجهة المساعد (لم يجد المساعد الجواب في اللائحة):\n{body.question.strip()}{context}",
        "related_articles": [a for a in body.related_articles if 0 < a < 100][:10],
    }, session_id=sid, client_ip=ip)
    if out.get("status") != "sent":
        raise HTTPException(400, out.get("error", "تعذّر إرسال طلب الفتوى"))
    return {"reference": out["reference"], "recipient_role": "مكتب المستشارين الشرعيين بصندوق الزكاة"}


@app.post("/api/session/new")
def new_session() -> dict:
    return {"session_id": sessions.create()}


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_enabled": settings.llm_enabled,
        "model": settings.gemini_model,
        "email_dry_run": settings.email_dry_run,
        "records": knowledge.kb.stats().get("libya_zakat_fund", {}).get("records", 0),
        "sessions": sessions.count(),
    }


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"), headers={"Cache-Control": "no-cache"})
