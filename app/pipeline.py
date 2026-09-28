"""Answer pipeline for a single clone.

    user message → topic classification → slot extraction → missing-information
    detection → clarifying questions → retrieval (clone-scoped) → source
    verification filter → opinion extraction → answer composition → citations

The composer is deliberately template-based: every sentence in the answer is either
(a) text copied from a verified record, (b) a clearly labelled system analysis that
applies the record to the user's answers, or (c) arithmetic. Nothing is generated
from model memory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import calculator as calc
from .kb import CloneMeta, KnowledgeBase, Record
from .normalize import extract_numbers, normalize
from .questioning import (
    Answers,
    Slot,
    extract_from_text,
    missing_slots,
    slot_to_dict,
    slots_for_topic,
)
from .retrieval import Hit, Retriever, specific_tags
from .topics import TOPIC_BY_ID, Topic, detect_topics

NOT_FOUND_AR = "لم أجد في المصادر التي تم التحقق منها ما يكفي لإسناد هذا الحكم."
REFER_AR = (
    "هذه المسألة تحتاج إلى الرجوع إلى جهة إفتاء أو عالم متخصص، خصوصًا لعدم توفر نص موثق كافٍ "
    "في قاعدة المصادر الحالية."
)
CLONE_LABEL = {
    "albani": "الألباني",
    "ibn_uthaymeen": "ابن عثيمين",
    "ibn_baz": "ابن باز",
    "maliki": "المذهب المالكي",
}
CALC_WORDS = ("كم", "احسب", "حساب", "زكاتي", "المبلغ الواجب", "كم اخرج", "كم ادفع", "مقدار زكاة")
RULING_ONLY_WORDS = ("الحكم فقط", "بدون حساب", "لا اريد حساب", "ما الحكم", "هل يجوز", "هل تجب", "هل يجب")
SKIPPED = "__skip__"  # answer value the UI sends when the user declines to answer a question
MAX_QUESTIONS_PER_TURN = 3
MIN_SCORE = 0.25
MAX_RECORDS = 5
RELATED_MIN_LEXICAL = 0.7  # unrelated-topic records need a strong lexical match to be shown
RELATED_TOPIC_MIN_LEXICAL = 0.15  # same-topic records need at least some lexical overlap

CURRENCY_HINTS: list[tuple[tuple[str, ...], str, str | None]] = [
    (("دولار", "usd", "$"), "USD", None),
    (("يورو", "eur", "€"), "EUR", None),
    (("جنيه استرليني", "استرليني", "gbp"), "GBP", None),
    (("جنيه", "egp"), "EGP", "فُهم «جنيه» على أنه الجنيه المصري؛ صحّح العملة إن كانت غير ذلك."),
    (("ريال سعودي", "sar", "ر.س"), "SAR", None),
    (("ريال قطري", "qar"), "QAR", None),
    (("ريال عماني", "omr"), "OMR", None),
    (("ريال يمني", "yer"), "YER", None),
    (("ريال",), "SAR", "فُهم «ريال» على أنه الريال السعودي؛ صحّح العملة إن كانت غير ذلك."),
    (("درهم اماراتي", "aed"), "AED", None),
    (("درهم مغربي", "mad"), "MAD", None),
    (("درهم",), "AED", "فُهم «درهم» على أنه الدرهم الإماراتي؛ صحّح العملة إن كانت غير ذلك."),
    (("دينار كويتي", "kwd"), "KWD", None),
    (("دينار اردني", "jod"), "JOD", None),
    (("دينار جزائري", "dzd"), "DZD", None),
    (("دينار عراقي", "iqd"), "IQD", None),
    (("دينار تونسي", "tnd"), "TND", None),
    (("دينار ليبي", "lyd"), "LYD", None),
    (("ليرة تركية", "try"), "TRY", None),
    (("روبية", "pkr"), "PKR", None),
]


@dataclass
class Question:
    slot: Slot

    def to_dict(self) -> dict:
        return slot_to_dict(self.slot)


@dataclass
class Section:
    kind: str  # ruling | evidence | statement | analysis | calculation | conclusion | khilaf | note
    title: str
    text: str
    record_id: str | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title, "text": self.text, "record_id": self.record_id}


@dataclass
class Citation:
    record_id: str
    lines: list[str]
    quote: str | None
    url: str | None
    verification_status: str
    verification_notes: str | None
    source_type: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class PipelineResult:
    stage: str  # clarify | answer
    clone: str
    topic: Topic | None
    answers: Answers
    questions: list[Question] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    calculation: dict | None = None
    not_found: bool = False
    notes: list[str] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)

    def to_dict(self, meta: CloneMeta | None) -> dict:
        return {
            "stage": self.stage,
            "clone": {
                "id": self.clone,
                "name_ar": meta.name_ar if meta else self.clone,
                "disclaimer_ar": meta.disclaimer_ar if meta else "",
            },
            "topic": {"id": self.topic.id, "label_ar": self.topic.label_ar, "group": self.topic.group} if self.topic else None,
            "answers": self.answers,
            "questions": [q.to_dict() for q in self.questions],
            "sections": [s.to_dict() for s in self.sections],
            "citations": [c.to_dict() for c in self.citations],
            "calculation": self.calculation,
            "not_found": self.not_found,
            "notes": self.notes,
            "records": [h.record.id for h in self.hits],
        }


# --------------------------------------------------------------------------- helpers


def detect_currency(text: str) -> tuple[str | None, str | None]:
    raw = text or ""
    n = f" {normalize(raw)} "
    for words, code, note in CURRENCY_HINTS:
        for w in words:
            if w in ("$", "€"):
                if w in raw:
                    return code, note
            elif w.isascii():
                if f" {w.lower()} " in n:  # ISO code as a whole word
                    return code, note
            elif normalize(w) in n:  # Arabic name, allow prefixes/suffixes (بالدولار، دولارا)
                return code, note
    return None, None


def wants_calculation(text: str, topic: Topic | None, answers: Answers) -> bool:
    if topic is None or not topic.calculable:
        return False
    n = normalize(text)
    if any(normalize(w) in n for w in RULING_ONLY_WORDS) and not any(normalize(w) in n for w in CALC_WORDS):
        return False
    if any(normalize(w) in n for w in CALC_WORDS):
        return True
    if extract_numbers(text):
        return True
    return any(k in answers for k in ("amount", "gold_grams", "silver_grams", "trade_value", "stock_value", "fitr_count"))


# answers that change which records matter: (query words, extra area-marker tag they make relevant)
_RETRIEVAL_HINTS: dict[tuple[str, str], tuple[str, str | None]] = {
    ("debts", "due_now"): ("الدين الذي على المزكي هل يمنع الزكاة", "debts"),
    ("debts", "installments"): ("الدين الذي على المزكي أقساط", "debts"),
    ("purpose", "trade"): ("عروض التجارة", "trade"),
    ("other_money", "yes"): ("ضم الأموال تكميل النصاب", None),
    ("jewelry_use", "worn"): ("حلي المرأة المستعمل", None),
    ("jewelry_use", "stored"): ("الذهب المدخر للقنية", None),
    ("jewelry_use", "trade"): ("حلي للتجارة عروض", "trade"),
    ("stock_intent", "trade"): ("أسهم للمضاربة والبيع", "trade"),
    ("stock_intent", "invest"): ("أسهم للاستثمار والأرباح", None),
    ("property_intent", "sale"): ("عقار معد للبيع", "trade"),
    ("property_intent", "rent"): ("عقار مؤجر غلة", None),
    ("property_intent", "residence"): ("بيت السكن", None),
    ("trade_receivables", "yes"): ("ديون التجارة على الناس", "debts"),
}

# tags that mark a record as belonging to a distinct zakat area; a record carrying another area's
# marker is off-topic for the current question unless it matches lexically very well
_GROUP_MARKERS: dict[str, str] = {
    "zakat_al_fitr": "zakat_fitr", "crops": "crops", "livestock": "livestock",
    "recipients": "recipients", "real_estate": "real_estate", "stocks": "investments",
    "debts": "debts", "trade": "trade", "gold": "gold_silver", "silver": "gold_silver",
    "jewelry": "gold_silver", "crypto": "modern",
}


# when the user has stated a situation does NOT apply, records about that situation are not the primary ruling
_EXCLUDING_ANSWERS: dict[tuple[str, str], set[str]] = {
    ("debts", "none"): {"debts", "loans", "receivables"},
    ("purpose", "savings"): {"salary", "trade", "profit"},
    ("purpose", "trade"): {"salary"},
    ("other_money", "no"): {"combining"},
}


def _excluded_tags(answers: Answers) -> set[str]:
    out: set[str] = set()
    for (slot, value), tags in _EXCLUDING_ANSWERS.items():
        if answers.get(slot) == value:
            out |= tags
    return out


def _active_hints(answers: Answers) -> list[tuple[str, str | None]]:
    return [hint for (slot, value), hint in _RETRIEVAL_HINTS.items() if answers.get(slot) == value]


def _retrieval_query(message: str, topic: Topic, answers: Answers) -> str:
    return " ".join([message, topic.label_ar, *(text for text, _ in _active_hints(answers))])


def _conflicts_with_topic(record_tags: list[str], topic: Topic, answers: Answers) -> bool:
    if topic.group == "basics" or specific_tags(topic.id) & set(record_tags):
        return False
    own_groups = {topic.group, topic.id}
    allowed = set(topic.tags) | {marker for _, marker in _active_hints(answers) if marker}
    return any(
        marker in record_tags and group not in own_groups and marker not in allowed
        for marker, group in _GROUP_MARKERS.items()
    )


def _clone_param(meta: CloneMeta | None, name: str) -> str | None:
    if meta is None:
        return None
    p = _calc_param(meta, name)
    return p.value if p else None


def _calc_param(meta: CloneMeta, name: str):
    return {
        "nisab_standard": meta.calc_params.nisab_standard,
        "debt_deduction": meta.calc_params.debt_deduction,
        "jewelry_worn": meta.calc_params.jewelry_worn,
        "fitr_cash": meta.calc_params.fitr_cash,
        "trade_valuation": meta.calc_params.trade_valuation,
        "trade_goods": meta.calc_params.trade_goods,
        "child_wealth": meta.calc_params.child_wealth,
    }[name]


def _param_basis(meta: CloneMeta | None, name: str) -> list[str]:
    if meta is None:
        return []
    p = _calc_param(meta, name)
    return list(p.basis_records) if p else []


def _param_note(meta: CloneMeta | None, name: str) -> str | None:
    if meta is None:
        return None
    p = _calc_param(meta, name)
    return p.note_ar if p else None


def make_citation(rec: Record, scholar_label: str) -> Citation:
    src = rec.primary_source
    return Citation(
        record_id=rec.id,
        lines=[f"المصدر: {rec.id}"] + src.citation_lines(scholar_label),
        quote=src.quote,
        url=src.url,
        verification_status=rec.verification_status,
        verification_notes=rec.verification_notes,
        source_type=src.source_type,
    )


# --------------------------------------------------------------------------- pipeline


class Pipeline:
    def __init__(self, kb: KnowledgeBase, retriever: Retriever):
        self.kb = kb
        self.retriever = retriever

    # ---- stage 1-3: classify, extract, detect missing ----
    def prepare(self, clone: str, message: str, topic_id: str | None, answers: Answers | None) -> PipelineResult:
        answers = dict(answers or {})
        topic = TOPIC_BY_ID.get(topic_id) if topic_id else None
        if topic is None:
            detected = detect_topics(message)
            topic = detected[0][0] if detected else None
        result = PipelineResult(stage="clarify", clone=clone, topic=topic, answers=answers)
        if topic is None:
            return result

        answers = extract_from_text(message, topic.id, answers)
        code, note = detect_currency(message)
        if code and "currency" not in answers:
            answers["currency"] = code
            if note:
                result.notes.append(note)
        result.answers = answers
        need_calc = wants_calculation(message, topic, answers)
        answers.setdefault("_want_calc", need_calc)

        skipped_ids = {k for k, v in answers.items() if v == SKIPPED} | set(answers.get("_skipped") or [])
        for k in skipped_ids:
            answers.pop(k, None)
        skipped = [s for s in [*slots_for_topic(topic.id), CURRENCY_SLOT] if s.id in skipped_ids]
        if skipped:
            answers["_skipped"] = sorted(skipped_ids)
            if any(s.required_for == "calculation" for s in skipped) and answers.get("_want_calc"):
                answers["_want_calc"] = False
            if not answers.get("_want_calc") and need_calc:
                result.notes.append("لم تُدخل بعض بيانات الحساب، فلن يُحسب المبلغ؛ يُعرض الحكم من المصادر فقط.")
            ruling_skipped = [s for s in skipped if s.required_for == "ruling"]
            if ruling_skipped:
                result.notes.append(
                    "لم تُجب عن: " + "، ".join(s.question_ar for s in ruling_skipped)
                    + "؛ لذا يُعرض ما في المصادر دون تطبيقه على هذه النقاط، ولا يُفترض فيها شيء."
                )
            need_calc = bool(answers.get("_want_calc"))

        missing = [s for s in missing_slots(topic.id, answers, need_calculation=need_calc) if s.id not in skipped_ids]
        # currency is needed whenever we will price nisab
        if need_calc and topic.id in ("cash", "bank", "salary", "ewallet", "crypto", "trade_goods", "company", "stocks",
                                      "property_for_sale", "rented_property", "debt_owed_to_you", "debt_on_you") \
                and not answers.get("currency") and CURRENCY_SLOT.id not in skipped_ids:
            missing.append(CURRENCY_SLOT)
        # ruling-relevant questions first, then calculation inputs; never more than a few per turn
        missing.sort(key=lambda s: 0 if s.required_for == "ruling" else 1)
        result.questions = [Question(s) for s in missing[:MAX_QUESTIONS_PER_TURN]]
        if not missing:
            result.stage = "answer"
        return result

    # ---- stage 4-9: retrieve, filter, extract, compose ----
    def answer(self, prepared: PipelineResult, message: str, market: dict | None, market_error: str | None) -> PipelineResult:
        res = prepared
        topic = res.topic
        meta = self.kb.meta.get(res.clone)
        label = CLONE_LABEL.get(res.clone, res.clone)
        query = message if topic is None else _retrieval_query(message, topic, res.answers)
        hits = self.retriever.search(res.clone, query, topic.id if topic else None, k=8)
        hits = [h for h in hits if h.score >= MIN_SCORE]
        # source verification filter: unverified records are never used for the ruling itself
        usable = [h for h in hits if h.record.verification_status in ("verified", "partially_verified")]
        # topic filter: records explicitly about another zakat area are dropped; the primary record is
        # the best on-topic hit (falling back to the best overall hit when nothing is tagged for the topic)
        if topic is not None:
            usable = [h for h in usable if not _conflicts_with_topic(h.record.tags, topic, res.answers)]
            on_topic = [h for h in usable if h.matched_topic]
            if on_topic:
                excluded = _excluded_tags(res.answers)
                # "how much is my zakat" is answered first by the record stating the obligation/rate/nisab
                core = {"rate", "nisab", "conditions", "obligation"} if res.answers.get("_want_calc") else set()
                primary_hit = min(
                    on_topic,
                    key=lambda h: (
                        bool(excluded & set(h.record.tags)),
                        "hadith_grading" in h.record.tags,  # gradings are evidence, not the ruling itself
                        not (core & set(h.record.tags)),
                        -h.score,
                    ),
                )
                usable = [primary_hit] + [
                    h for h in usable
                    if h is not primary_hit
                    and (h.lexical >= RELATED_MIN_LEXICAL or (h.matched_topic and h.lexical >= RELATED_TOPIC_MIN_LEXICAL))
                ]
        usable = usable[:MAX_RECORDS]
        res.hits = usable
        res.stage = "answer"

        if not usable:
            res.not_found = True
            res.sections.append(Section("note", "النتيجة", NOT_FOUND_AR))
            if topic and topic.group == "modern":
                res.sections.append(Section("note", "توجيه", REFER_AR))
            return res

        primary = usable[0].record
        secondary = [h.record for h in usable[1:4]]

        # -- الحكم
        res.sections.append(Section("ruling", "الحكم (كما ورد في المصدر)", primary.ruling, primary.id))
        if primary.verification_status == "partially_verified":
            res.sections.append(Section("note", "تنبيه على درجة التحقق",
                                        f"هذا السجل متحقق منه جزئيًّا: {primary.verification_notes or ''}".strip(), primary.id))
        # -- الدليل
        if primary.evidence or primary.quran or primary.hadith:
            lines = []
            for q in primary.quran:
                lines.append(f"قال الله تعالى: «{q.text}» [{q.surah}: {q.ayah}]" if q.text else f"[{q.surah}: {q.ayah}]")
            for h in primary.hadith:
                grade = f" — {h.grading}" + (f" ({h.grading_by})" if h.grading_by else "") if h.grading else ""
                lines.append(f"حديث: «{h.text}» — {h.reference}{grade}")
            if primary.evidence:
                lines.append(primary.evidence)
            res.sections.append(Section("evidence", "الدليل", "\n".join(lines), primary.id))
        if primary.evidence_explanation:
            res.sections.append(Section("evidence", "شرح الدليل / التعليل", primary.evidence_explanation, primary.id))
        # -- قول العالم / النص
        if primary.scholar_statement:
            title = "نص المتن/الشرح" if res.clone == "maliki" else f"قول الشيخ ({label})"
            res.sections.append(Section("statement", title, f"«{primary.scholar_statement}»", primary.id))
        # -- الخلاف داخل المذهب
        if res.clone == "maliki" and (primary.is_mashhur is not None or primary.other_opinions):
            parts = []
            if primary.is_mashhur is True:
                parts.append("ما تقدم هو المشهور في المذهب.")
            elif primary.is_mashhur is False:
                parts.append("ما تقدم ليس هو المشهور في المذهب.")
            for o in primary.other_opinions:
                parts.append(f"قول آخر: {o}")
            if primary.reason_for_difference:
                parts.append(f"سبب الخلاف: {primary.reason_for_difference}")
            res.sections.append(Section("khilaf", "المشهور والأقوال الأخرى داخل المذهب", "\n".join(parts), primary.id))
        # -- مسائل ذات صلة من نفس المصادر
        for rec in secondary:
            res.sections.append(Section("related", f"مسألة ذات صلة: {rec.subtopic}", rec.ruling, rec.id))

        # -- التطبيق على حالة المستخدم + الحساب
        if topic is not None:
            analysis, calculation, extra_ids = self._apply(res.clone, meta, topic, res.answers, market, market_error, usable)
            if analysis:
                res.sections.append(Section("analysis", "التطبيق على حالتك (تحليل نظامي، ليس نصًّا من المصدر)", "\n".join(analysis)))
            if calculation:
                res.calculation = calculation
                res.sections.append(Section("calculation", "الحساب (عملية رياضية)", "\n".join(calculation["steps"])))
            for rid in extra_ids:
                rec = self.kb.get(rid)
                if rec and all(h.record.id != rid for h in usable):
                    usable.append(Hit(rec, 0.0, False))

        # -- الخلاصة
        res.sections.append(Section("conclusion", "الخلاصة", self._conclusion(res, primary, label)))

        # -- التوثيق
        seen: set[str] = set()
        for h in usable:
            if h.record.id in seen:
                continue
            seen.add(h.record.id)
            res.citations.append(make_citation(h.record, h.record.scholar))
        return res

    # ---- applying the clone's parameters to the user's case ----
    def _apply(self, clone: str, meta: CloneMeta | None, topic: Topic, answers: Answers, market: dict | None,
               market_error: str | None, hits: list[Hit]) -> tuple[list[str], dict | None, list[str]]:
        notes: list[str] = []
        basis: list[str] = []
        calculation: dict | None = None
        want_calc = bool(answers.get("_want_calc"))

        if answers.get("ownership") == "not_mine":
            notes.append("ذكرت أن المال ليس ملكك (أمانة/وديعة)؛ فالزكاة إنما تجب على مالكه، ولا يُحسب عليك.")
            return notes, None, basis
        if answers.get("ownership") == "shared":
            notes.append("المال مشترك؛ يُنظر إلى حصتك وحدها في بلوغ النصاب والحساب.")
        if answers.get("hawl") == "no":
            notes.append("ذكرت أن المال لم يحل عليه الحول؛ فلا تجب الزكاة الآن في هذا المال على أصل اشتراط الحول، إلا ما كان ربحًا أو نماءً يتبع أصله على تفصيل المنهج.")
            want_calc = False
        if answers.get("hawl") == "unknown":
            notes.append("لم يُعرف تاريخ بلوغ النصاب؛ يُحدَّد يوم بلغ المال النصاب أول مرة ويُعدّ الحول منه، أو يُختار يوم ثابت ويُزكّى فيه كل ما بلغ النصاب احتياطًا.")
        skipped_ruling = [s for s in slots_for_topic(topic.id) if s.id in (answers.get("_skipped") or []) and s.required_for == "ruling"]
        if skipped_ruling and want_calc:
            notes.append("الحساب الآتي مشروط بتحقق الشروط التي لم تُجب عنها (" + "، ".join(s.id for s in skipped_ruling) + ")؛ فهو تقدير رياضي لا حكمٌ بوجوب هذا المبلغ عليك.")
        if answers.get("purpose") == "spending":
            notes.append("المال الذي يُنفق قبل تمام الحول ولا يبقى منه ما يبلغ النصاب لا زكاة فيه؛ الزكاة فيما يبقى مدخرًا حولًا كاملًا.")
            want_calc = False

        nisab_std = _clone_param(meta, "nisab_standard") or "lower"
        debt_rule = _clone_param(meta, "debt_deduction") or "unspecified"
        deductible = 0.0
        if topic.id in ("cash", "bank", "salary", "ewallet", "gold", "silver", "jewelry", "trade_goods", "stocks", "crypto"):
            debts = answers.get("debts")
            amt = float(answers.get("debt_amount") or 0)
            if debts in ("due_now", "installments") and amt:
                basis += _param_basis(meta, "debt_deduction")
                if debt_rule.startswith("all_debts"):
                    deductible = amt
                    notes.append(f"وفق هذا المنهج يُسقط الدين الذي عليك من وعاء زكاة النقد/العروض ({_param_note(meta, 'debt_deduction') or ''}).".replace(" ()", ""))
                elif debt_rule == "due":
                    deductible = amt if debts == "due_now" else 0.0
                    notes.append("وفق هذا المنهج يُخصم الدين الحالّ فقط دون الأقساط المؤجلة.")
                elif debt_rule == "none":
                    notes.append("وفق هذا المنهج لا يمنع الدين وجوب الزكاة ولا يُخصم من المال؛ فتُزكّي ما بيدك وتقضي دينك من غيره.")
                else:
                    notes.append("لم أجد في مصادر هذا المنهج المتحقق منها نصًّا صريحًا في خصم الدين؛ عُرض الحساب بدون خصم مع التنبيه.")

        # nisab standard explanation
        std_for_calc = {"gold": "gold", "silver": "silver", "lower": "lower", "silver_or_gold_combined": "lower"}.get(nisab_std, "lower")
        if topic.id in ("cash", "bank", "salary", "ewallet", "crypto", "trade_goods", "company", "stocks", "property_for_sale", "debt_owed_to_you"):
            basis += _param_basis(meta, "nisab_standard")
            n = _param_note(meta, "nisab_standard")
            if n:
                notes.append(f"معيار النصاب في هذا المنهج: {n}")
            if clone == "maliki":
                notes.append("النقود الورقية: معاملتها كالعين (الذهب والفضة) تطبيق معاصر لا نصٌّ مالكي متحقق منه في هذه القاعدة؛ راجع السجل mlk_fulus_and_paper_money_gap.")

        # jewelry rule
        if topic.id in ("jewelry", "gold") and answers.get("jewelry_use") == "worn":
            basis += _param_basis(meta, "jewelry_worn")
            rule = _clone_param(meta, "jewelry_worn")
            if rule and rule.startswith("not_obligatory"):
                notes.append("الحلي المباح المتخذ للّبس لا زكاة فيه على المشهور في هذا المنهج؛ فلا يُحسب عليه شيء ما دام كذلك. (تجب فيه إن كان مدخرًا للعاقبة أو للتجارة أو محرمًا.)")
                want_calc = False
            elif rule == "obligatory":
                notes.append("هذا المنهج يوجب الزكاة في الحلي الملبوس إذا بلغ النصاب وحال عليه الحول، فيُحسب كالذهب.")
        # trade goods rule
        if topic.id in ("trade_goods", "company"):
            basis += _param_basis(meta, "trade_goods") + _param_basis(meta, "trade_valuation")
            tg = _clone_param(meta, "trade_goods")
            if tg == "no_fixed_zakat":
                notes.append("هذا المنهج لا يرى زكاة مقدَّرة (2.5%) في عروض التجارة نفسها، بل الزكاة في النقد الحاصل منها؛ فلا يُقدَّم حساب على قيمة البضاعة.")
                want_calc = False
            elif tg and tg.startswith("mudir"):
                notes.append("عند المالكية: إن كنت «مديرًا» (تبيع بالسعر الجاري وتستبدل البضاعة) قوّمت عروضك كل عام وزكّيتها مع النقد؛ وإن كنت «محتكرًا» (تنتظر ارتفاع السوق) فلا زكاة حتى تبيع فتزكّي الثمن لعام واحد. الحساب أدناه يفترض حالة المدير.")
        if topic.id == "stocks" and answers.get("stock_intent") == "long_term":
            notes.append("الأسهم المقصود بها الاستثمار طويل الأجل تُزكّى بحسب ما يقابلها من أصول زكوية للشركة (النقد والبضائع والديون)، لا بقيمتها السوقية كاملة، وإذا أخرجت الشركة الزكاة سقطت عن المساهم؛ لذلك لا يُحسب لها مبلغ مقطوع بدون معرفة أصول الشركة.")
            if answers.get("company_type") != "trading_co":
                want_calc = False
        if topic.id == "debt_owed_to_you" and answers.get("debtor_status") == "insolvent":
            notes.append("الدين غير المرجوّ (على معسر أو مماطل) لا تُخرج زكاته كل عام على أكثر المناهج، بل عند قبضه على تفصيل بينها (لعام واحد عند المالكية، وقيل لما مضى)؛ فلا يُحسب الآن.")
            want_calc = False
        if topic.id == "rented_property":
            notes.append("لا زكاة في قيمة العقار المؤجر نفسه، بل في الأجرة المدخرة إذا بلغت النصاب وحال عليها الحول (وعند المالكية يُستقبل بالغلة حول من يوم قبضها).")
        if topic.id == "property_for_sale" and answers.get("property_intent") == "hold":
            notes.append("العقار/الأرض المقتناة بلا نية بيع لا زكاة فيها؛ فإن نويت بها التجارة عند الشراء فهي عروض تجارة.")
            want_calc = False
        if topic.id == "zakat_fitr":
            basis += _param_basis(meta, "fitr_cash")
            fc = _clone_param(meta, "fitr_cash")
            if answers.get("fitr_mode") in ("cash", "both") and fc:
                if fc.startswith("not_allowed") or fc.startswith("value_not_permitted"):
                    notes.append(f"إخراج زكاة الفطر نقدًا: هذا المنهج لا يجيزه على الأصل ({_param_note(meta, 'fitr_cash') or ''}).".replace(" ()", ""))
                elif fc == "disputed":
                    notes.append("إخراج زكاة الفطر نقدًا مسألة خلافية في هذا المنهج؛ انظر النصوص أعلاه.")
            persons = int(answers.get("fitr_count") or 1) if answers.get("fitr_payer") == "family" else 1
            calculation = calc.fitr_quantity(persons)
            return notes, calculation, basis

        if not want_calc:
            return notes, None, basis

        # ---- arithmetic
        if market is None:
            notes.append("لم يتيسر الحصول على أسعار الذهب/الفضة الحالية للحساب"
                         + (f" ({market_error})" if market_error else "") +
                         "؛ أدخل سعر جرام الذهب والفضة بعملتك يدويًّا لإتمام الحساب.")
            return notes, None, basis

        try:
            if topic.id in ("cash", "bank", "salary", "ewallet", "crypto"):
                amount = float(answers.get("amount") or 0)
                extra = float(answers.get("other_money_amount") or 0)
                c = calc.cash_zakat(amount, market, std_for_calc, deductible, extra)
                calculation = c.to_dict()
            elif topic.id in ("gold", "jewelry"):
                grams = float(answers.get("gold_grams") or 0)
                karat = answers.get("karat")
                k = int(karat) if karat and str(karat).isdigit() else None
                if k is None:
                    notes.append("العيار غير معلوم؛ حُسب على أنه عيار 24 (خالص) وهو الحد الأعلى، فإن كان أقل فالوزن الصافي أقل.")
                c = calc.gold_zakat(grams, k, market, deductible)
                calculation = c.to_dict()
            elif topic.id == "silver":
                c = calc.silver_zakat(float(answers.get("silver_grams") or 0), market)
                calculation = c.to_dict()
            elif topic.id in ("trade_goods", "company"):
                c = calc.trade_zakat(float(answers.get("trade_value") or 0), float(answers.get("trade_cash") or 0),
                                     float(answers.get("trade_receivables") or 0), market, std_for_calc, deductible)
                calculation = c.to_dict()
            elif topic.id == "stocks":
                c = calc.cash_zakat(float(answers.get("stock_value") or 0), market, std_for_calc, deductible)
                calculation = c.to_dict()
            elif topic.id == "property_for_sale":
                c = calc.cash_zakat(float(answers.get("property_value") or 0), market, std_for_calc)
                calculation = c.to_dict()
            elif topic.id == "rented_property":
                c = calc.cash_zakat(float(answers.get("rent_saved") or 0), market, std_for_calc)
                calculation = c.to_dict()
            elif topic.id == "debt_owed_to_you":
                c = calc.cash_zakat(float(answers.get("receivable_amount") or 0), market, std_for_calc)
                calculation = c.to_dict()
            elif topic.id == "debt_on_you":
                assets = float(answers.get("liability_assets") or 0)
                liab = float(answers.get("liability_amount") or 0)
                ded = liab if debt_rule.startswith("all_debts") or (debt_rule == "due" and answers.get("liability_type") == "due_now") else 0.0
                c = calc.cash_zakat(assets, market, std_for_calc, ded)
                calculation = c.to_dict()
        except (TypeError, ValueError) as e:
            notes.append(f"تعذّر الحساب لنقص أو خطأ في الأرقام المدخلة ({e}).")
            calculation = None

        if calculation:
            m = calculation.get("market") or {}
            g, s = m.get("gold") or {}, m.get("silver") or {}
            notes.append(
                "بيانات الأسعار المستخدمة: "
                f"الذهب {calc._fmt(m.get('gold_per_gram', 0))} {m.get('currency')}/جم، الفضة {calc._fmt(m.get('silver_per_gram', 0))} {m.get('currency')}/جم؛ "
                f"المصدر: {g.get('source', 'غير معروف')}؛ تاريخ البيانات: {g.get('as_of') or s.get('as_of') or 'غير معروف'}؛ وقت الجلب: {g.get('fetched_at', '')}."
            )
            if m.get("fx"):
                notes.append(f"سعر الصرف: 1 USD = {m['fx']['usd_to_currency']:.4f} {m.get('currency')} — المصدر {m['fx']['source']} ({m['fx']['as_of']}).")
        return notes, calculation, basis

    def _conclusion(self, res: PipelineResult, primary: Record, label: str) -> str:
        parts = [f"هذا ما وجدته في مصادر {label} المتحقق منها ({len(res.hits)} سجلًا ذا صلة)؛ الحكم أعلاه منقول من المصدر، والتطبيق والحساب من النظام."]
        if res.calculation and res.calculation.get("due") is not None:
            if res.calculation["due"]:
                parts.append(f"النتيجة الحسابية: {calc._fmt(res.calculation['zakat_amount'])} {res.calculation.get('currency', '')} — بشرط تحقق الملك التام وتمام الحول وفق ما أجبت.")
            else:
                parts.append("النتيجة الحسابية: المال دون النصاب بحسب الأسعار المستخدمة، فلا زكاة فيه الآن.")
        if primary.verification_status != "verified":
            parts.append("درجة التحقق من المصدر الأساسي ليست كاملة؛ يُستحسن مراجعة المصدر الأصلي.")
        return " ".join(parts)


# a pseudo-slot for currency (not in questioning.SLOTS because it is pipeline-level)
CURRENCY_SLOT = Slot(
    "currency",
    "ما العملة التي تُقاس بها المبالغ؟ (مثل USD, SAR, EGP, EUR, KWD)",
    "لازمة لتحويل نصاب الذهب/الفضة إلى عملتك بسعر السوق الحالي.",
    "text",
    ("cash",),
    unit_ar="رمز العملة",
    required_for="calculation",
)

_CURRENCY_CODE = re.compile(r"^[A-Za-z]{3}$")


def normalize_currency(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if _CURRENCY_CODE.match(s):
        return s.upper()
    code, _ = detect_currency(s)
    return code
