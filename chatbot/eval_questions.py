"""Prints the assistant's reply + citations for 15 realistic questions, for human review.

Runs only with GEMINI_API_KEY set and explicit consent (`--yes`) because it spends API quota.
    python -m chatbot.eval_questions --yes
"""
from __future__ import annotations

import sys

QUESTIONS = [
    "عندي 50 ألف دينار في البنك من سنة، كم زكاتها؟",
    "كم نصاب الذهب والفضة حسب اللائحة؟",
    "عندي 60 شاة، هل فيها زكاة وكم؟",
    "أنا موظف راتبي 900 دينار وعندي 4 أطفال، هل أستحق إعانة من الصندوق؟",
    "ما المستندات المطلوبة لطلب إعانة فقير؟",
    "كم الحد الأقصى لإعانة العلاج؟",
    "أريد أن أتزوج وليس عندي مال، هل يساعد الصندوق؟",
    "علي دين 25 ألف دينار لا أستطيع سداده، هل يسدده الصندوق؟",
    "ما مقدار زكاة الفطر ومتى تخرج؟",
    "هل تجوز زكاة الحلي؟",
    "كفارة اليمين كيف أدفعها عن طريق الصندوق؟",
    "قدمت ملفي في مكتب بنغازي قبل 4 أشهر ولم يردوا علي، ماذا أفعل؟",
    "موظف في المكتب طلب مني مبلغًا مقابل تسريع ملفي",
    "هل يمكنني دفع زكاتي لأخي الفقير مباشرة أم للصندوق؟",
    "ما هو الطقس اليوم في طرابلس؟",
]


def main() -> int:
    from .config import settings
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY غير مضبوط؛ التقييم يحتاج مفتاحًا حقيقيًا.")
        return 1
    if "--yes" not in sys.argv:
        print("هذا السكربت يستهلك رصيد Gemini. أعد تشغيله مع --yes للموافقة.")
        return 1
    from .escalation import EscalationService
    from .knowledge import KnowledgeIndex, citations_for
    from .llm import GeminiChat
    from .prompt import load_system_prompt, load_tool_definition, to_gemini_function_declaration

    kn = KnowledgeIndex()
    llm = GeminiChat(load_system_prompt(), to_gemini_function_declaration(load_tool_definition()), settings)
    esc = EscalationService(settings)
    for i, q in enumerate(QUESTIONS, 1):
        k = kn.build(q)
        res = llm.chat([], f"{k.block}\n\nسؤال المستخدم: {q}", lambda n, a: esc.send(a, session_id=f"eval-{i}", client_ip="eval"))
        print(f"\n{'=' * 80}\n[{i}] {q}\n{'-' * 80}\n{res.reply}\n")
        print("الإحالات:", "؛ ".join(f"{c['article']} (ص {c['page']})" for c in citations_for(k.hits, res.reply)) or "—")
        print("الحالة:", res.stop_reason, res.error or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
