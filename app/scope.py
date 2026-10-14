"""Scope guard: decides whether a message belongs to zakat jurisprudence before anything else runs.

Layer 1 (deterministic, always on): explicit zakat vocabulary, topic keywords, greeting patterns and
a list of clearly foreign domains (other acts of worship, programming, sports, weather, ...).
Layer 2 (optional): when the message has no explicit zakat word, a tiny Gemini yes/no classification
refines the verdict; on any failure the deterministic verdict stands. The guard never retrieves,
computes or answers — it only gates the pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import normalize
from .topics import detect_topics

SCOPE_TITLE_AR = "هذا النظام متخصص في فقه الزكاة فقط"
SCOPE_MESSAGE_AR = (
    "لا أستطيع الإجابة عن هذا الطلب لأنه خارج نطاق الزكاة. "
    "من فضلك اطرح سؤالًا عن الزكاة: مالٍ أو ذهبٍ أو تجارةٍ أو أسهمٍ أو ديونٍ أو زروعٍ أو أنعامٍ، أو زكاة الفطر ومصارف الزكاة."
)
UNCLEAR_MESSAGE_AR = (
    "لم أتبيّن أن سؤالك يتعلق بالزكاة، وهذا النظام لا يجيب إلا في فقه الزكاة. "
    "أعد صياغة السؤال بذكر نوع المال أو المسألة (مثل: زكاة الراتب، نصاب الذهب، الدين، عروض التجارة، زكاة الفطر)، "
    "أو اختر الموضوع من قائمة «الموضوع»."
)
GREETING_MESSAGE_AR = (
    "أهلًا بك. هذا النظام متخصص في فقه الزكاة فقط: اسأل عن حكم زكاة مالٍ معيّن أو نصابه أو حساب المقدار الواجب أو مصارف الزكاة، "
    "وسأجيبك من مصادر المنهج المختار مع ذكر المصدر لكل حكم."
)
EXAMPLES_AR = (
    "عندي 10,000 دولار مدخرة منذ سنة، كم زكاتها؟",
    "هل في حلي الذهب المستعمل زكاة؟",
    "كيف أزكي بضاعة محلي التجاري؟",
    "لي دين عند شخص، هل أزكيه؟",
    "هل يجوز إخراج زكاة الفطر نقدًا؟",
    "هل تُعطى الزكاة للأقارب؟",
)

# explicit zakat vocabulary (normalised): any of these settles the matter without the model
_ZAKAT_RE = re.compile(
    r"(^|\s)(ال)?(زكا|زكو|زكي|زكه|ازك|تزك|يزك|نزك|مزك)|"
    r"(^|\s)(ال)?نصاب|صدق[هة] الفطر|(^|\s)(ال)?فطر[هة](\s|$)|"
    r"(^|\s)(ال)?مصارف(\s|$)|نصف العشر|ربع العشر|"
    r"(^|\s)(zakat|zakah|nisab)(\s|$)"
)

_GREETING_WORDS = {
    "السلام", "عليكم", "ورحمه", "الله", "وبركاته", "سلام", "مرحبا", "مرحبًا", "اهلا", "اهلًا", "هلا", "هاي", "صباح", "مساء",
    "الخير", "النور", "شكرا", "شكرًا", "جزاك", "جزاكم", "خيرا", "خيرًا", "بارك", "فيك", "فيكم", "تمام", "ممتاز", "حسنا", "طيب",
    "ok", "hi", "hello", "hey", "thanks", "thank", "you", "salam", "و",
}

# clearly foreign domains (normalised stems). Words that are also merchandise, money vocabulary or
# fiqh terms shared with zakat (بيع، إيجار، مطعم، جوال، إنترنت، مشهور...) are deliberately absent.
_OFF_TOPIC_MARKERS = (
    # other acts of worship / other fiqh chapters
    "صلاه", "اصلي", "صليت", "صلوات", "وضوء", "تيمم", "غسل", "طهاره", "حيض", "نفاس", "صيام", "صوم", "افطر", "افطرت",
    "سحور", "تراويح", "حج", "العمره", "طواف", "احرام", "اضحيه", "عقيقه", "طلاق", "زواج", "نكاح", "مهر", "خطبه",
    "ميراث", "ارث", "وصيه", "وقف", "قصاص", "جنايه", "رضاع", "حضانه", "اذان", "جنازه", "قبر", "دفن", "تفسير", "سوره",
    "ايه", "تجويد", "عقيده", "توحيد", "بدعه", "شرك", "ربا", "قمار", "ميسر", "كفاله",
    # programming / writing tasks
    "برمجه", "برنامج", "كود", "code", "python", "javascript", "java", "html", "css", "sql", "بايثون", "جافا", "سيرفر",
    "خطا في", "error", "bug", "api", "ترجم", "ترجمه", "translate", "قصيده", "قصه", "شعر", "روايه", "اكتب لي", "اكتب",
    "لخص", "ملخص", "مقال", "ايميل", "بريد", "سيره ذاتيه", "cv",
    # everyday / general knowledge
    "وصفه", "طبخ", "اطبخ", "كيك", "كره", "مباراه", "رياضه", "لاعب", "بطوله", "طقس", "الجو", "حراره", "درجه الحراره",
    "سياسه", "انتخابات", "حرب", "فيلم", "مسلسل", "اغنيه", "موسيقى", "رياضيات", "معادله", "جذر", "هندسه", "فيزياء",
    "كيمياء", "جغرافيا", "عاصمه", "طبيب", "نكته", "لعبه", "العاب", "game",
    "ذكاء اصطناعي", "الذكاء الاصطناعي", "chatgpt", "gpt", "gemini", "نموذج لغوي", "روبوت", "الوقت الان", "الساعه", "التاريخ اليوم",
    "طيران", "طائره", "حجز", "سفر", "تاشيره", "واي فاي", "كلمه مرور",
    "weather", "football", "recipe", "poem", "story", "joke", "movie", "song", "prayer", "fasting", "hajj", "marriage",
)

# questions about the assistant itself get the welcome text rather than a refusal
_IDENTITY = ("من انت", "من صنعك", "من انشاك", "من طورك", "ما هذا النظام", "ما هذا الموقع", "ماذا تفعل", "ماذا تستطيع", "كيف استخدم", "كيف استعمل", "who are you", "what can you do")

# a bare asset noun (أرض، سيارة، ذهب…) is only a zakat question when the user talks about *owning* it:
# an amount, a currency, a holding period or ownership/finance vocabulary. Otherwise the model (or the user) decides.
_CONTEXT_WORDS = (
    "عندي", "لدي", "املك", "ملكت", "ورثت", "ورث", "ادخر", "مدخر", "مدخرات", "راتب", "راتبي", "مال", "مالي", "اموال", "قيمه", "قيمته",
    "ثمن", "سعر", "للبيع", "للتجاره", "تجاره", "مؤجر", "مؤجره", "اجار", "ايجار", "ربح", "ارباح", "شركه", "شركتي", "اسهم",
    "دين", "ديني", "ديون", "قرض", "مدين", "حول", "الحول", "سنه", "سنتين", "سنوات", "شهر", "اشهر", "منذ", "حساب", "احسب", "جرام", "جرامات", "غرام",
    "دولار", "ريال", "جنيه", "دينار", "درهم", "يورو", "ليره", "الف", "الاف", "مليون", "مستحق", "فقير", "فقراء", "مسكين", "مساكين",
    "الفقير", "الفقراء", "المساكين", "صدقه", "الصدقه", "اخرج", "اخراج", "ادفع", "تجب", "يجب", "واجب", "واجبه",
)
_CLITICS = ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك")

# stems that collide with other words (حجز/شركة/الميراث…): only count them as standalone words
_WORD_ONLY = {"حج", "العمره", "الجو", "ارث", "غسل", "مهر", "وقف", "شرك", "ربا", "كره", "جذر", "كود", "ايه", "gpt", "api", "css", "sql", "java", "cv", "bug"}


@dataclass
class ScopeDecision:
    verdict: str  # in_scope | out_of_scope | greeting | unclear
    reason: str
    method: str = "rules"

    @property
    def blocked(self) -> bool:
        return self.verdict != "in_scope"

    def payload(self) -> dict:
        if self.verdict == "greeting":
            title, message = "مرحبًا بك في متخصص فقه الزكاة", GREETING_MESSAGE_AR
        elif self.verdict == "unclear":
            title, message = SCOPE_TITLE_AR, UNCLEAR_MESSAGE_AR
        else:
            title, message = SCOPE_TITLE_AR, SCOPE_MESSAGE_AR
        return {
            "kind": self.verdict,
            "title_ar": title,
            "message_ar": message,
            "examples_ar": list(EXAMPLES_AR),
            "reason": self.reason,
            "method": self.method,
        }


def has_zakat_word(text: str) -> bool:
    return bool(_ZAKAT_RE.search(normalize(text)))


def is_greeting(text: str) -> bool:
    n = normalize(text)
    words = n.split()
    if words and len(words) <= 8 and all(w in _GREETING_WORDS for w in words):
        return True
    return len(words) <= 8 and any(p in n for p in _IDENTITY)


def _bare_forms(word: str) -> set[str]:
    """The word itself plus the word with one leading clitic (و/ف/ب/ل/ك/ال…) removed."""
    forms = {word}
    for c in _CLITICS:
        if word.startswith(c) and len(word) > len(c) + 1:
            forms.add(word[len(c):])
            break
    return forms


def off_topic_markers(text: str) -> list[str]:
    n = normalize(text)
    words = [f for w in n.split() for f in _bare_forms(w)]
    found: list[str] = []
    for m in _OFF_TOPIC_MARKERS:
        k = normalize(m)
        if m in _WORD_ONLY:
            hit = k in words
        elif " " in k:
            hit = k in n
        else:
            hit = any(w.startswith(k) for w in words)
        if hit:
            found.append(m)
    return found


def has_finance_context(text: str) -> bool:
    n = normalize(text)
    if re.search(r"\d", n):
        return True
    words = {f for w in n.split() for f in _bare_forms(w)}
    return any(c in words for c in _CONTEXT_WORDS)


def classify_rules(message: str, *, topic_hint: bool = False, continuation: bool = False) -> ScopeDecision:
    """Deterministic verdict. `topic_hint` = user picked a topic from the list; `continuation` = answering clarifying questions."""
    if topic_hint or continuation:
        return ScopeDecision("in_scope", "continuation" if continuation else "topic_selected")
    n = normalize(message)
    if not n:
        return ScopeDecision("unclear", "empty")
    if has_zakat_word(n):
        return ScopeDecision("in_scope", "zakat_word")
    if is_greeting(n):
        return ScopeDecision("greeting", "greeting")
    detected = detect_topics(message)
    markers = off_topic_markers(message)
    if not detected:
        return ScopeDecision("out_of_scope", "markers:" + ",".join(markers[:3])) if markers else ScopeDecision("unclear", "no_topic")
    topic_id = detected[0][0].id
    strong = detected[0][1] >= 2.0  # at least one whole-word (non-currency) keyword match
    if not markers:
        context = has_finance_context(message)
        if context and (strong or re.search(r"\d", n)):  # "ورثت 100 ألف ريال منذ سنة": currency-only match but a real amount
            return ScopeDecision("in_scope", f"topic:{topic_id}")
        return ScopeDecision("unclear", f"topic:{topic_id};no_context")
    if strong:
        return ScopeDecision("unclear", f"topic:{topic_id};markers:" + ",".join(markers[:3]))
    return ScopeDecision("out_of_scope", "markers:" + ",".join(markers[:3]))


class ScopeGuard:
    """Combines the rules with an optional LLM yes/no check (only when no explicit zakat word decided the case)."""

    @staticmethod
    def _without_model(rules: ScopeDecision) -> ScopeDecision:
        if rules.verdict == "unclear" and "markers:" in rules.reason:
            return ScopeDecision("out_of_scope", rules.reason)  # topic word + foreign domain, no model to arbitrate
        return rules  # asset word without ownership context: ask the user to rephrase

    def __init__(self, llm=None):
        self.llm = llm

    async def classify(self, message: str, *, topic_hint: bool = False, continuation: bool = False) -> ScopeDecision:
        rules = classify_rules(message, topic_hint=topic_hint, continuation=continuation)
        if rules.verdict != "unclear":
            return rules  # explicit zakat vocabulary, a clear foreign domain, a greeting or a continuation: no model call
        if rules.reason == "no_topic" and len(normalize(message).split()) <= 2:
            return rules  # "نعم" / a bare number outside a clarification flow: ask for a real question, no model call
        if self.llm is None or not getattr(self.llm, "enabled", False):
            return self._without_model(rules)
        verdict = await self.llm.classify_scope(message)
        if verdict is None:
            return self._without_model(rules)
        if verdict:
            return ScopeDecision("in_scope", f"llm_yes;{rules.reason}", method="llm")
        return ScopeDecision("out_of_scope", f"llm_no;{rules.reason}", method="llm")
