"""Optional Gemini layer: restates the already-composed, source-grounded answer in fluent Arabic.

The model never decides the ruling, picks records, or computes anything: it receives the
deterministic sections/citations/calculation produced by the pipeline and may only rephrase
them. Its output is validated (no new numbers, URLs, preference language, or impersonation);
on any violation, network failure, or missing key the template answer is served unchanged.

Env: GEMINI_API_KEY (required to enable), GEMINI_MODEL (default gemini-3.8-flash).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from .normalize import normalize

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash"
TIMEOUT_S = 25.0
MAX_OUTPUT_CHARS = 4000

SYSTEM_AR = """أنت طبقة صياغة لغوية فقط في نظام فقه زكاة قائم على مصادر متحقق منها. ستُعطى جوابًا مركّبًا مسبقًا من سجلات موثقة (أقسام: الحكم كما ورد في المصدر، الدليل، قول العالم/نص المتن، التطبيق على حالة المستخدم، الحساب، الخلاصة) مع سؤال المستخدم. مهمتك: إعادة عرض هذا المضمون نفسه بعربية واضحة ومترابطة يفهمها المستخدم بسهولة.

قواعد ملزمة لا تُخالف:
1. لا تُضف أي حكم أو دليل أو حديث أو آية أو قول عالم أو مصدر أو رقم أو نسبة أو تاريخ غير موجود نصًّا في المادة المعطاة. إن لم تجد شيئًا في المادة فلا تذكره.
2. لا تختلق أرقام صفحات أو أجزاء أو روابط أو أرقام فتاوى؛ الإحالة تكون بعبارة «انظر المصادر المرفقة أدناه».
3. عند نقل قول العالم أو نص المتن انقله حرفيًّا بين علامتي تنصيص «» كما ورد، أو لخّصه مع التصريح بأنه تلخيص.
4. ميّز بوضوح بين: ما ورد في المصدر، وتحليل النظام لحالة المستخدم، والعملية الحسابية. الحساب المذكور تقدير رياضي وليس حكمًا على المستخدم ما لم تتحقق الشروط.
5. لا تفاضل بين المناهج ولا تقل «الرأي الصحيح» أو «الأصح» أو «الراجح» من عندك؛ إن كان في المادة ترجيح صريح لعالم فانسبه إليه بلفظه.
6. لا تتكلم بلسان العالم ولا تقل «أنا الشيخ»؛ الصيغة: «في مصادر الشيخ …» أو «المشهور في المذهب المالكي …».
7. إن كانت المادة تقول إنه لم يوجد نص كافٍ فانقل ذلك كما هو دون تعويضه بمعلومات من عندك.
8. حافظ على تحذيرات وتنبيهات المادة (الأسئلة التي لم يُجب عنها، درجة التحقق، الإحالة إلى جهة إفتاء).
9. اكتب نصًّا عاديًّا بلا عناوين Markdown ولا جداول، في حدود 250 كلمة، بأسلوب علمي هادئ محترم.
"""

SYSTEM_COMPARE_AR = SYSTEM_AR + """
هذه مادة مقارنة بين أربعة مناهج (الألباني، ابن عثيمين، ابن باز، المذهب المالكي). اعرض ما وجده كل منهج في مصادره على حدة بنفس الترتيب، ثم نقاط الاتفاق والاختلاف وسبب الاختلاف إن نُصّ عليه فقط. لا تختر رأيًا ولا تُلمّح إلى أفضلية.
"""

_PREFERENCE = ("الرأي الصحيح", "الأصح", "الراجح", "الأرجح", "الصواب هو", "القول الصحيح", "الأقوى")
_IMPERSONATION = re.compile(r"(^|\s)أنا\s+(الشيخ|الإمام|ابن|الألباني|العثيمين|باز)")
_URL = re.compile(r"https?://\S+")
_NUM = re.compile(r"\d+(?:[.,٬،]\d+)*")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# spelled-out numbers in classical texts ("واحد في الأربعين", "خمسة وعشرين") count as present in the context
_UNITS = {"واحد": 1, "واحده": 1, "اثنان": 2, "اثنين": 2, "ثلاث": 3, "ثلاثه": 3, "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5,
          "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7, "ثمان": 8, "ثمانيه": 8, "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10}
_TENS = {"عشرين": 20, "عشرون": 20, "ثلاثين": 30, "ثلاثون": 30, "اربعين": 40, "اربعون": 40, "خمسين": 50, "خمسون": 50,
         "ستين": 60, "ستون": 60, "سبعين": 70, "سبعون": 70, "ثمانين": 80, "ثمانون": 80, "تسعين": 90, "تسعون": 90}
_BIG = {"مئه": 100, "مائه": 100, "مئتين": 200, "مائتين": 200, "الف": 1000, "الفين": 2000, "مليون": 1_000_000}
_WORD_NUM = re.compile(
    r"(?:^|\s)(?:و|ب|ل|ف)?(?:ال)?(" + "|".join(sorted({**_UNITS, **_TENS, **_BIG}, key=len, reverse=True)) + r")(?=\s|$)"
)


def _spelled_numbers(text: str) -> set[str]:
    n = normalize(text)
    words = [m.group(1) for m in _WORD_NUM.finditer(n)]
    out: set[str] = set()
    for i, w in enumerate(words):
        val = _UNITS.get(w) or _TENS.get(w) or _BIG.get(w)
        out.add(str(val))
        if w in _UNITS and i + 1 < len(words) and words[i + 1] in _TENS:  # خمسة وعشرين
            out.add(str(_UNITS[w] + _TENS[words[i + 1]]))
    return out


def _canon(tok: str) -> str:
    plain = re.sub(r"[,٬،]", "", tok)  # 10,000 -> 10000
    return plain.rstrip("0").rstrip(".") if "." in plain else plain  # 250.00 -> 250


def _numbers(text: str, with_parts: bool = False) -> set[str]:
    out: set[str] = set()
    for m in _NUM.finditer(text.translate(_AR_DIGITS)):
        tok = m.group(0)
        out.add(_canon(tok))
        if with_parts:  # "85,595" in a source line also vouches for 85 and 595
            for part in re.split(r"[.,٬،]", tok):
                out.add(part.lstrip("0") or "0")
    return out


def validate(output: str, context: str) -> str | None:
    """Return a rejection reason when `output` says something `context` does not contain, else None."""
    if not output or not output.strip():
        return "empty"
    if len(output) > MAX_OUTPUT_CHARS:
        return "too_long"
    if _IMPERSONATION.search(output):
        return "impersonation"
    ctx_norm = normalize(context)
    for phrase in _PREFERENCE:
        if normalize(phrase) in normalize(output) and normalize(phrase) not in ctx_norm:
            return f"preference_language:{phrase}"
    ctx_urls = set(_URL.findall(context))
    for url in _URL.findall(output):
        if url.rstrip(".،,") not in ctx_urls:
            return "unknown_url"
    allowed = _numbers(context, with_parts=True) | _spelled_numbers(context)
    for num in _numbers(output):
        if num not in allowed:
            return f"unknown_number:{num}"
    return None


def answer_context(message: str, data: dict) -> str:
    parts = [f"سؤال المستخدم: {message}"]
    if data.get("topic"):
        parts.append(f"الموضوع: {data['topic']['label_ar']}")
    clone = data.get("clone") or {}
    if clone.get("name_ar"):
        parts.append(f"المنهج: {clone['name_ar']}")
    facts = {k: v for k, v in (data.get("answers") or {}).items() if not k.startswith("_")}
    if facts:
        parts.append("معلومات صرّح بها المستخدم: " + "، ".join(f"{k}={v}" for k, v in facts.items()))
    for n in data.get("notes") or []:
        parts.append(f"تنبيه: {n}")
    for s in data.get("sections") or []:
        parts.append(f"[{s['title']}]\n{s['text']}")
    calc = data.get("calculation")
    if calc and calc.get("steps"):
        parts.append("[خطوات الحساب]\n" + "\n".join(calc["steps"]))
    cites = data.get("citations") or []
    if cites:
        parts.append("[المصادر المرفقة]\n" + "\n".join("؛ ".join(c["lines"]) + f" — حالة التحقق: {c['verification_status']}" for c in cites))
    return "\n\n".join(parts)


def compare_context(message: str, data: dict) -> str:
    parts = [f"سؤال المستخدم: {message}"]
    if data.get("topic"):
        parts.append(f"الموضوع: {data['topic']['label_ar']}")
    for f in data.get("findings") or []:
        block = [f"[{f['label']}]"]
        if f.get("found"):
            block.append(f"الحكم كما ورد في مصادره: {f.get('ruling') or ''}")
            if f.get("statement"):
                block.append(f"النص: «{f['statement']}»")
            if f.get("is_mashhur") is True:
                block.append("هذا هو المشهور في المذهب.")
            for o in f.get("other_opinions") or []:
                block.append(f"قول آخر: {o}")
            if f.get("reason_for_difference"):
                block.append(f"سبب الاختلاف (كما في السجل): {f['reason_for_difference']}")
            block.append(f"حالة التحقق: {f.get('verification_status')}")
        else:
            block.append("لا سجل موثق كافٍ في مصادر هذا المنهج.")
        parts.append("\n".join(block))
    for title, key in (("نقاط الاتفاق", "agreement_points"), ("نقاط الاختلاف", "difference_points"), ("سبب الاختلاف", "reasons")):
        if data.get(key):
            parts.append(f"[{title}]\n" + "\n".join(data[key]))
    if data.get("disclaimer_ar"):
        parts.append(f"تنبيه: {data['disclaimer_ar']}")
    return "\n\n".join(parts)


@dataclass
class Narrative:
    used: bool
    text: str | None = None
    reason: str | None = None
    model: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class GeminiRephraser:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _generate(self, system: str, user: str) -> str:
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},  # thinking models spend tokens on reasoning too
        }
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(GEMINI_URL.format(model=self.model), json=body, headers={"x-goog-api-key": self.api_key})
            r.raise_for_status()
        cands = r.json().get("candidates") or []
        if not cands:
            raise RuntimeError("no candidates")
        if cands[0].get("finishReason") not in (None, "STOP"):
            raise RuntimeError(f"finish_reason={cands[0].get('finishReason')}")
        return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if not p.get("thought"))

    async def _narrate(self, system: str, context: str) -> Narrative:
        if not self.enabled:
            return Narrative(used=False, reason="no_api_key")
        try:
            text = (await self._generate(system, "المادة المعطاة:\n\n" + context)).strip()
        except Exception as e:  # network / quota / bad key: template answer is served unchanged
            return Narrative(used=False, reason=f"request_failed: {type(e).__name__}", model=self.model)
        problem = validate(text, context)
        if problem:
            return Narrative(used=False, reason=f"rejected: {problem}", model=self.model)
        return Narrative(used=True, text=text, model=self.model)

    async def narrate_answer(self, message: str, data: dict) -> Narrative:
        return await self._narrate(SYSTEM_AR, answer_context(message, data))

    async def narrate_compare(self, message: str, data: dict) -> Narrative:
        return await self._narrate(SYSTEM_COMPARE_AR, compare_context(message, data))
