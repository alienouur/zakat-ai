"""Quick end-to-end smoke run of the /api/ask and /api/compare endpoints (no network needed)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PRICES = {"gold_per_gram": 85, "silver_per_gram": 1.0}
CASH_ANSWERS = {"ownership": "full", "hawl": "yes", "other_money": "no", "purpose": "savings", "debts": "none"}


def show(d: dict) -> None:
    print("stage:", d.get("stage"), "| topic:", d.get("topic") and d["topic"]["id"])
    for q in d.get("questions", []):
        print("   Q:", q["id"])
    for s in d.get("sections", []):
        print("   §", s["title"], "->", s["text"][:90].replace("\n", " "))
    for n in d.get("notes", []):
        print("   note:", n[:120])
    print("   citations:", [(c["record_id"], c["verification_status"][0]) for c in d.get("citations", [])])


def main() -> None:
    c = TestClient(app)
    cases = [
        ("ibn_uthaymeen", "عندي 10,000 دولار، كم زكاتي؟", "cash", CASH_ANSWERS),
        ("albani", "عندي 10,000 دولار، كم زكاتي؟", "cash", CASH_ANSWERS),
        ("ibn_baz", "عندي 10,000 دولار، كم زكاتي؟", "cash", CASH_ANSWERS),
        ("maliki", "عندي 10,000 دولار، كم زكاتي؟", "cash", {**CASH_ANSWERS, "debts": "due_now", "debt_amount": 4000}),
        ("ibn_baz", "هل يجوز إعطاء الزكاة للأقارب؟", None, {"recipient_kind": "relative"}),
        ("maliki", "ما زكاة الزروع وكم النصاب", None, {}),
        ("ibn_uthaymeen", "راتبي 8000 ريال شهريا كيف ازكيه", None, {}),
        ("albani", "هل تجب الزكاة في العملات الرقمية مثل البيتكوين؟", None, {"ownership": "full", "hawl": "yes", "debts": "none"}),
        ("ibn_uthaymeen", "عندي أسهم اشتريتها للاستثمار طويل الأجل هل عليها زكاة", None, {"ownership": "full", "hawl": "yes", "debts": "none", "stock_intent": "invest"}),
        ("maliki", "هل في حلي المرأة الملبوس زكاة؟", None, {"ownership": "full", "hawl": "yes", "debts": "none"}),
        ("albani", "هل يجوز إخراج زكاة الفطر نقدا؟", None, {"fitr_payer": "self"}),
    ]
    for clone, msg, topic, answers in cases:
        print(f"===== {clone}: {msg}")
        body = {"clone": clone, "message": msg, "answers": answers, "manual_prices": PRICES}
        if topic:
            body["topic"] = topic
        r = c.post("/api/ask", json=body)
        r.raise_for_status()
        show(r.json())
    print("===== compare: jewelry")
    r = c.post("/api/compare", json={"message": "هل في حلي المرأة الملبوس زكاة؟"})
    r.raise_for_status()
    d = r.json()
    for f in d["findings"]:
        print("  ", f["clone"], "->", (f.get("ruling") or "")[:80].replace("\n", " "), "|", f.get("record_id"))
    for line in d.get("agreement_points", []) + d.get("difference_points", []) + d.get("reasons", []):
        print("  *", line[:120])


if __name__ == "__main__":
    main()
