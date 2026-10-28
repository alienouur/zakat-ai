import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_TMP = Path(os.environ.get("PYTEST_CHATBOT_TMP", "/tmp/zakat_chatbot_tests"))
_TMP.mkdir(parents=True, exist_ok=True)
CONFIG = _TMP / "config"
CONFIG.mkdir(exist_ok=True)
(CONFIG / "system_prompt.md").write_text("أنت مساعد صندوق الزكاة (نسخة اختبار).", encoding="utf-8")
(CONFIG / "escalation_tool.json").write_text(json.dumps({
    "name": "send_escalation_email", "description": "test tool", "strict": True,
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["request_type", "subject", "user_name", "details"],
                     "properties": {"request_type": {"type": "string", "enum": ["complaint", "inquiry", "file_follow_up"]},
                                    "subject": {"type": "string"}, "user_name": {"type": "string"},
                                    "contact_phone": {"type": "string"}, "contact_email": {"type": "string"},
                                    "city_or_office": {"type": "string"}, "file_or_receipt_number": {"type": "string"},
                                    "details": {"type": "string"}, "related_articles": {"type": "array", "items": {"type": "integer"}}}}},
    ensure_ascii=False), encoding="utf-8")

os.environ["CHATBOT_CONFIG_DIR"] = str(CONFIG)
os.environ["CHATBOT_DATA_DIR"] = str(_TMP / "data")
os.environ["EMAIL_DRY_RUN"] = "true"
os.environ["GEMINI_API_KEY"] = ""
os.environ["SMTP_HOST"] = ""
os.environ["CHAT_RATE_LIMIT_PER_MINUTE"] = "1000"


@pytest.fixture(scope="session")
def index():
    from chatbot.knowledge import KnowledgeIndex
    return KnowledgeIndex()


@pytest.fixture
def cfg(tmp_path):
    from chatbot.config import Settings
    os.environ["CHATBOT_DATA_DIR"] = str(tmp_path / "data")
    return Settings()


def valid_args(**over):
    base = {"request_type": "complaint", "subject": "تأخر صرف الإعانة", "user_name": "محمد علي", "contact_phone": "0912345678",
            "contact_email": "", "city_or_office": "مكتب طرابلس", "file_or_receipt_number": "",
            "details": "قدمت ملفي قبل ثلاثة أشهر في مكتب طرابلس ولم يصلني أي رد حتى الآن.", "related_articles": [7, 16]}
    base.update(over)
    return base
