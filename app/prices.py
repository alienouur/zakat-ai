"""Live market data (gold, silver, FX) with explicit provenance.

Every value returned carries ``source``, ``fetched_at`` and ``as_of`` so the answer can
state the date and origin of the data used; nothing is ever presented as "current"
without a timestamp. Results are cached briefly; on failure the caller must ask the
user for a manual price instead of using stale data.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import httpx

TROY_OUNCE_GRAMS = 31.1034768
GOLD_API = "https://api.gold-api.com/price/{symbol}"
FX_API = "https://open.er-api.com/v6/latest/USD"
_CACHE_TTL = 600


@dataclass
class Price:
    metal: str
    usd_per_gram: float
    usd_per_troy_ounce: float
    source: str
    as_of: str
    fetched_at: str


@dataclass
class Rates:
    base: str
    rates: dict[str, float]
    source: str
    as_of: str
    fetched_at: str


_cache: dict[str, tuple[float, object]] = {}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cached(key: str):
    item = _cache.get(key)
    if item and time.time() - item[0] < _CACHE_TTL:
        return item[1]
    return None


async def metal_price(symbol: str) -> Price:
    cached = _cached(symbol)
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(GOLD_API.format(symbol=symbol))
        r.raise_for_status()
        data = r.json()
    per_oz = float(data["price"])
    price = Price(
        metal="gold" if symbol == "XAU" else "silver",
        usd_per_gram=per_oz / TROY_OUNCE_GRAMS,
        usd_per_troy_ounce=per_oz,
        source="gold-api.com (سعر الأونصة بالدولار)",
        as_of=str(data.get("updatedAt", "")),
        fetched_at=_now(),
    )
    _cache[symbol] = (time.time(), price)
    return price


async def fx_rates() -> Rates:
    cached = _cached("fx")
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(FX_API)
        r.raise_for_status()
        data = r.json()
    rates = Rates(
        base="USD",
        rates={k: float(v) for k, v in data["rates"].items()},
        source="open.er-api.com (exchangerate-api.com)",
        as_of=str(data.get("time_last_update_utc", "")),
        fetched_at=_now(),
    )
    _cache["fx"] = (time.time(), rates)
    return rates


async def market_snapshot(currency: str = "USD") -> dict:
    """Gold/silver per gram in the requested currency plus provenance. Raises on failure."""
    currency = (currency or "USD").upper()
    gold = await metal_price("XAU")
    silver = await metal_price("XAG")
    rate = 1.0
    fx_meta: dict | None = None
    if currency != "USD":
        fx = await fx_rates()
        if currency not in fx.rates:
            raise ValueError(f"unsupported currency {currency}")
        rate = fx.rates[currency]
        fx_meta = {"source": fx.source, "as_of": fx.as_of, "usd_to_currency": rate}
    return {
        "currency": currency,
        "gold_per_gram": gold.usd_per_gram * rate,
        "silver_per_gram": silver.usd_per_gram * rate,
        "gold": asdict(gold),
        "silver": asdict(silver),
        "fx": fx_meta,
        "note": "أسعار السوق تتغير باستمرار؛ اعتمد سعر يوم تمام الحول عند الإخراج.",
    }
