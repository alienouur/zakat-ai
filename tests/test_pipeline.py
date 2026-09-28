import pytest
from fastapi.testclient import TestClient

from app.calculator import cash_zakat, gold_zakat
from app.kb import KnowledgeBase
from app.main import app
from app.normalize import extract_numbers
from app.pipeline import detect_currency
from app.questioning import extract_from_text
from app.topics import detect_topics

MARKET = {
    "currency": "USD",
    "gold_per_gram": 80.0,
    "silver_per_gram": 1.0,
    "gold": {"source": "test", "as_of": "test", "fetched_at": ""},
    "silver": {"source": "test", "as_of": "test", "fetched_at": ""},
    "fx": None,
}
PRICES = {"gold_per_gram": 80, "silver_per_gram": 1}
CASH_ANSWERS = {"ownership": "full", "hawl": "yes", "other_money": "no", "purpose": "savings", "debts": "none"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# ----------------------------------------------------------------------------- knowledge base


def test_kb_loads_without_errors():
    kb = KnowledgeBase()
    assert not kb.errors
    for clone in ("albani", "ibn_uthaymeen", "ibn_baz", "maliki"):
        assert kb.by_clone[clone], clone
        assert kb.meta[clone].disclaimer_ar


def test_records_are_not_shared_between_clones():
    kb = KnowledgeBase()
    for clone, recs in kb.by_clone.items():
        assert all(r.clone == clone for r in recs)


def test_verified_records_have_a_locator():
    kb = KnowledgeBase()
    for recs in kb.by_clone.values():
        for r in recs:
            if r.verification_status == "verified":
                src = r.primary_source
                assert src.url or src.page, r.id


# ----------------------------------------------------------------------------- extraction


@pytest.mark.parametrize("text,expected", [("10,000", 10000.0), ("١٢٬٥٠٠", 12500.0), ("5 الف", 5000.0), ("3.5 مليون", 3_500_000.0)])
def test_extract_numbers(text, expected):
    assert extract_numbers(text)[0] == expected


def test_gold_weight_not_confused_with_karat():
    ans = extract_from_text("عندي ذهب عيار 21 وزن 120 جرام", "gold", {})
    assert ans["gold_grams"] == 120
    assert ans["karat"] == "21"


@pytest.mark.parametrize("text,code", [("عندي 10,000 دولار", "USD"), ("500 SAR", "SAR"), ("3000$", "USD"), ("ريال قطري", "QAR"), ("جنيه", "EGP")])
def test_detect_currency(text, code):
    assert detect_currency(text)[0] == code


@pytest.mark.parametrize("text,topic", [
    ("راتبي 8000 ريال شهريا كيف ازكيه", "salary"),
    ("ما زكاة الزروع وكم النصاب", "crops"),
    ("ما هو نصاب الذهب", "gold"),
    ("عندي ذهب مدخر 200 جرام", "gold"),
    ("هل في حلي المرأة الملبوس زكاة؟", "jewelry"),
    ("هل يجوز إخراج زكاة الفطر نقدا؟", "zakat_fitr"),
    ("كم نصاب النقود", "nisab"),
    ("عندي 10,000 دولار، كم زكاتي؟", "cash"),
])
def test_detect_topics(text, topic):
    assert detect_topics(text)[0][0].id == topic


# ----------------------------------------------------------------------------- calculator


def test_cash_zakat_math_is_separate_from_ruling():
    calc = cash_zakat(10000, MARKET, "silver")
    assert calc.due is True
    assert calc.zakat_amount == pytest.approx(250.0)
    assert calc.nisab_value == pytest.approx(595.0)
    below = cash_zakat(500, MARKET, "silver")
    assert below.due is False and below.zakat_amount == 0.0


def test_gold_zakat_uses_pure_gold():
    calc = gold_zakat(120, 21, MARKET)
    assert calc.due is True
    assert calc.zakatable_base == pytest.approx(120 * 21 / 24 * 80)


# ----------------------------------------------------------------------------- API / pipeline


def test_no_ruling_before_required_information(client):
    r = client.post("/api/ask", json={"clone": "ibn_uthaymeen", "message": "عندي 10,000 دولار، كم زكاتي؟"})
    d = r.json()
    assert r.status_code == 200
    assert d["stage"] == "clarify"
    assert d["questions"] and len(d["questions"]) <= 3
    assert d["calculation"] is None
    assert d["answers"]["amount"] == 10000
    assert d["answers"]["currency"] == "USD"


def test_answer_with_complete_information_has_sources_and_calculation(client):
    r = client.post("/api/ask", json={
        "clone": "ibn_baz", "message": "عندي 10,000 دولار، كم زكاتي؟",
        "answers": CASH_ANSWERS, "manual_prices": PRICES,
    })
    d = r.json()
    assert d["stage"] == "answer"
    assert d["sections"][0]["kind"] == "ruling"
    assert d["citations"]
    assert all(c["verification_status"] in ("verified", "partially_verified") for c in d["citations"])
    assert all(any(line.startswith("الرابط") for line in c["lines"]) for c in d["citations"])
    assert d["calculation"]["zakat_amount"] == pytest.approx(250.0)
    assert d["calculation"]["market"]["gold"]["source"]
    assert d["clone"]["disclaimer_ar"]


def test_answer_always_states_evidence_or_its_absence(client):
    for clone in ("albani", "ibn_uthaymeen", "ibn_baz", "maliki"):
        d = client.post("/api/ask", json={"clone": clone, "message": "عندي 10,000 دولار، كم زكاتي؟", "answers": CASH_ANSWERS, "manual_prices": PRICES}).json()
        kinds = [s["kind"] for s in d["sections"]]
        assert "evidence" in kinds, clone
        assert kinds.index("ruling") < kinds.index("evidence")
        for s in d["sections"]:
            if s["kind"] == "related":
                assert not s["title"].split(": ", 1)[1].isascii(), s["title"]  # Arabic topic label, not the raw id


def test_clone_only_uses_its_own_records(client):
    kb = KnowledgeBase()
    for clone in ("albani", "ibn_uthaymeen", "ibn_baz", "maliki"):
        r = client.post("/api/ask", json={"clone": clone, "message": "عندي 10,000 دولار، كم زكاتي؟", "answers": CASH_ANSWERS, "manual_prices": PRICES})
        d = r.json()
        assert d["stage"] == "answer"
        for rid in d["records"]:
            assert kb.get(rid).clone == clone


def test_cash_question_does_not_cite_fitr_or_crops_records(client):
    kb = KnowledgeBase()
    for clone in ("albani", "ibn_uthaymeen", "ibn_baz"):
        r = client.post("/api/ask", json={"clone": clone, "message": "عندي 10,000 دولار، كم زكاتي؟", "answers": CASH_ANSWERS, "manual_prices": PRICES})
        for rid in r.json()["records"]:
            tags = set(kb.get(rid).tags)
            assert not tags & {"zakat_al_fitr", "crops", "livestock"}, (clone, rid)


def test_skipped_question_is_not_assumed(client):
    answers = {**CASH_ANSWERS, "hawl": "__skip__"}
    d = client.post("/api/ask", json={"clone": "ibn_baz", "message": "عندي 10,000 دولار، كم زكاتي؟", "answers": answers, "manual_prices": PRICES}).json()
    assert d["stage"] == "answer"
    assert "hawl" not in d["answers"]
    assert d["answers"]["_skipped"] == ["hawl"]
    assert any("لم تُجب" in n for n in d["notes"])
    assert any("مشروط" in s["text"] for s in d["sections"] if s["kind"] == "analysis")


def test_skipping_currency_disables_calculation(client):
    answers = {**CASH_ANSWERS, "currency": "__skip__"}
    d = client.post("/api/ask", json={"clone": "ibn_baz", "message": "عندي 10,000 كم زكاتي؟", "topic": "cash", "answers": answers, "manual_prices": PRICES}).json()
    assert d["stage"] == "answer"
    assert d["calculation"] is None


def test_no_calculation_without_prices(client):
    d = client.post("/api/ask", json={"clone": "ibn_baz", "message": "عندي 10,000 دولار، كم زكاتي؟", "answers": CASH_ANSWERS}).json()
    assert d["stage"] == "answer"
    if d["calculation"] is None:
        assert any("يدوي" in s["text"] for s in d["sections"])
    else:
        assert d["calculation"]["market"]["gold"]["source"] and d["calculation"]["market"]["gold"]["as_of"]


def test_unknown_topic_returns_clarify(client):
    d = client.post("/api/ask", json={"clone": "albani", "message": "هل تجب الزكاة في هذا؟"}).json()
    assert d["stage"] == "clarify"
    assert d["topic"] is None


def test_compare_lists_all_four_clones_without_choosing(client):
    d = client.post("/api/compare", json={"message": "هل في حلي المرأة الملبوس زكاة؟"}).json()
    assert [f["clone"] for f in d["findings"]] == ["albani", "ibn_uthaymeen", "ibn_baz", "maliki"]
    assert all(f["found"] for f in d["findings"])
    assert d["difference_points"]
    assert d["agreement_points"]  # partial agreement (3 vs Maliki) is still reported, never left blank
    assert any("بخلاف المذهب المالكي" in a for a in d["agreement_points"])
    assert "أصح" not in " ".join(d["agreement_points"] + d["difference_points"])
    maliki = next(f for f in d["findings"] if f["clone"] == "maliki")
    assert maliki["is_mashhur"] is not None


def test_static_ui_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/clones").json()[-1]["id"] == "comparative"
