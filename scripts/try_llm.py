"""Print the raw Gemini restatement (and the validator verdict) for a sample question.

Usage: GEMINI_API_KEY=... PYTHONPATH=. python scripts/try_llm.py [clone|comparative] [message]
"""
from __future__ import annotations

import asyncio
import json
import sys

from fastapi.testclient import TestClient

from app.llm import SYSTEM_AR, SYSTEM_COMPARE_AR, GeminiRephraser, answer_context, compare_context, validate
from app.main import app

CASH = {"ownership": "full", "hawl": "yes", "other_money": "no", "purpose": "savings", "debts": "none"}


async def main() -> None:
    clone = sys.argv[1] if len(sys.argv) > 1 else "ibn_uthaymeen"
    message = sys.argv[2] if len(sys.argv) > 2 else "عندي 10,000 دولار، كم زكاتي؟"
    client = TestClient(app)
    r = GeminiRephraser()
    if clone == "comparative":
        data = client.post("/api/compare", json={"message": message, "use_llm": False}).json()
        ctx = compare_context(message, data)
        text = await r._generate(SYSTEM_COMPARE_AR, "المادة المعطاة:\n\n" + ctx)
    else:
        data = client.post("/api/ask", json={"clone": clone, "message": message, "answers": CASH,
                                             "manual_prices": {"gold_per_gram": 80, "silver_per_gram": 1}, "use_llm": False}).json()
        ctx = answer_context(message, data)
        text = await r._generate(SYSTEM_AR, "المادة المعطاة:\n\n" + ctx)
    print("=== CONTEXT ===\n", ctx)
    print("\n=== OUTPUT ===\n", text)
    print("\n=== VALIDATOR ===", json.dumps(validate(text, ctx), ensure_ascii=False))


asyncio.run(main())
