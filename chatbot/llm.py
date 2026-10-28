"""Conversation loop with Gemini (function calling) — the transport is a small injectable callable so tests can mock it.

The history is a list of Gemini `contents` entries ({"role": "user"|"model", "parts": [...]}) and is only ever appended to.
Model turns are stored exactly as returned (including thought signatures) so later requests stay valid.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .config import Settings, settings as default_settings

log = logging.getLogger("chatbot.llm")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_TOOL_ROUNDS = 3

MSG_REFUSAL = "عذرًا، لا أستطيع المساعدة في هذا الطلب. يمكنك سؤالي عن أحكام الزكاة أو إجراءات صندوق الزكاة، أو مراسلة الصندوق مباشرة."
MSG_RATE_LIMIT = "الخدمة مزدحمة حاليًا، حاول بعد لحظات."
MSG_API_ERROR = "حدث خلل مؤقت في خدمة المساعد، حاول بعد لحظات."
MSG_CONNECTION = "تعذّر الاتصال بخدمة المساعد حاليًا، حاول بعد لحظات."
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MSG_DISABLED = "خدمة المساعد الذكي غير مفعّلة على هذا الخادم (GEMINI_API_KEY غير مضبوط). يرجى إبلاغ فريق التقنية."
MSG_EMPTY = "لم أتمكن من صياغة إجابة. هل يمكنك إعادة صياغة سؤالك؟"
MSG_TOOL_LOOP = "لم أستطع إكمال الطلب تلقائيًا. يمكنك مراسلة الصندوق مباشرة على البريد المذكور."

Transport = Callable[..., dict]  # (body, model=None) -> response json
ToolExecutor = Callable[[str, dict], dict]


@dataclass
class ChatResult:
    reply: str
    new_messages: list[dict] = field(default_factory=list)
    escalation: dict | None = None
    stop_reason: str = "STOP"
    error: str | None = None
    rounds: int = 0


DRAFT_INSTRUCTIONS = """أنت تساعد مواطنًا على كتابة طلب فتوى إلى مكتب المستشارين الشرعيين في صندوق الزكاة الليبي. ستُعطى محادثته مع المساعد الآلي (أسئلته وآخر رد). المساعد لم يجد الجواب في اللائحة، فالسؤال يُحال إلى المفتي.
أعد JSON فقط بالحقول:
- "subject": عنوان قصير بالعربية (أقل من 80 حرفًا) يلخص المسألة.
- "question": صياغة واضحة ومهذبة للسؤال بصيغة المتكلم (أنا…)، تجمع كل ما ذكره المستخدم من تفاصيل (مبالغ، أعداد، مدد، ظروف) في فقرة أو فقرتين، دون إضافة معلومات لم يذكرها ودون أي حكم فقهي.
- "missing_info": قائمة (0 إلى 4 عناصر) بمعلومات قصيرة قد يحتاجها المفتي ولم يذكرها المستخدم، كل عنصر جملة قصيرة.
- "related_articles": قائمة أرقام مواد اللائحة التي وردت في المحادثة (أعداد صحيحة) أو قائمة فارغة.
لا تكتب أي شيء خارج JSON."""


class GeminiError(Exception):
    def __init__(self, status: int | None, detail: str):
        super().__init__(detail)
        self.status = status


def http_transport(cfg: Settings) -> Transport:
    # httpx honours HTTPS_PROXY / HTTP_PROXY from the environment, which lets a server in a region
    # where the Gemini API is unavailable route only these calls through a proxy abroad.
    timeout = httpx.Timeout(cfg.llm_timeout_seconds, connect=cfg.llm_connect_timeout_seconds)

    def send(body: dict, model: str | None = None) -> dict:
        try:
            r = httpx.post(GEMINI_URL.format(model=model or cfg.gemini_model), json=body, trust_env=True,
                           headers={"x-goog-api-key": cfg.gemini_api_key}, timeout=timeout)
        except httpx.RequestError as e:
            raise GeminiError(None, type(e).__name__) from e
        if r.status_code >= 400:
            raise GeminiError(r.status_code, r.text[:300])
        return r.json()
    return send


class GeminiChat:
    def __init__(self, system_prompt: str, function_declaration: dict, cfg: Settings | None = None,
                 transport: Transport | None = None):
        self.cfg = cfg or default_settings
        self.system_prompt = system_prompt
        self.function_declaration = function_declaration
        self._uses_http = transport is None
        self.transport = transport or http_transport(self.cfg)
        self._sleep = time.sleep

    def _models(self) -> list[str]:
        fb = self.cfg.gemini_fallback_model
        return [self.cfg.gemini_model] + ([fb] if fb and fb != self.cfg.gemini_model else [])

    def _call(self, body: dict) -> dict:
        """Retries transient failures (503/429/5xx/connection) with 1s, 2s, 4s backoff, then tries the fallback model."""
        last: GeminiError | None = None
        for model in self._models():
            for attempt in range(1, max(1, self.cfg.llm_retries) + 1):
                try:
                    return self.transport(body, model=model)
                except GeminiError as e:
                    last = e
                    transient = e.status is None or e.status in RETRYABLE_STATUSES
                    if not transient:
                        raise
                    log.warning("gemini %s attempt %s/%s failed: %s", model, attempt, self.cfg.llm_retries,
                                f"status {e.status}" if e.status else f"connection ({e})")
                    if attempt < self.cfg.llm_retries:
                        self._sleep(self.cfg.llm_retry_base_seconds * (2 ** (attempt - 1)))
            if model != self._models()[-1]:
                log.warning("switching to fallback model %s", self._models()[-1])
        assert last is not None
        raise last

    def _body(self, contents: list[dict]) -> dict:
        return {
            "system_instruction": {"parts": [{"text": self.system_prompt}]},
            "contents": contents,
            "tools": [{"functionDeclarations": [self.function_declaration]}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": self.cfg.llm_max_output_tokens},
        }

    def probe(self) -> dict:
        """Tiny real call used by /api/health?llm=1 to show *why* Gemini fails (status + Google's message)."""
        if self._uses_http and not self.cfg.gemini_api_key:
            return {"ok": False, "status": None, "error": "GEMINI_API_KEY غير مضبوط"}
        body = {"contents": [{"role": "user", "parts": [{"text": "قل: جاهز"}]}],
                "generationConfig": {"maxOutputTokens": 8}}
        try:
            resp = self._call(body)
        except GeminiError as e:
            return {"ok": False, "status": e.status, "error": str(e)[:300], "model": self.cfg.gemini_model,
                    "fallback_model": self.cfg.gemini_fallback_model or None, "retries": self.cfg.llm_retries}
        text = self._text(resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])) if resp.get("candidates") else ""
        return {"ok": True, "status": 200, "model": self.cfg.gemini_model, "sample": text[:40]}

    def draft_fatwa_request(self, questions: list[str], last_reply: str) -> dict | None:
        """One tool-free JSON call that pre-writes the fatwa request from the conversation; None on any failure."""
        if self._uses_http and not self.cfg.gemini_api_key:
            return None
        convo = "\n".join(f"- سؤال المستخدم: {q}" for q in questions[-6:]) + (f"\n- آخر رد من المساعد: {last_reply[:1500]}" if last_reply else "")
        body = {
            "system_instruction": {"parts": [{"text": DRAFT_INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": [{"text": convo}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024, "responseMimeType": "application/json"},
        }
        try:
            resp = self._call(body)
            text = self._text(resp["candidates"][0]["content"]["parts"])
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        except (GeminiError, KeyError, IndexError, ValueError, TypeError) as e:
            log.warning("fatwa draft failed: %s", type(e).__name__)
            return None
        if not isinstance(data, dict) or not str(data.get("question", "")).strip():
            return None
        arts = [int(a) for a in data.get("related_articles") or [] if str(a).isdigit()]
        return {
            "subject": str(data.get("subject", "")).strip()[:90],
            "question": str(data["question"]).strip()[:3500],
            "missing_info": [str(x).strip() for x in (data.get("missing_info") or []) if str(x).strip()][:4],
            "related_articles": arts[:10],
        }

    @staticmethod
    def _text(parts: list[dict]) -> str:
        return "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought")).strip()

    def chat(self, history: list[dict], user_text: str, execute_tool: ToolExecutor) -> ChatResult:
        if self._uses_http and not self.cfg.gemini_api_key:
            return ChatResult(MSG_DISABLED, [], None, "DISABLED", "no_api_key")
        new: list[dict] = [{"role": "user", "parts": [{"text": user_text}]}]
        escalation: dict | None = None
        last_text = ""
        for round_no in range(1, MAX_TOOL_ROUNDS + 2):
            try:
                resp = self._call(self._body(history + new))
            except GeminiError as e:
                if e.status in (429, 503):
                    return ChatResult(MSG_RATE_LIMIT, new, escalation, "ERROR", f"busy:{e.status}")
                if e.status is not None:
                    log.warning("gemini status %s: %s", e.status, e)
                    return ChatResult(MSG_API_ERROR, new, escalation, "ERROR", f"status:{e.status}")
                log.warning("gemini connection error: %s", e)
                return ChatResult(MSG_CONNECTION, new, escalation, "ERROR", "connection")

            if resp.get("promptFeedback", {}).get("blockReason") or not resp.get("candidates"):
                return ChatResult(MSG_REFUSAL, new, escalation, "REFUSAL", "blocked", round_no)
            cand = resp["candidates"][0]
            finish = cand.get("finishReason", "STOP")
            content = cand.get("content") or {"role": "model", "parts": []}
            parts = content.get("parts", [])
            new.append({"role": "model", "parts": parts})  # verbatim, thinking/thought signatures included
            last_text = self._text(parts) or last_text

            if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
                return ChatResult(MSG_REFUSAL, new, escalation, "REFUSAL", finish, round_no)
            if finish == "MAX_TOKENS":
                log.warning("gemini hit MAX_TOKENS (round %s)", round_no)
                return ChatResult(last_text or MSG_EMPTY, new, escalation, "MAX_TOKENS", None, round_no)

            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls:
                return ChatResult(last_text or MSG_EMPTY, new, escalation, finish, None, round_no)
            if round_no > MAX_TOOL_ROUNDS:
                return ChatResult(last_text or MSG_TOOL_LOOP, new, escalation, "TOOL_LIMIT", "tool_rounds_exceeded", round_no)

            results = []
            for call in calls:  # every result goes back in ONE user message
                name, args = call.get("name", ""), call.get("args") or {}
                try:
                    if name != self.function_declaration["name"]:
                        out = {"status": "failed", "error": f"أداة غير معروفة: {name}", "is_error": True}
                    else:
                        out = execute_tool(name, args)
                        if out.get("status") == "sent":
                            escalation = {"reference": out.get("reference")}
                except Exception as e:  # tool crashed: tell the model, don't crash the chat
                    log.exception("tool %s failed", name)
                    out = {"status": "failed", "error": "تعذّر تنفيذ الطلب", "is_error": True, "exception": type(e).__name__}
                results.append({"functionResponse": {"name": name, "response": out}})
            new.append({"role": "user", "parts": results})
        return ChatResult(last_text or MSG_TOOL_LOOP, new, escalation, "TOOL_LIMIT", "tool_rounds_exceeded", MAX_TOOL_ROUNDS)
