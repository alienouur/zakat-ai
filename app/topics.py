"""Zakat topic taxonomy: subtopic ids, Arabic labels, detection keywords and tags."""
from __future__ import annotations

from dataclasses import dataclass, field

from .normalize import normalize


@dataclass(frozen=True)
class Topic:
    id: str
    label_ar: str
    group: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...] = field(default_factory=tuple)
    calculable: bool = False


TOPICS: list[Topic] = [
    # --- basics ---
    Topic("definition", "معنى الزكاة وحكمها", "basics", ("معنى الزكاة", "تعريف الزكاة", "حكم الزكاة", "ما هي الزكاة", "فرض الزكاة", "اركان"), ("zakat", "basics")),
    Topic("conditions", "شروط وجوب الزكاة", "basics", ("شروط الزكاة", "شروط وجوب", "على من تجب", "الصغير", "المجنون", "اليتيم", "مال اليتيم"), ("zakat", "conditions")),
    Topic("nisab", "النصاب", "basics", ("النصاب", "نصاب", "كم النصاب", "بلغ النصاب"), ("zakat", "nisab")),
    Topic("hawl", "الحول", "basics", ("الحول", "حول", "سنة كاملة", "مضى عليه سنة", "حال عليه"), ("zakat", "hawl")),
    Topic("mustafad", "المال المستفاد أثناء الحول", "basics", ("المال المستفاد", "مال مستفاد", "اضيف الى المال", "زاد المال", "دخل جديد"), ("zakat", "hawl", "mustafad")),
    Topic("advance", "تعجيل الزكاة", "basics", ("تعجيل", "اعجل", "قبل الحول", "اخراج الزكاة مقدما", "تقديم الزكاة"), ("zakat", "advance")),
    Topic("delay", "تأخير الزكاة", "basics", ("تاخير الزكاة", "تاخرت", "لم اخرج الزكاة", "سنوات ماضية", "زكاة سنوات", "نسيت الزكاة"), ("zakat", "delay")),
    # --- cash ---
    Topic("cash", "زكاة النقود والمدخرات", "cash", ("نقود", "نقد", "مدخرات", "مدخر", "فلوس", "مبلغ", "دولار", "ريال", "دينار", "درهم", "جنيه", "يورو", "اوراق نقدية", "عملة", "عملات", "كاش"), ("zakat", "cash", "nisab", "hawl"), True),
    Topic("bank", "الحساب البنكي", "cash", ("حساب بنكي", "البنك", "بنك", "حساب جاري", "حساب توفير", "وديعة", "مصرف"), ("zakat", "cash", "bank"), True),
    Topic("salary", "زكاة الراتب والدخل", "cash", ("راتب", "رواتب", "مرتب", "دخل شهري", "الدخل", "اجرة عملي", "معاش"), ("zakat", "cash", "salary", "hawl"), True),
    # --- gold & silver ---
    Topic("gold", "زكاة الذهب", "gold_silver", ("ذهب", "سبائك", "جرام ذهب", "غرام ذهب", "عيار"), ("zakat", "gold", "nisab"), True),
    Topic("silver", "زكاة الفضة", "gold_silver", ("فضة", "فضه", "جرام فضة"), ("zakat", "silver", "nisab"), True),
    Topic("jewelry", "زكاة الحلي (الذهب المستعمل)", "gold_silver", ("حلي", "حلى", "مجوهرات", "اساور", "سوار", "خاتم", "قلادة", "ذهب المراة", "ذهب زوجتي", "ذهب امي", "ذهب اللبس", "تلبسه", "للزينة", "زينة"), ("zakat", "gold", "jewelry"), True),
    # --- trade ---
    Topic("trade_goods", "زكاة عروض التجارة", "trade", ("عروض التجارة", "تجارة", "بضاعة", "بضائع", "مخزون", "محل", "متجر", "سلع", "دكان", "بيع وشراء", "تاجر", "اونلاين", "الكتروني", "انترنت", "متجر الكتروني", "تكلفة البضاعة", "سعر الجملة"), ("zakat", "trade"), True),
    Topic("company", "زكاة الشركات", "trade", ("شركة", "شركات", "شركتي", "شريك", "شركاء", "راس مال الشركة"), ("zakat", "trade", "company"), True),
    # --- investments ---
    Topic("stocks", "زكاة الأسهم", "investments", ("اسهم", "سهم", "بورصة", "سوق المال", "تداول", "محفظة استثمارية", "صناديق", "صندوق استثمار", "استثمار", "مضاربة", "ارباح الاسهم", "توزيعات"), ("zakat", "stocks", "investments"), True),
    # --- real estate ---
    Topic("residence", "منزل السكن", "real_estate", ("بيتي", "منزل السكن", "منزلي", "شقتي", "بيت اسكنه", "سكن", "البيت الذي اسكنه", "سيارتي", "سيارة"), ("zakat", "real_estate", "residence")),
    Topic("rented_property", "العقار المؤجر", "real_estate", ("عقار مؤجر", "مؤجر", "اجار", "ايجار", "شقة مؤجرة", "محلات مؤجرة", "الاجرة", "مستغلات"), ("zakat", "real_estate", "rent"), True),
    Topic("property_for_sale", "العقار والأرض المعدة للبيع", "real_estate", ("ارض", "اراضي", "قطعة ارض", "عقار للبيع", "عقار", "عقارات", "استثمار عقاري", "اشتريت ارض", "ارض للبيع", "بناء للبيع"), ("zakat", "real_estate", "trade"), True),
    # --- debts ---
    Topic("debt_owed_to_you", "الدين الذي لك على غيرك", "debts", ("دين لي", "لي دين", "اقرضت", "سلفت", "دين على شخص", "دين عند", "مال عند الناس", "مطلوب لي", "لي مال عند", "دين مرجو", "دين غير مرجو", "معسر", "مليء", "ديون لي"), ("zakat", "debts", "receivable"), True),
    Topic("debt_on_you", "الدين الذي عليك", "debts", ("علي دين", "عليّ دين", "دين علي", "مدين", "قرض علي", "استدنت", "اقساط", "قسط", "قرض", "قروض", "ديون علي", "التزامات", "دين عقاري", "تمويل"), ("zakat", "debts", "liability"), True),
    # --- modern assets ---
    Topic("crypto", "العملات الرقمية والأصول الرقمية", "modern", ("عملات رقمية", "عملة رقمية", "بيتكوين", "بتكوين", "كريبتو", "crypto", "bitcoin", "usdt", "ايثيريوم", "اصول رقمية", "nft"), ("zakat", "crypto", "modern"), True),
    Topic("ewallet", "المحافظ الإلكترونية ومنصات الاستثمار", "modern", ("محفظة الكترونية", "محافظ الكترونية", "paypal", "بايبال", "stc pay", "منصة استثمار", "منصات", "رصيد الكتروني"), ("zakat", "cash", "modern"), True),
    # --- crops & livestock ---
    Topic("crops", "زكاة الزروع والثمار", "crops", ("زروع", "زرع", "ثمار", "تمر", "نخل", "قمح", "شعير", "حبوب", "محصول", "مزرعة", "زيتون", "خضار", "فواكه", "سقي", "بعل", "اوسق", "وسق"), ("zakat", "crops")),
    Topic("livestock", "زكاة الأنعام (الإبل والبقر والغنم)", "livestock", ("ابل", "ناقة", "جمل", "بقر", "بقرة", "غنم", "شياه", "شاة", "خراف", "ماعز", "معز", "انعام", "مواشي", "سائمة", "اغنام"), ("zakat", "livestock")),
    # --- zakat al-fitr ---
    Topic("zakat_fitr", "زكاة الفطر", "zakat_fitr", ("زكاة الفطر", "صدقة الفطر", "فطرة", "الفطرة", "زكاة رمضان", "صاع", "العيد", "زكاة الفطر نقدا"), ("zakat_al_fitr",), True),
    # --- recipients ---
    Topic("recipients", "مصارف الزكاة", "recipients", ("مصارف", "لمن تعطى", "من يستحق", "الفقراء", "المساكين", "العاملين عليها", "المؤلفة قلوبهم", "الرقاب", "الغارمين", "غارم", "في سبيل الله", "ابن السبيل", "اعطاء الزكاة", "دفع الزكاة ل", "الزكاة للاقارب", "الزكاة للوالدين", "بناء مسجد", "طالب علم", "مستشفى", "الجمعيات الخيرية", "تعطى الزكاة", "المستحق"), ("zakat", "recipients")),
]

TOPIC_BY_ID = {t.id: t for t in TOPICS}
GROUP_LABELS = {
    "basics": "أساسيات الزكاة", "cash": "النقود والمدخرات", "gold_silver": "الذهب والفضة",
    "trade": "التجارة", "investments": "الأسهم والاستثمارات", "real_estate": "العقارات",
    "debts": "الديون", "modern": "الأصول الحديثة", "crops": "الزروع والثمار",
    "livestock": "الأنعام", "zakat_fitr": "زكاة الفطر", "recipients": "مصارف الزكاة",
}


def detect_topics(text: str, limit: int = 3) -> list[tuple[Topic, float]]:
    """Keyword-based topic detection; longer keyword matches weigh more."""
    n = normalize(text)
    padded = f" {n} "
    scored: list[tuple[Topic, float]] = []
    for t in TOPICS:
        score = 0.0
        for kw in t.keywords:
            k = normalize(kw)
            if not k:
                continue
            if f" {k} " in padded:
                score += 2.0 + 0.1 * len(k.split())
            elif k in n:
                score += 1.0 + 0.1 * len(k.split())
        if score:
            scored.append((t, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]
