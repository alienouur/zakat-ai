"""Loads the system prompt and the escalation tool definition from the (git-ignored) `config/` folder."""
from __future__ import annotations

import json
from pathlib import Path

from .config import settings

SYSTEM_PROMPT_FILE = "system_prompt.md"
TOOL_FILE = "escalation_tool.json"


class PromptFilesMissing(RuntimeError):
    pass


def _missing(path: Path, what: str) -> PromptFilesMissing:
    return PromptFilesMissing(
        f"ملف {what} غير موجود: {path}\n"
        f"ضع تعليمات النظام في {settings.config_dir / SYSTEM_PROMPT_FILE} "
        f"وتعريف أداة البريد في {settings.config_dir / TOOL_FILE} (المجلد config/ غير مرفوع إلى GitHub)، "
        "أو غيّر المسار عبر المتغير CHATBOT_CONFIG_DIR."
    )


def load_system_prompt(config_dir: Path | None = None) -> str:
    path = (config_dir or settings.config_dir) / SYSTEM_PROMPT_FILE
    if not path.is_file():
        raise _missing(path, "تعليمات النظام")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise _missing(path, "تعليمات النظام (الملف فارغ)")
    return text


def load_tool_definition(config_dir: Path | None = None) -> dict:
    """Returns the tool as written (Anthropic-style: name/description/input_schema)."""
    path = (config_dir or settings.config_dir) / TOOL_FILE
    if not path.is_file():
        raise _missing(path, "تعريف الأداة")
    try:
        tool = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PromptFilesMissing(f"تعريف الأداة في {path} ليس JSON صالحًا: {e}") from e
    for key in ("name", "description", "input_schema"):
        if key not in tool:
            raise PromptFilesMissing(f"تعريف الأداة في {path} ينقصه الحقل '{key}'")
    return tool


_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "strict", "$schema", "default"}


def _clean_schema(schema):
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


def to_gemini_function_declaration(tool: dict) -> dict:
    """Gemini `functionDeclarations` entry: same schema minus keys the API rejects."""
    return {
        "name": tool["name"],
        "description": tool["description"],
        "parameters": _clean_schema(tool["input_schema"]),
    }
