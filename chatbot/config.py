"""All runtime settings come from environment variables (a `.env` file at the repo root is loaded via python-dotenv)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip())
    llm_max_output_tokens: int = field(default_factory=lambda: _int("LLM_MAX_OUTPUT_TOKENS", 4096))
    llm_timeout_seconds: float = field(default_factory=lambda: float(os.environ.get("LLM_TIMEOUT_SECONDS", "25")))
    llm_connect_timeout_seconds: float = field(default_factory=lambda: float(os.environ.get("LLM_CONNECT_TIMEOUT_SECONDS", "10")))
    gemini_fallback_model: str = field(default_factory=lambda: os.environ.get("GEMINI_FALLBACK_MODEL", "").strip())
    llm_retries: int = field(default_factory=lambda: _int("LLM_RETRIES", 2))
    llm_retry_base_seconds: float = field(default_factory=lambda: float(os.environ.get("LLM_RETRY_BASE_SECONDS", "0.5")))

    config_dir: Path = field(default_factory=lambda: Path(os.environ.get("CHATBOT_CONFIG_DIR", str(ROOT / "config"))))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CHATBOT_DATA_DIR", str(ROOT / "data"))))

    complaints_email: str = field(default_factory=lambda: os.environ.get("COMPLAINTS_EMAIL", "complaints@zakatfund.gov.ly").strip())
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", "").strip())
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", "").strip())
    smtp_password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: os.environ.get("SMTP_FROM", "").strip())
    smtp_use_ssl: bool = field(default_factory=lambda: _flag("SMTP_USE_SSL", "false"))
    email_dry_run: bool = field(default_factory=lambda: _flag("EMAIL_DRY_RUN", "true"))

    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip())
    )
    chat_rate_limit_per_minute: int = field(default_factory=lambda: _int("CHAT_RATE_LIMIT_PER_MINUTE", 30))
    escalations_per_session: int = field(default_factory=lambda: _int("ESCALATIONS_PER_SESSION", 3))
    escalations_per_ip_per_hour: int = field(default_factory=lambda: _int("ESCALATIONS_PER_IP_PER_HOUR", 20))
    max_message_chars: int = field(default_factory=lambda: _int("MAX_MESSAGE_CHARS", 2000))
    max_session_messages: int = field(default_factory=lambda: _int("MAX_SESSION_MESSAGES", 40))
    session_retention_days: int = field(default_factory=lambda: _int("SESSION_RETENTION_DAYS", 30))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_key)


settings = Settings()
