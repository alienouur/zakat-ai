"""The `send_escalation_email` tool: validates the model's input, enforces limits, emails the Fund (or writes an .eml in dry-run)."""
from __future__ import annotations

import logging
import re
import secrets
import smtplib
import sqlite3
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .config import Settings, settings as default_settings

log = logging.getLogger("chatbot.escalation")

REQUEST_TYPES = {"complaint": "شكوى", "inquiry": "استفسار", "file_follow_up": "متابعة ملف", "consultation": "طلب فتوى"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class EscalationInput(BaseModel):
    request_type: str = Field(pattern="^(complaint|inquiry|file_follow_up|consultation)$")
    subject: str = Field(min_length=3, max_length=150)
    user_name: str = Field(min_length=2, max_length=120)
    contact_phone: str = ""
    contact_email: str = ""
    city_or_office: str = ""
    file_or_receipt_number: str = ""
    details: str = Field(min_length=10, max_length=4000)
    related_articles: list[int] = Field(default_factory=list)

    @field_validator("contact_phone", "contact_email", "city_or_office", "file_or_receipt_number", "subject", "user_name", "details", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else ("" if v is None else v)

    @field_validator("contact_email")
    @classmethod
    def _email_format(cls, v: str) -> str:
        if v and not _EMAIL_RE.match(v):
            raise ValueError("صيغة البريد الإلكتروني غير صحيحة")
        return v

    @field_validator("contact_phone")
    @classmethod
    def _phone_format(cls, v: str) -> str:
        if v and len(re.sub(r"\D", "", v)) < 7:
            raise ValueError("رقم الهاتف غير مكتمل")
        return v

    @model_validator(mode="after")
    def _contact_required(self):
        if not self.contact_phone and not self.contact_email:
            raise ValueError("يلزم رقم هاتف أو بريد إلكتروني للرد على المستخدم")
        return self


def _arabic_errors(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "المدخلات"
        msg = err["msg"].replace("Value error, ", "")
        if err["type"] in ("string_too_short", "string_too_long"):
            msg = {"details": "التفاصيل يجب أن تكون بين 10 و4000 حرف", "subject": "العنوان قصير جدًا أو طويل جدًا",
                   "user_name": "الاسم ناقص"}.get(loc, msg)
        msgs.append(f"{loc}: {msg}")
    return "بيانات ناقصة أو غير صحيحة — " + "؛ ".join(msgs)


class EscalationService:
    def __init__(self, cfg: Settings | None = None, now=None):
        self.cfg = cfg or default_settings
        self._now = now or time.time
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.cfg.data_dir / "escalations.sqlite", check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS escalations (reference TEXT PRIMARY KEY, request_type TEXT NOT NULL, "
            "created_at TEXT NOT NULL, status TEXT NOT NULL, office TEXT, session_id TEXT, client_ip TEXT, error TEXT)"
        )
        self._conn.commit()

    # ---- limits -------------------------------------------------------------------------------------
    def _count(self, where: str, params: tuple) -> int:
        return self._conn.execute(f"SELECT COUNT(*) FROM escalations WHERE status='sent' AND {where}", params).fetchone()[0]

    def _check_limits(self, session_id: str, client_ip: str) -> str | None:
        if self._count("session_id=?", (session_id,)) >= self.cfg.escalations_per_session:
            return "تم بلوغ الحد الأقصى لعدد الرسائل في هذه المحادثة (3). يمكن مراسلة الصندوق مباشرة على " + self.cfg.complaints_email
        hour_ago = datetime.fromtimestamp(self._now() - 3600, timezone.utc).isoformat(timespec="seconds")
        if self._count("client_ip=? AND created_at>=?", (client_ip, hour_ago)) >= self.cfg.escalations_per_ip_per_hour:
            return "تم بلوغ الحد الأقصى لعدد الرسائل خلال هذه الساعة. حاول لاحقًا أو راسل الصندوق مباشرة على " + self.cfg.complaints_email
        return None

    # ---- reference ----------------------------------------------------------------------------------
    def _new_reference(self) -> str:
        day = datetime.fromtimestamp(self._now(), timezone.utc).strftime("%Y%m%d")
        while True:
            ref = f"ZKT-{day}-{''.join(secrets.choice(_REF_ALPHABET) for _ in range(4))}"
            if not self._conn.execute("SELECT 1 FROM escalations WHERE reference=?", (ref,)).fetchone():
                return ref

    # ---- email --------------------------------------------------------------------------------------
    def build_email(self, data: EscalationInput, reference: str, session_id: str) -> EmailMessage:
        kind = REQUEST_TYPES[data.request_type]
        now = datetime.fromtimestamp(self._now(), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = EmailMessage()
        msg["Subject"] = f"[{kind}] {data.subject} – {reference}"
        msg["From"] = self.cfg.smtp_from or self.cfg.smtp_user or "zakat-assistant@localhost"
        # one fixed recipient for everything (complaints desk forwards fatwa requests to the Sharia advisors); never from the model
        msg["To"] = self.cfg.complaints_email
        if data.contact_email:
            msg["Reply-To"] = data.contact_email
        arts = "، ".join(f"المادة {a}" for a in data.related_articles) or "—"
        body = "\n".join([
            f"طلب جديد من مساعد صندوق الزكاة — {kind}" + ("\nموجّه إلى: مكتب المستشارين الشرعيين — سؤال لم يجد له المساعد جوابًا في اللائحة ويحتاج فتوى" if data.request_type == "consultation" else ""),
            f"الموضوع: {data.subject}",
            f"الرقم المرجعي: {reference}",
            f"وقت الطلب: {now}",
            f"رقم الجلسة: {session_id}",
            "",
            f"الاسم: {data.user_name}",
            f"الهاتف: {data.contact_phone or '—'}",
            f"البريد الإلكتروني: {data.contact_email or '—'}",
            f"المدينة / المكتب: {data.city_or_office or '—'}",
            f"رقم الملف أو الإيصال: {data.file_or_receipt_number or '—'}",
            f"مواد اللائحة ذات الصلة: {arts}",
            "",
            "التفاصيل:",
            data.details,
            "",
            "— أُرسلت هذه الرسالة آليًا بناءً على تأكيد المستخدم داخل المساعد؛ لم يُرفق نص المحادثة.",
        ])
        msg.set_content(body, charset="utf-8")
        return msg

    def _deliver(self, msg: EmailMessage, reference: str) -> None:
        if self.cfg.email_dry_run:
            outbox = self.cfg.data_dir / "outbox"
            outbox.mkdir(parents=True, exist_ok=True)
            (outbox / f"{reference}.eml").write_bytes(bytes(msg))
            return
        if not self.cfg.smtp_host:
            raise RuntimeError("SMTP_HOST غير مضبوط")
        if self.cfg.smtp_use_ssl or self.cfg.smtp_port == 465:
            server = smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=20)
        with server:
            if not (self.cfg.smtp_use_ssl or self.cfg.smtp_port == 465):
                server.ehlo()
                server.starttls()
                server.ehlo()
            if self.cfg.smtp_user:
                server.login(self.cfg.smtp_user, self.cfg.smtp_password)
            server.send_message(msg)

    # ---- the tool -----------------------------------------------------------------------------------
    def send(self, args: dict, *, session_id: str, client_ip: str) -> dict:
        """Tool entry point. Returns {"status": "sent", "reference"} or {"status": "failed", "error"} (Arabic)."""
        try:
            data = EscalationInput.model_validate(args or {})
        except ValidationError as e:
            return {"status": "failed", "error": _arabic_errors(e)}
        with self._lock:
            limit = self._check_limits(session_id, client_ip)
            if limit:
                return {"status": "failed", "error": limit}
            reference = self._new_reference()
            created = datetime.fromtimestamp(self._now(), timezone.utc).isoformat(timespec="seconds")
            self._conn.execute(
                "INSERT INTO escalations(reference, request_type, created_at, status, office, session_id, client_ip) VALUES (?,?,?,?,?,?,?)",
                (reference, data.request_type, created, "pending", data.city_or_office, session_id, client_ip),
            )
            self._conn.commit()
        try:
            self._deliver(self.build_email(data, reference, session_id), reference)
        except Exception as e:  # smtplib/socket/config errors: report to model, keep PII out of logs
            log.warning("escalation %s failed: %s", reference, type(e).__name__)
            with self._lock:
                self._conn.execute("UPDATE escalations SET status='failed', error=? WHERE reference=?", (type(e).__name__, reference))
                self._conn.commit()
            return {"status": "failed", "error": "تعذّر إرسال البريد إلى الصندوق حاليًا"}
        with self._lock:
            self._conn.execute("UPDATE escalations SET status='sent' WHERE reference=?", (reference,))
            self._conn.commit()
        log.info("escalation %s sent (%s)", reference, data.request_type)
        return {"status": "sent", "reference": reference}

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT reference, request_type, created_at, status, office FROM escalations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(zip(("reference", "request_type", "created_at", "status", "office"), r)) for r in rows]
