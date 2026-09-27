"""Arabic text normalisation and tokenisation used by the classifier and the BM25 index."""
from __future__ import annotations

import re

_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

_CHAR_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
        "ک": "ك",
        "ی": "ي",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "،": " ",
        "؟": " ",
        "؛": " ",
    }
)

_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ل", "ك")
_SUFFIXES = ("هما", "كما", "ات", "ون", "ين", "ان", "ها", "هم", "كم", "نا", "ه", "ك", "ي", "ت")

STOPWORDS = {
    "في", "من", "على", "الى", "عن", "ان", "او", "و", "ثم", "هل", "ما", "لا", "لم", "لن", "قد",
    "كان", "هو", "هي", "هذا", "هذه", "ذلك", "تلك", "التي", "الذي", "الذين", "كل", "بعض", "مع",
    "عند", "عندي", "لدي", "لي", "له", "لها", "لهم", "به", "بها", "اذا", "اذ", "كم", "كيف", "ماذا",
    "لماذا", "اي", "ايضا", "غير", "بين", "حتى", "بعد", "قبل", "ليس", "نعم", "الشيخ", "السائل",
    "السؤال", "الجواب", "فضيله", "يقول", "تقول", "سؤال", "جزاكم", "الله", "خيرا",
}


def normalize(text: str) -> str:
    text = text or ""
    text = _DIACRITICS.sub("", text)
    text = text.translate(_CHAR_MAP)
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip().lower()


def light_stem(token: str) -> str:
    if len(token) <= 3:
        return token
    for p in _PREFIXES:
        if token.startswith(p) and len(token) - len(p) >= 3:
            token = token[len(p):]
            break
    for s in _SUFFIXES:
        if token.endswith(s) and len(token) - len(s) >= 3:
            token = token[: -len(s)]
            break
    return token


def tokenize(text: str, stem: bool = True) -> list[str]:
    tokens = [t for t in normalize(text).split() if t and t not in STOPWORDS]
    if stem:
        tokens = [light_stem(t) for t in tokens]
    return [t for t in tokens if len(t) > 1]


_NUMBER = re.compile(r"(\d[\d,\.]*)\s*(ألف|الف|مليون|k|m)?", re.IGNORECASE)


def extract_numbers(text: str) -> list[float]:
    """Return numeric amounts found in the text (Arabic digits are normalised first)."""
    out: list[float] = []
    for raw, unit in _NUMBER.findall(normalize(text)):
        raw = raw.replace(",", "").rstrip(".")
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        unit = (unit or "").lower()
        if unit in ("ألف", "الف", "k"):
            value *= 1_000
        elif unit in ("مليون", "m"):
            value *= 1_000_000
        out.append(value)
    return out
