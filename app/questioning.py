"""Smart questioning: detect the information needed for a ruling and ask only for what is missing.

Every ``Slot`` declares which topics it matters for, *why* it matters (shown to the
user), and an optional dependency on another slot so questions are asked only when they
can affect the ruling or the calculation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .normalize import extract_numbers, normalize

Answers = dict[str, Any]


@dataclass(frozen=True)
class Option:
    value: str
    label_ar: str
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Slot:
    id: str
    question_ar: str
    why_ar: str
    kind: str  # "choice" | "number" | "text"
    topics: tuple[str, ...]
    options: tuple[Option, ...] = ()
    unit_ar: str | None = None
    required_for: str = "ruling"  # "ruling" | "calculation"
    depends_on: Callable[[Answers], bool] | None = field(default=None, compare=False)
    extract: Callable[[str], Any] | None = field(default=None, compare=False)


def _has(*words: str) -> Callable[[str], Any]:
    def f(text: str):
        n = normalize(text)
        return any(normalize(w) in n for w in words) or None
    return f


def _choice_extractor(options: tuple[Option, ...]) -> Callable[[str], Any]:
    def f(text: str):
        n = normalize(text)
        for opt in options:
            for p in opt.patterns:
                if normalize(p) in n:
                    return opt.value
        return None
    return f


def _first_number(text: str):
    nums = extract_numbers(text)
    return nums[0] if nums else None


YES_NO = (Option("yes", "نعم", ("نعم", "ايوه", "اجل", "بالتاكيد")), Option("no", "لا", ("لا ", "كلا", "ليس")))

OWNERSHIP = (
    Option("full", "نعم، مملوك لي بالكامل", ("مملوك لي", "ملكي", "مالي الخاص", "بالكامل")),
    Option("shared", "مشترك مع غيري", ("مشترك", "شراكة", "مع اخي", "مع زوجتي")),
    Option("not_mine", "ليس ملكي (أمانة/وديعة/مال غيري)", ("امانة", "وديعة", "ليس ملكي", "مال غيري")),
)

HAWL = (
    Option("yes", "نعم، مضى عليه عام هجري كامل وهو فوق النصاب", ("حال عليه الحول", "مضى عليه سنة", "سنة كاملة", "اكثر من سنة", "من سنتين", "منذ سنة")),
    Option("no", "لا، لم يمضِ عليه عام", ("لم يحل", "اقل من سنة", "شهر", "اشهر", "حديث")),
    Option("unknown", "لا أعرف بالضبط", ("لا اعرف", "لا ادري", "غير متاكد")),
)

PURPOSE_MONEY = (
    Option("savings", "مدخر / في الحساب", ("مدخر", "ادخار", "توفير", "في الحساب", "في البنك")),
    Option("trade", "رأس مال تجارة", ("تجارة", "بضاعة", "راس مال")),
    Option("spending", "للنفقة الشهرية ولا يبقى منه شيء", ("للنفقة", "اصرفه", "ينفد", "لا يبقى")),
)

DEBT_STATUS = (
    Option("none", "لا توجد ديون عليّ", ("لا يوجد دين", "ليس علي دين", "لا ديون", "بدون ديون")),
    Option("due_now", "عليّ دين حالّ (مطلوب مني الآن)", ("دين حال", "مطلوب مني", "علي دين")),
    Option("installments", "عليّ أقساط مؤجلة (قرض/تمويل)", ("اقساط", "قسط", "تمويل", "قرض")),
)

JEWELRY_USE = (
    Option("worn", "للّبس والزينة", ("للبس", "البس", "تلبسه", "زينة", "للزينة", "استعمال")),
    Option("stored", "مدخر / كنز ولا يُلبس", ("مدخر", "كنز", "لا تلبسه", "محفوظ", "ادخار")),
    Option("trade", "معدّ للبيع / تجارة", ("للبيع", "تجارة", "للتجارة")),
)

KARAT = (
    Option("24", "عيار 24", ("عيار 24", "24",)),
    Option("22", "عيار 22", ("عيار 22", "22")),
    Option("21", "عيار 21", ("عيار 21", "21")),
    Option("18", "عيار 18", ("عيار 18", "18")),
    Option("unknown", "لا أعرف / مختلط", ("لا اعرف", "مختلط")),
)

STOCK_INTENT = (
    Option("trading", "للمضاربة (بيع وشراء بقصد الربح من فرق السعر)", ("مضاربة", "تداول", "بيع وشراء", "يومي")),
    Option("long_term", "استثمار طويل الأجل بقصد الأرباح/التوزيعات", ("طويل الاجل", "استثمار", "توزيعات", "ارباح سنوية", "احتفظ")),
)

COMPANY_TYPE = (
    Option("trading_co", "شركة تجارية/صناعية تملك سلعًا ونقودًا", ("تجارية", "صناعية", "بضائع")),
    Option("services", "شركة خدمية/عقارية (أصولها ثابتة غالبًا)", ("خدمية", "عقارية", "خدمات")),
    Option("unknown", "لا أعرف طبيعة أصولها", ("لا اعرف",)),
    Option("company_pays", "الشركة نفسها تخرج زكاة الأسهم", ("الشركة تزكي", "تخرج الشركة")),
)

DEBTOR_STATUS = (
    Option("solvent", "المدين مليء (قادر) والدين مرجوّ السداد", ("مليء", "قادر", "مرجو", "يسدد", "موسر")),
    Option("insolvent", "المدين معسر أو مماطل أو الدين غير مرجوّ", ("معسر", "مماطل", "غير مرجو", "لا يسدد", "منكر", "ضائع")),
)

PROPERTY_INTENT = (
    Option("sale", "اشتريته للبيع والتربّح", ("للبيع", "تجارة", "اربح", "ابيعه")),
    Option("rent", "للتأجير والاستغلال", ("تاجير", "ايجار", "مؤجر", "اجار")),
    Option("hold", "اقتناء/ادخار بلا نية محددة أو للسكن مستقبلًا", ("ادخار", "بلا نية", "للسكن", "احتفظ", "لم اقرر")),
)

FITR_PAYER = (
    Option("self", "عن نفسي فقط", ("عن نفسي",)),
    Option("family", "عن نفسي ومن أعولهم", ("اعول", "اسرتي", "عائلتي", "اولادي", "زوجتي")),
)

SLOTS: list[Slot] = [
    # --- shared money-type slots ---
    Slot("amount", "ما مقدار المال (بالعملة التي تذكرها)؟", "لا يمكن معرفة بلوغ النصاب ولا الحساب بدون المقدار.", "number",
         ("cash", "bank", "salary", "ewallet", "crypto"), unit_ar="مبلغ", required_for="calculation", extract=_first_number),
    Slot("ownership", "هل هذا المال مملوك لك بالكامل؟", "الزكاة إنما تجب في الملك التام؛ المال المشترك يُزكّى بحسب حصتك، ومال الغير لا يُزكّيه غير مالكه.", "choice",
         ("cash", "bank", "salary", "gold", "silver", "jewelry", "trade_goods", "stocks", "crypto", "ewallet", "property_for_sale"), OWNERSHIP, extract=_choice_extractor(OWNERSHIP)),
    Slot("hawl", "منذ متى بلغ هذا المال النصاب؟ هل مضى عليه عام هجري كامل؟", "الحول شرط في زكاة النقود والذهب وعروض التجارة عند عامة الفقهاء؛ وكيفية اعتباره للراتب مسألة فيها تفصيل.", "choice",
         ("cash", "bank", "salary", "gold", "silver", "jewelry", "trade_goods", "stocks", "crypto", "ewallet", "property_for_sale", "debt_owed_to_you"), HAWL, extract=_choice_extractor(HAWL)),
    Slot("other_money", "هل لديك أموال أخرى من نفس الجنس (نقود/حسابات/رواتب مدخرة) تُضاف إلى هذا المبلغ؟", "الأموال من جنس واحد تُضمّ إلى بعضها في تكميل النصاب وفي الحساب.", "choice",
         ("cash", "bank", "salary", "ewallet"), (Option("no", "لا", ("لا",)), Option("yes", "نعم (أذكر مجموعها)", ("نعم",))), extract=None),
    Slot("purpose", "هل المال مدخر أم متعلق بتجارة أم يُصرف في النفقة ولا يبقى؟", "المال المدخر تجري عليه أحكام النقد، ورأس مال التجارة يُضمّ إلى عروضها، وما يُنفق قبل الحول لا زكاة فيه.", "choice",
         ("cash", "bank", "salary", "ewallet"), PURPOSE_MONEY, extract=_choice_extractor(PURPOSE_MONEY)),
    Slot("debts", "هل عليك ديون أو التزامات؟ وما نوعها؟", "أثر الدين في منع الزكاة مسألة خلافية بين المناهج؛ لذا يهم معرفة وجود الدين ونوعه.", "choice",
         ("cash", "bank", "salary", "gold", "silver", "trade_goods", "stocks", "ewallet", "crypto"), DEBT_STATUS, extract=_choice_extractor(DEBT_STATUS)),
    Slot("debt_amount", "ما مقدار الدين الحالّ الذي عليك؟", "يلزم في المناهج التي تُسقط الدين من الوعاء الزكوي.", "number",
         ("cash", "bank", "salary", "gold", "silver", "trade_goods", "stocks", "ewallet", "crypto"), unit_ar="مبلغ", required_for="calculation",
         depends_on=lambda a: a.get("debts") in ("due_now", "installments")),
    # --- gold / silver / jewelry ---
    Slot("gold_grams", "ما وزن الذهب بالجرام؟", "نصاب الذهب بالوزن (85 جرامًا تقريبًا من الذهب الخالص) ولا يُعرف بدون الوزن.", "number",
         ("gold", "jewelry"), unit_ar="جرام", required_for="calculation", extract=_first_number),
    Slot("karat", "ما عيار الذهب؟", "النصاب يُحسب بالذهب الخالص، فالعيار الأقل يُنسب إلى 24 للوصول إلى الوزن الصافي.", "choice",
         ("gold", "jewelry"), KARAT, required_for="calculation", extract=_choice_extractor(KARAT)),
    Slot("silver_grams", "ما وزن الفضة بالجرام؟", "نصاب الفضة بالوزن (595 جرامًا تقريبًا).", "number",
         ("silver",), unit_ar="جرام", required_for="calculation", extract=_first_number),
    Slot("jewelry_use", "هل الذهب للّبس والزينة، أم مدخر لا يُلبس، أم معدّ للبيع؟", "الحلي المستعمل هو محل الخلاف الفقهي؛ أما المدخر والمعدّ للتجارة فتجب زكاته باتفاق.", "choice",
         ("jewelry", "gold"), JEWELRY_USE, extract=_choice_extractor(JEWELRY_USE)),
    # --- trade ---
    Slot("trade_value", "ما قيمة البضاعة/المخزون بسعر البيع الحالي في السوق عند تمام الحول (لا بسعر التكلفة)؟", "عروض التجارة تُقوَّم عند الحول بقيمتها السوقية الحالية عند جمهور المعاصرين، وفي كيفية التقويم تفصيل عند المالكية.", "number",
         ("trade_goods", "company"), unit_ar="مبلغ", required_for="calculation", extract=_first_number),
    Slot("trade_cash", "ما مقدار النقد المتعلق بالتجارة (صندوق، حساب المحل) عند الحول؟", "يُضمّ نقد التجارة إلى قيمة البضاعة.", "number",
         ("trade_goods", "company"), unit_ar="مبلغ", required_for="calculation"),
    Slot("trade_receivables", "هل لك ديون على العملاء (مبيعات بالأجل) مرجوّة السداد؟ وما مقدارها؟", "الديون المرجوّة تُضمّ إلى الوعاء الزكوي عند من يقول بزكاتها.", "number",
         ("trade_goods", "company"), unit_ar="مبلغ", required_for="calculation"),
    # --- stocks ---
    Slot("stock_intent", "هل تشتري الأسهم للمضاربة (البيع والشراء) أم للاستثمار طويل الأجل؟", "نيّة التجارة تجعل الأسهم عروض تجارة تُزكّى بقيمتها كلها، أما الاستثمار طويل الأجل فالتفصيل فيه بحسب أصول الشركة.", "choice",
         ("stocks",), STOCK_INTENT, extract=_choice_extractor(STOCK_INTENT)),
    Slot("company_type", "ما طبيعة أصول الشركة المساهم فيها؟ وهل تُخرج الشركة الزكاة عن المساهمين؟", "زكاة السهم الاستثماري تتبع ما يقابله من أصول زكوية، وإخراج الشركة للزكاة يمنع التكرار.", "choice",
         ("stocks",), COMPANY_TYPE, depends_on=lambda a: a.get("stock_intent") == "long_term", extract=_choice_extractor(COMPANY_TYPE)),
    Slot("stock_value", "ما القيمة السوقية الحالية للأسهم؟", "لازمة لحساب زكاة أسهم المضاربة.", "number",
         ("stocks",), unit_ar="مبلغ", required_for="calculation", extract=_first_number),
    # --- real estate ---
    Slot("property_intent", "ما نيّتك في هذا العقار/الأرض عند تملّكه؟", "زكاة العقار تتبع النيّة: المعدّ للبيع عروض تجارة، والمؤجَّر تُزكّى غلته، والمقتنى لا زكاة فيه.", "choice",
         ("property_for_sale", "rented_property"), PROPERTY_INTENT, extract=_choice_extractor(PROPERTY_INTENT)),
    Slot("property_value", "ما القيمة السوقية الحالية للعقار المعدّ للبيع؟", "تُزكّى قيمته السوقية عند الحول إن كان للتجارة.", "number",
         ("property_for_sale",), unit_ar="مبلغ", required_for="calculation", depends_on=lambda a: a.get("property_intent") == "sale", extract=_first_number),
    Slot("rent_saved", "ما المتبقّي من الأجرة المدخرة التي حال عليها الحول؟", "الزكاة في غلة العقار المؤجر تكون في الأجرة المدخرة التي بلغت النصاب وحال عليها الحول، لا في قيمة العقار.", "number",
         ("rented_property",), unit_ar="مبلغ", required_for="calculation", depends_on=lambda a: a.get("property_intent") in (None, "rent")),
    # --- receivables ---
    Slot("debtor_status", "هل المدين مليء (قادر على السداد) والدين مرجوّ، أم معسر/مماطل؟", "الفقهاء يفرّقون بين الدين المرجوّ وغير المرجوّ في وجوب الزكاة ووقت إخراجها.", "choice",
         ("debt_owed_to_you",), DEBTOR_STATUS, extract=_choice_extractor(DEBTOR_STATUS)),
    Slot("receivable_amount", "ما مقدار الدين الذي لك؟", "لازم للحساب.", "number", ("debt_owed_to_you",), unit_ar="مبلغ", required_for="calculation", extract=_first_number),
    # --- debt on you ---
    Slot("liability_type", "هل الدين الذي عليك حالّ الآن أم أقساط مؤجلة؟", "بعض المناهج لا تُسقط إلا الدين الحالّ، وبعضها لا يُسقط الدين أصلًا.", "choice",
         ("debt_on_you",), DEBT_STATUS[1:], extract=_choice_extractor(DEBT_STATUS)),
    Slot("liability_assets", "ما مقدار النقود/الأموال الزكوية التي تملكها؟", "حتى يُنظر: هل يبقى بعد الدين نصاب أم لا.", "number", ("debt_on_you",), unit_ar="مبلغ", required_for="calculation", extract=_first_number),
    Slot("liability_amount", "ما مقدار الدين الذي عليك؟", "لازم لمعرفة أثره على النصاب.", "number", ("debt_on_you",), unit_ar="مبلغ", required_for="calculation"),
    # --- crypto ---
    Slot("crypto_intent", "هل تحتفظ بالعملة الرقمية كمدخر/وسيط تبادل أم تتاجر بها؟", "تخريج زكاتها معاصر؛ فالتفريق بين التجارة والادخار يؤثر في كيفية إلحاقها.", "choice",
         ("crypto",), (Option("hold", "ادخار/احتفاظ", ("احتفظ", "ادخار", "مدخر")), Option("trade", "تداول وتجارة", ("تداول", "تجارة", "مضاربة"))), extract=None),
    # --- zakat al-fitr ---
    Slot("fitr_payer", "هل تريد إخراجها عن نفسك فقط أم عمّن تعولهم أيضًا؟", "زكاة الفطر تجب عن كل فرد، وتُخرج عن من تلزم نفقتهم.", "choice",
         ("zakat_fitr",), FITR_PAYER, extract=_choice_extractor(FITR_PAYER)),
    Slot("fitr_count", "كم عدد الأفراد الذين تُخرج عنهم (بمن فيهم أنت)؟", "المقدار صاع عن كل فرد.", "number",
         ("zakat_fitr",), unit_ar="فرد", required_for="calculation", depends_on=lambda a: a.get("fitr_payer") == "family", extract=None),
    Slot("fitr_mode", "هل تسأل عن إخراجها طعامًا أم نقدًا؟", "إخراج زكاة الفطر نقدًا مسألة خلافية بين المناهج.", "choice",
         ("zakat_fitr",), (Option("food", "طعامًا", ("طعام", "ارز", "تمر", "قمح", "صاع")), Option("cash", "نقدًا", ("نقد", "نقود", "فلوس", "مال", "قيمة")), Option("both", "أريد معرفة الحكم في الحالتين", ("الحالتين", "الاثنين"))), extract=_choice_extractor((Option("food", "", ("طعام", "ارز", "تمر", "قمح")), Option("cash", "", ("نقد", "نقود", "فلوس", "قيمه", "بالمال"))))),
    # --- recipients ---
    Slot("recipient_kind", "لمن تريد إعطاء الزكاة؟", "الأصناف الثمانية محددة بنصّ القرآن، وبعض الجهات (كالأقارب وبناء المساجد) فيها تفصيل وخلاف.", "choice",
         ("recipients",), (
             Option("poor", "فقير/مسكين", ("فقير", "مسكين", "محتاج")),
             Option("relative", "قريب (والدان/أولاد/أخ/عم...)", ("قريب", "اقارب", "اخي", "اختي", "والدي", "امي", "ابي", "عمي", "خالي", "ولدي")),
             Option("spouse", "الزوج/الزوجة", ("زوجتي", "زوجي")),
             Option("debtor", "مدين عاجز عن السداد (غارم)", ("غارم", "مدين", "دين")),
             Option("student", "طالب علم", ("طالب علم", "طلاب")),
             Option("mosque", "بناء مسجد/مشروع خيري عام", ("مسجد", "مستشفى", "مشروع", "بناء")),
             Option("charity", "جمعية خيرية", ("جمعية", "جمعيات", "مؤسسة خيرية")),
             Option("nonmuslim", "غير مسلم", ("غير مسلم", "نصراني", "كافر")),
         ), extract=None),
    # --- crops / livestock ---
    Slot("irrigation", "هل الزرع يُسقى بالمطر/الأنهار بلا كلفة أم بآلة/كلفة؟", "ما سُقي بلا كلفة فيه العشر، وما سُقي بكلفة فيه نصف العشر.", "choice",
         ("crops",), (Option("free", "بلا كلفة (مطر/عيون)", ("مطر", "بعل", "عيون", "نهر", "بلا كلفة")), Option("cost", "بكلفة (مضخات/ري)", ("مضخة", "الة", "بكلفة", "ري", "مكينة")), Option("mixed", "بالاثنين", ("الاثنين", "مختلط"))), extract=None),
    Slot("crop_kind", "ما نوع المحصول؟", "الفقهاء يختلفون في أي الزروع والثمار تجب فيها الزكاة (حبوب مُقتاتة مدّخرة، أو كل ما تخرجه الأرض).", "text", ("crops",)),
    Slot("animal_kind", "ما نوع الأنعام وعددها؟ وهل هي سائمة (ترعى أكثر الحول) أم مَعلوفة؟", "النُّصُب مختلفة لكل نوع، واشتراط السَّوْم محل خلاف بين المناهج.", "text", ("livestock",)),
]

SLOT_BY_ID = {s.id: s for s in SLOTS}


def slots_for_topic(topic_id: str) -> list[Slot]:
    return [s for s in SLOTS if topic_id in s.topics]


def extract_from_text(text: str, topic_id: str, answers: Answers) -> Answers:
    """Fill slots from free text without guessing; only explicit matches are accepted."""
    out = dict(answers)
    for slot in slots_for_topic(topic_id):
        if slot.id in out or slot.extract is None:
            continue
        try:
            val = slot.extract(text)
        except Exception:
            val = None
        if val is not None:
            out[slot.id] = val
    return out


def missing_slots(topic_id: str, answers: Answers, need_calculation: bool = True) -> list[Slot]:
    result = []
    for slot in slots_for_topic(topic_id):
        if slot.depends_on and not slot.depends_on(answers):
            continue
        if slot.required_for == "calculation" and not need_calculation:
            continue
        if slot.id not in answers or answers[slot.id] in (None, ""):
            result.append(slot)
    return result


def slot_to_dict(slot: Slot) -> dict:
    return {
        "id": slot.id,
        "question": slot.question_ar,
        "why": slot.why_ar,
        "kind": slot.kind,
        "unit": slot.unit_ar,
        "required_for": slot.required_for,
        "options": [{"value": o.value, "label": o.label_ar} for o in slot.options],
    }
