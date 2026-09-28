"""Knowledge-base schema and loader.

Layout (one independent dataset per clone):

    zakat_knowledge_base/<clone>/<folder>/*.json   -> list of Record
    zakat_knowledge_base/<clone>/metadata/clone.json     -> CloneMeta
    zakat_knowledge_base/<clone>/metadata/sources.json   -> list of Source (bibliography)

Records never cross clone boundaries: a record's ``clone`` field must match the
directory it lives in, and retrieval is always filtered by clone.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

KB_ROOT = Path(__file__).resolve().parent.parent / "zakat_knowledge_base"

CloneId = Literal["albani", "ibn_uthaymeen", "ibn_baz", "maliki", "comparative"]
VerificationStatus = Literal["verified", "partially_verified", "unverified"]
SourceType = Literal[
    "book", "verified_book", "official_fatwa", "recorded_fatwa", "audio_transcript",
    "hadith_grading", "classical_text", "commentary", "contemporary_fatwa", "secondary",
]

# Lower is more authoritative (spec section 19).
SOURCE_PRIORITY: dict[str, int] = {
    "book": 1,
    "classical_text": 1,
    "verified_book": 2,
    "commentary": 2,
    "hadith_grading": 2,
    "official_fatwa": 3,
    "recorded_fatwa": 4,
    "audio_transcript": 4,
    "contemporary_fatwa": 5,
    "secondary": 6,
}


class Source(BaseModel):
    title: str
    author: str
    source_type: SourceType
    edition: str | None = None
    publisher: str | None = None
    year: str | None = None
    volume: str | None = None
    chapter: str | None = None
    page: str | None = None
    url: str | None = None
    digital_source: str | None = None
    quote: str | None = Field(default=None, description="Original text as it appears in the source")
    notes: str | None = None

    def citation_lines(self, scholar_label: str) -> list[str]:
        unverified = "غير متحقق منه"
        return [
            f"اسم العالم/المذهب: {scholar_label}",
            f"اسم الكتاب/المصدر: {self.title}",
            f"المؤلف: {self.author}",
            f"الباب: {self.chapter or unverified}",
            f"الجزء: {self.volume or unverified}",
            f"الصفحة: {self.page or ('رقم الصفحة غير متحقق منه' if self.source_type in ('book','classical_text','verified_book','commentary') else 'لا ينطبق (مصدر إلكتروني)')}",
            f"الرابط: {self.url or unverified}",
        ]


class HadithRef(BaseModel):
    text: str
    reference: str
    grading: str | None = None
    grading_by: str | None = None


class QuranRef(BaseModel):
    surah: str
    ayah: str
    text: str | None = None


class Record(BaseModel):
    id: str
    clone: CloneId
    scholar: str
    school: str
    topic: str = "zakat"
    subtopic: str
    tags: list[str] = Field(default_factory=list)
    question: str
    ruling: str
    evidence: str | None = None
    evidence_explanation: str | None = None
    scholar_statement: str | None = Field(default=None, description="Verbatim quote attributed to the scholar / text")
    quran: list[QuranRef] = Field(default_factory=list)
    hadith: list[HadithRef] = Field(default_factory=list)
    sources: list[Source] = Field(min_length=1)
    # Maliki-only: distinguishes the mashhur from other narrations
    is_mashhur: bool | None = None
    other_opinions: list[str] = Field(default_factory=list)
    reason_for_difference: str | None = None
    verification_status: VerificationStatus
    verification_notes: str | None = None
    date_verified: str | None = None
    language: str = "ar"

    @model_validator(mode="after")
    def _check_verified_has_url_or_page(self) -> Record:
        if self.verification_status == "verified":
            ok = any(s.url or s.page for s in self.sources)
            if not ok:
                raise ValueError(f"{self.id}: verified records need at least one source with url or page")
        return self

    @property
    def primary_source(self) -> Source:
        return sorted(self.sources, key=lambda s: SOURCE_PRIORITY.get(s.source_type, 9))[0]

    def search_text(self) -> str:
        parts = [self.subtopic, " ".join(self.tags), self.question, self.ruling,
                 self.evidence or "", self.scholar_statement or "",
                 " ".join(o for o in self.other_opinions)]
        return "\n".join(parts)


class CalcParam(BaseModel):
    """A calculator parameter together with the dataset record(s) that justify it."""

    value: str
    basis_records: list[str] = Field(default_factory=list)
    note_ar: str | None = None


class CalcParams(BaseModel):
    # how the nisab of cash is measured: gold | silver | lower (whichever is reached first)
    nisab_standard: CalcParam | None = None
    # deduction of debts owed by the payer: none | due | all | disputed
    debt_deduction: CalcParam | None = None
    # worn jewelry: obligatory | not_obligatory | disputed
    jewelry_worn: CalcParam | None = None
    # zakat al-fitr in cash: allowed | not_allowed | disputed
    fitr_cash: CalcParam | None = None
    # trade goods valuation: market | disputed
    trade_valuation: CalcParam | None = None
    # whether the fixed 2.5% zakat applies to trade goods: fixed | no_fixed_zakat | disputed
    trade_goods: CalcParam | None = None
    # zakat on wealth of a minor: obligatory | not_obligatory | disputed
    child_wealth: CalcParam | None = None


class CloneMeta(BaseModel):
    id: CloneId
    name_ar: str
    name_en: str
    school: str
    description_ar: str
    disclaimer_ar: str
    methodology_ar: list[str] = Field(default_factory=list)
    source_priority_ar: list[str] = Field(default_factory=list)
    # optional description of the kinds of text the dataset contains (book text, transcript, grading...)
    text_layers_ar: dict[str, str] = Field(default_factory=dict)
    calc_params: CalcParams = Field(default_factory=CalcParams)


class KnowledgeBase:
    def __init__(self, root: Path = KB_ROOT):
        self.root = root
        self.records: list[Record] = []
        self.by_clone: dict[str, list[Record]] = {}
        self.meta: dict[str, CloneMeta] = {}
        self.bibliography: dict[str, list[Source]] = {}
        self.errors: list[str] = []
        self.load()

    def load(self) -> None:
        self.records, self.by_clone, self.meta, self.bibliography, self.errors = [], {}, {}, {}, []
        if not self.root.exists():
            return
        for clone_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            clone = clone_dir.name
            meta_dir = clone_dir / "metadata"
            if (meta_dir / "clone.json").exists():
                try:
                    self.meta[clone] = CloneMeta.model_validate_json((meta_dir / "clone.json").read_text("utf-8"))
                except ValidationError as e:
                    self.errors.append(f"{clone}/metadata/clone.json: {e}")
            if (meta_dir / "sources.json").exists():
                try:
                    raw = json.loads((meta_dir / "sources.json").read_text("utf-8"))
                    self.bibliography[clone] = [Source.model_validate(s) for s in raw]
                except (ValidationError, json.JSONDecodeError) as e:
                    self.errors.append(f"{clone}/metadata/sources.json: {e}")
            for path in sorted(clone_dir.rglob("*.json")):
                if path.parent.name == "metadata":
                    continue
                try:
                    raw = json.loads(path.read_text("utf-8"))
                except json.JSONDecodeError as e:
                    self.errors.append(f"{path}: {e}")
                    continue
                items = raw if isinstance(raw, list) else [raw]
                for item in items:
                    try:
                        rec = Record.model_validate(item)
                    except ValidationError as e:
                        self.errors.append(f"{path}: {e}")
                        continue
                    if rec.clone != clone:
                        self.errors.append(f"{path}: record {rec.id} has clone={rec.clone} but lives in {clone}/")
                        continue
                    self.records.append(rec)
                    self.by_clone.setdefault(clone, []).append(rec)
        ids = [r.id for r in self.records]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            self.errors.append(f"duplicate record ids: {sorted(dupes)}")

    def clone_records(self, clone: str) -> list[Record]:
        return self.by_clone.get(clone, [])

    def get(self, record_id: str) -> Record | None:
        return next((r for r in self.records if r.id == record_id), None)

    def stats(self) -> dict:
        out = {}
        for clone, recs in self.by_clone.items():
            out[clone] = {
                "records": len(recs),
                "verified": sum(r.verification_status == "verified" for r in recs),
                "partially_verified": sum(r.verification_status == "partially_verified" for r in recs),
                "unverified": sum(r.verification_status == "unverified" for r in recs),
                "subtopics": sorted({r.subtopic for r in recs}),
            }
        return out
