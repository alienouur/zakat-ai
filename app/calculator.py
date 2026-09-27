"""Pure arithmetic for zakat. No fiqh decisions live here.

The caller (pipeline) decides *whether* zakat is due and *which* parameters apply
according to the selected clone; this module only performs and documents the math,
returning every step so the answer can show the calculation transparently.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GOLD_NISAB_GRAMS = 85.0       # 20 mithqal ≈ 85 g pure gold (widely used contemporary estimate)
SILVER_NISAB_GRAMS = 595.0    # 200 dirham ≈ 595 g silver (widely used contemporary estimate)
ZAKAT_RATE = 0.025
SAA_KG_APPROX = (2.04, 3.0)   # range of contemporary estimates for the sa' of food (kg); shown as a range, never as a single "fact"


@dataclass
class Calculation:
    due: bool | None
    zakatable_base: float
    rate: float
    zakat_amount: float
    nisab_value: float | None
    nisab_standard: str | None
    currency: str
    steps: list[str] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)
    market: dict | None = None

    def to_dict(self) -> dict:
        return {
            "due": self.due,
            "zakatable_base": round(self.zakatable_base, 2),
            "rate": self.rate,
            "zakat_amount": round(self.zakat_amount, 2),
            "nisab_value": round(self.nisab_value, 2) if self.nisab_value is not None else None,
            "nisab_standard": self.nisab_standard,
            "currency": self.currency,
            "steps": self.steps,
            "inputs": self.inputs,
            "market": self.market,
        }


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


def nisab_in_currency(market: dict, standard: str) -> tuple[float, str, list[str]]:
    """Return (nisab value, label, steps) for the given standard: gold | silver | lower."""
    cur = market["currency"]
    gold_n = GOLD_NISAB_GRAMS * market["gold_per_gram"]
    silver_n = SILVER_NISAB_GRAMS * market["silver_per_gram"]
    steps = [
        f"نصاب الذهب = {GOLD_NISAB_GRAMS:g} جم × {_fmt(market['gold_per_gram'])} {cur}/جم = {_fmt(gold_n)} {cur}",
        f"نصاب الفضة = {SILVER_NISAB_GRAMS:g} جم × {_fmt(market['silver_per_gram'])} {cur}/جم = {_fmt(silver_n)} {cur}",
    ]
    if standard == "gold":
        return gold_n, "نصاب الذهب (85 جم)", steps
    if standard == "silver":
        return silver_n, "نصاب الفضة (595 جم)", steps
    n = min(gold_n, silver_n)
    label = "الأقل من نصابَي الذهب والفضة" + (" (وهو الفضة)" if silver_n <= gold_n else " (وهو الذهب)")
    steps.append(f"المعتمد: {label} = {_fmt(n)} {cur}")
    return n, label, steps


def cash_zakat(amount: float, market: dict, nisab_standard: str, deductible_debt: float = 0.0,
               extra_assets: float = 0.0) -> Calculation:
    cur = market["currency"]
    nisab, label, steps = nisab_in_currency(market, nisab_standard)
    base = amount + extra_assets
    inputs = {"amount": amount, "extra_assets": extra_assets, "deductible_debt": deductible_debt}
    if extra_assets:
        steps.append(f"مجموع النقود = {_fmt(amount)} + {_fmt(extra_assets)} = {_fmt(base)} {cur}")
    if deductible_debt:
        steps.append(f"بعد خصم الدين المعتبر = {_fmt(base)} − {_fmt(deductible_debt)} = {_fmt(base - deductible_debt)} {cur}")
        base -= deductible_debt
    due = base >= nisab
    steps.append(f"مقارنة بالنصاب: {_fmt(base)} {'≥' if due else '<'} {_fmt(nisab)} ⇒ {'بلغ النصاب' if due else 'لم يبلغ النصاب'}")
    zakat = base * ZAKAT_RATE if due else 0.0
    if due:
        steps.append(f"الزكاة = {_fmt(base)} × 2.5% = {_fmt(zakat)} {cur}")
    return Calculation(due, max(base, 0.0), ZAKAT_RATE, zakat, nisab, label, cur, steps, inputs, market)


def gold_zakat(grams: float, karat: int | None, market: dict, deductible_debt: float = 0.0) -> Calculation:
    cur = market["currency"]
    k = karat or 24
    pure = grams * k / 24
    steps = [f"الوزن الصافي (عيار {k}) = {grams:g} × {k}/24 = {pure:.2f} جم ذهب خالص"]
    due = pure >= GOLD_NISAB_GRAMS
    steps.append(f"مقارنة بنصاب الذهب: {pure:.2f} {'≥' if due else '<'} {GOLD_NISAB_GRAMS:g} جم ⇒ {'بلغ النصاب' if due else 'لم يبلغ النصاب'}")
    value = pure * market["gold_per_gram"]
    steps.append(f"القيمة السوقية = {pure:.2f} جم × {_fmt(market['gold_per_gram'])} {cur}/جم = {_fmt(value)} {cur}")
    base = value
    if deductible_debt and due:
        base = max(value - deductible_debt, 0.0)
        steps.append(f"بعد خصم الدين المعتبر = {_fmt(base)} {cur}")
        due = base >= GOLD_NISAB_GRAMS * market["gold_per_gram"]
    zakat = base * ZAKAT_RATE if due else 0.0
    if due:
        steps.append(f"الزكاة = {_fmt(base)} × 2.5% = {_fmt(zakat)} {cur} (أو ما يعادلها ذهبًا: {pure * ZAKAT_RATE:.2f} جم)")
    return Calculation(due, base, ZAKAT_RATE, zakat, GOLD_NISAB_GRAMS * market["gold_per_gram"], "نصاب الذهب (85 جم خالص)", cur, steps,
                       {"grams": grams, "karat": k, "deductible_debt": deductible_debt}, market)


def silver_zakat(grams: float, market: dict) -> Calculation:
    cur = market["currency"]
    due = grams >= SILVER_NISAB_GRAMS
    steps = [f"مقارنة بنصاب الفضة: {grams:g} {'≥' if due else '<'} {SILVER_NISAB_GRAMS:g} جم ⇒ {'بلغ النصاب' if due else 'لم يبلغ النصاب'}"]
    value = grams * market["silver_per_gram"]
    steps.append(f"القيمة السوقية = {grams:g} × {_fmt(market['silver_per_gram'])} = {_fmt(value)} {cur}")
    zakat = value * ZAKAT_RATE if due else 0.0
    if due:
        steps.append(f"الزكاة = {_fmt(value)} × 2.5% = {_fmt(zakat)} {cur} (أو {grams * ZAKAT_RATE:.2f} جم فضة)")
    return Calculation(due, value, ZAKAT_RATE, zakat, SILVER_NISAB_GRAMS * market["silver_per_gram"], "نصاب الفضة (595 جم)", cur, steps,
                       {"grams": grams}, market)


def trade_zakat(goods_value: float, cash: float, receivables: float, market: dict, nisab_standard: str,
                deductible_debt: float = 0.0) -> Calculation:
    cur = market["currency"]
    nisab, label, steps = nisab_in_currency(market, nisab_standard)
    base = goods_value + cash + receivables
    steps.append(f"الوعاء = قيمة البضاعة {_fmt(goods_value)} + النقد {_fmt(cash)} + الديون المرجوّة {_fmt(receivables)} = {_fmt(base)} {cur}")
    if deductible_debt:
        base = max(base - deductible_debt, 0.0)
        steps.append(f"بعد خصم الدين المعتبر = {_fmt(base)} {cur}")
    due = base >= nisab
    steps.append(f"مقارنة بالنصاب: {_fmt(base)} {'≥' if due else '<'} {_fmt(nisab)} ⇒ {'بلغ النصاب' if due else 'لم يبلغ النصاب'}")
    zakat = base * ZAKAT_RATE if due else 0.0
    if due:
        steps.append(f"الزكاة = {_fmt(base)} × 2.5% = {_fmt(zakat)} {cur}")
    return Calculation(due, base, ZAKAT_RATE, zakat, nisab, label, cur, steps,
                       {"goods_value": goods_value, "cash": cash, "receivables": receivables, "deductible_debt": deductible_debt}, market)


def fitr_quantity(persons: int) -> dict:
    lo, hi = SAA_KG_APPROX
    return {
        "persons": persons,
        "saa_per_person": 1,
        "total_saa": persons,
        "approx_kg_range": [round(lo * persons, 2), round(hi * persons, 2)],
        "steps": [
            f"المقدار = {persons} × صاع واحد = {persons} صاع",
            f"تقدير الصاع بالكيلو يختلف بحسب نوع الطعام والتقدير المعتمد (نحو {lo}–{hi} كجم)؛ فالمجموع تقريبًا {lo * persons:.2f}–{hi * persons:.2f} كجم",
        ],
    }
