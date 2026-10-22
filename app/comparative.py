"""Zakat Comparative Scholar.

Runs the same question against each clone's *own* dataset (no source mixing), then
lays the findings side by side: opinion + source per clone, points of agreement and
difference, and — only when the sources themselves state it — the reason for the
difference. It never ranks opinions or names a "correct" one; explicit tarjih is
attributed to the scholar who made it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .kb import KnowledgeBase, Record
from .pipeline import CLONE_LABEL, MIN_SCORE, NOT_FOUND_AR, make_citation
from .retrieval import Retriever
from .topics import TOPIC_BY_ID, Topic, detect_topics

CLONE_ORDER = ["albani", "ibn_uthaymeen", "ibn_baz", "maliki"]

# calc_params keys relevant to a topic, with an Arabic label for the parameter and
# human labels for each value that appears in the datasets' clone.json files.
PARAM_FOR_TOPIC: dict[str, list[str]] = {
    "cash": ["nisab_standard", "debt_deduction"],
    "bank": ["nisab_standard", "debt_deduction"],
    "salary": ["nisab_standard", "debt_deduction"],
    "ewallet": ["nisab_standard", "debt_deduction"],
    "nisab": ["nisab_standard"],
    "debt_on_you": ["debt_deduction"],
    "gold": ["jewelry_worn"],
    "jewelry": ["jewelry_worn"],
    "trade_goods": ["trade_goods", "trade_valuation"],
    "company": ["trade_goods", "trade_valuation"],
    "zakat_fitr": ["fitr_cash"],
    "conditions": ["child_wealth"],
}
PARAM_LABEL = {
    "nisab_standard": "معيار نصاب النقود",
    "debt_deduction": "أثر الدين الذي على المزكي",
    "jewelry_worn": "زكاة الحلي الملبوس",
    "fitr_cash": "إخراج زكاة الفطر نقدًا",
    "trade_goods": "زكاة عروض التجارة",
    "trade_valuation": "تقويم عروض التجارة",
    "child_wealth": "زكاة مال الصغير",
}
VALUE_LABEL = {
    "gold": "بنصاب الذهب (20 دينارًا)",
    "silver": "بنصاب الفضة (200 درهم)",
    "lower": "بالأقل من النصابين (الأحظّ للفقراء)",
    "silver_or_gold_combined": "الذهب أو الفضة مع ضمّ أحدهما إلى الآخر",
    "none": "الدين لا يمنع الزكاة ولا يُخصم",
    "due": "يُخصم الدين الحالّ فقط",
    "all_debts_reduce_cash_zakat": "الدين يُسقط زكاة العين والعروض بقدره",
    "unspecified": "لم يوجد نص صريح متحقق منه",
    "obligatory": "تجب فيه الزكاة",
    "not_obligatory": "لا تجب فيه الزكاة",
    "not_obligatory_if_permissible_use": "لا زكاة في المباح المستعمل (وتجب في المدخر/المحرم/للتجارة)",
    "disputed": "مسألة خلافية",
    "not_allowed": "لا يجوز إخراجها نقدًا",
    "value_not_permitted_general_rule": "دفع القيمة لا يجزئ على قاعدة المذهب",
    "allowed": "يجوز إخراجها نقدًا",
    "market": "تُقوَّم بسعر السوق عند الحول",
    "no_fixed_zakat": "لا زكاة مقدَّرة في العروض نفسها",
    "fixed": "تُزكّى بقيمتها 2.5%",
    "mudir_values_yearly_muhtakir_on_sale": "المدير يقوّم كل عام، والمحتكر يزكي الثمن عند البيع لعام",
}
# Values that are considered the same position for agreement purposes.
EQUIV = {
    "not_allowed": "not_allowed", "value_not_permitted_general_rule": "not_allowed",
    "obligatory": "obligatory", "not_obligatory": "not_obligatory",
    "not_obligatory_if_permissible_use": "not_obligatory",
    "none": "none", "due": "due", "all_debts_reduce_cash_zakat": "all",
    "gold": "gold", "silver": "silver", "lower": "lower", "silver_or_gold_combined": "combined",
    "market": "market", "fixed": "fixed", "no_fixed_zakat": "no_fixed", "mudir_values_yearly_muhtakir_on_sale": "mudir",
}


@dataclass
class CloneFinding:
    clone: str
    label: str
    found: bool
    ruling: str | None = None
    statement: str | None = None
    record_id: str | None = None
    is_mashhur: bool | None = None
    other_opinions: list[str] = field(default_factory=list)
    reason_for_difference: str | None = None
    verification_status: str | None = None
    citation: dict | None = None
    related: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ParamComparison:
    param: str
    label: str
    positions: dict[str, dict]  # clone -> {value, label, basis_records, note}
    agreement: bool | None

    def to_dict(self) -> dict:
        return {"param": self.param, "label": self.label, "positions": self.positions, "agreement": self.agreement}


class Comparative:
    def __init__(self, kb: KnowledgeBase, retriever: Retriever):
        self.kb = kb
        self.retriever = retriever

    def compare(self, message: str, topic_id: str | None = None, k: int = 3) -> dict:
        topic: Topic | None = TOPIC_BY_ID.get(topic_id) if topic_id else None
        if topic is None:
            det = detect_topics(message)
            topic = det[0][0] if det else None
        query = message if topic is None else f"{message} {topic.label_ar}"

        findings: list[CloneFinding] = []
        for clone in CLONE_ORDER:
            if clone not in self.kb.by_clone:
                continue
            hits = [h for h in self.retriever.search(clone, query, topic.id if topic else None, k=k)
                    if h.score >= MIN_SCORE and h.record.verification_status != "unverified"]
            label = CLONE_LABEL.get(clone, clone)
            if not hits:
                findings.append(CloneFinding(clone, label, False, ruling=NOT_FOUND_AR))
                continue
            top: Record = hits[0].record
            findings.append(CloneFinding(
                clone, label, True,
                ruling=top.ruling,
                statement=top.scholar_statement,
                record_id=top.id,
                is_mashhur=top.is_mashhur,
                other_opinions=list(top.other_opinions),
                reason_for_difference=top.reason_for_difference,
                verification_status=top.verification_status,
                citation=make_citation(top, top.scholar).to_dict(),
                related=[{"record_id": h.record.id, "subtopic": h.record.subtopic, "ruling": h.record.ruling} for h in hits[1:]],
            ))

        params = self._compare_params(topic)
        agreement, difference = self._agreement_text(params, findings)
        return {
            "topic": {"id": topic.id, "label_ar": topic.label_ar} if topic else None,
            "findings": [f.to_dict() for f in findings],
            "parameters": [p.to_dict() for p in params],
            "agreement_points": agreement,
            "difference_points": difference,
            "reasons": self._reasons(findings),
            "disclaimer_ar": (
                "المقارن لا يختار رأيًا «أصح» من عنده؛ يعرض ما وجده في مصادر كل منهج على حدة مع مصدره. "
                "الترجيحات إن وُجدت منسوبة إلى قائلها في نص السجل نفسه. "
                "الأحكام المتعلقة بالنقود الورقية وأشباهها عند المالكية تطبيق معاصر مُصرَّح به في السجل."
            ),
        }

    def _compare_params(self, topic: Topic | None) -> list[ParamComparison]:
        if topic is None:
            return []
        out: list[ParamComparison] = []
        for name in PARAM_FOR_TOPIC.get(topic.id, []):
            positions: dict[str, dict] = {}
            for clone in CLONE_ORDER:
                meta = self.kb.meta.get(clone)
                if meta is None:
                    continue
                p = {
                    "nisab_standard": meta.calc_params.nisab_standard,
                    "debt_deduction": meta.calc_params.debt_deduction,
                    "jewelry_worn": meta.calc_params.jewelry_worn,
                    "fitr_cash": meta.calc_params.fitr_cash,
                    "trade_valuation": meta.calc_params.trade_valuation,
                    "trade_goods": meta.calc_params.trade_goods,
                    "child_wealth": meta.calc_params.child_wealth,
                }[name]
                if p is None:
                    positions[clone] = {"value": None, "label": "لم يُسجَّل موقف متحقق منه في هذه القاعدة", "basis_records": [], "note": None}
                else:
                    positions[clone] = {"value": p.value, "label": VALUE_LABEL.get(p.value, p.value),
                                        "basis_records": list(p.basis_records), "note": p.note_ar}
            known = {EQUIV.get(v["value"], v["value"]) for v in positions.values() if v["value"] not in (None, "unspecified", "disputed")}
            agreement = None if len(known) < 2 else len(known) == 1
            out.append(ParamComparison(name, PARAM_LABEL[name], positions, agreement))
        return out

    def _agreement_text(self, params: list[ParamComparison], findings: list[CloneFinding]) -> tuple[list[str], list[str]]:
        agree: list[str] = []
        differ: list[str] = []
        for p in params:
            named = {c: v for c, v in p.positions.items() if v["value"] not in (None, "unspecified")}
            if not named:
                continue
            if p.agreement:
                names = "، ".join(CLONE_LABEL[c] for c in named)
                agree.append(f"{p.label}: اتفقت مصادر {names} على: {next(iter(named.values()))['label']}.")
                continue
            parts = [f"{CLONE_LABEL[c]}: {v['label']}" for c, v in named.items()]
            differ.append(f"{p.label}: " + " | ".join(parts))
            groups: dict[str, list[str]] = {}
            for c, v in named.items():
                if v["value"] != "disputed":
                    groups.setdefault(EQUIV.get(v["value"], v["value"]), []).append(c)
            for clones in groups.values():
                if len(clones) >= 2:  # partial agreement inside a disputed parameter
                    others = [CLONE_LABEL[c] for c in named if c not in clones]
                    agree.append(
                        f"{p.label}: اتفقت مصادر {'، '.join(CLONE_LABEL[c] for c in clones)} على: {named[clones[0]]['label']}"
                        + (f"، بخلاف {'، '.join(others)}." if others else ".")
                    )
        if params and not agree:
            agree.append("لا توجد نقطة اتفاق بين موقفين مسجلين أو أكثر في المعايير المقارنة لهذه المسألة.")
        missing = [f.label for f in findings if not f.found]
        if missing:
            differ.append("لم يوجد نص متحقق منه في مصادر: " + "، ".join(missing) + ".")
        return agree, differ

    def _reasons(self, findings: list[CloneFinding]) -> list[str]:
        out: list[str] = []
        for f in findings:
            if f.reason_for_difference:
                out.append(f"{f.label}: {f.reason_for_difference}")
        if not out:
            out.append("لم يُنصّ في السجلات المسترجعة على سبب الخلاف؛ لا يُخترع سبب من النظام.")
        return out
