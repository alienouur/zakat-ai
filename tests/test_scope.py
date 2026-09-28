import pytest
from fastapi.testclient import TestClient

from app import main
from app.scope import ScopeGuard, classify_rules


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(main.app)


# ----------------------------------------------------------------------------- deterministic rules


@pytest.mark.parametrize(
    "text",
    [
        "ما هو الطقس اليوم في الرياض؟",
        "اكتب لي كود بايثون يطبع الأرقام",
        "من فاز بمباراة الأمس؟",
        "كيف أطبخ الكبسة",
        "ما رأيك في الذكاء الاصطناعي",
        "هل الحج واجب على كل مسلم",
        "كيف أتوضأ للصلاة",
    ],
)
def test_clearly_unrelated_questions_are_out_of_scope(text):
    assert classify_rules(text).verdict == "out_of_scope"


@pytest.mark.parametrize(
    "text",
    [
        "عندي 10,000 دولار كم زكاتي؟",
        "هل في حلي المرأة الملبوس زكاة؟",
        "عندي 100 جرام ذهب منذ سنتين",
        "ورثت 100 ألف ريال منذ سنة هل عليها شيء",
        "هل يجوز إخراج زكاة الفطر نقدًا",
        "لمن تُعطى الزكاة",
    ],
)
def test_zakat_questions_stay_in_scope(text):
    assert classify_rules(text).verdict == "in_scope"


def test_crypto_and_modern_assets_are_zakat_scope_not_off_topic():
    # the pipeline (not the guard) decides that the sources are insufficient for these
    assert classify_rules("عندي بيتكوين وعملات رقمية منذ سنة").verdict == "in_scope"
    assert classify_rules("زكاة الأسهم في الشركات").verdict == "in_scope"


def test_greeting_is_not_treated_as_unrelated():
    d = classify_rules("السلام عليكم")
    assert d.verdict == "greeting"
    assert classify_rules("من أنت؟").verdict == "greeting"


def test_clarification_answers_and_selected_topic_bypass_the_guard():
    assert classify_rules("نعم", continuation=True).verdict == "in_scope"
    assert classify_rules("5000", continuation=True).verdict == "in_scope"
    assert classify_rules("لا أعرف", continuation=True).verdict == "in_scope"
    assert classify_rules("عندي 300 جرام", topic_hint=True).verdict == "in_scope"
    # the same words in a fresh conversation are a request for a real question, not a refusal
    assert classify_rules("نعم").verdict == "unclear"
    assert classify_rules("5000").verdict == "unclear"


def test_asset_word_alone_is_not_enough():
    # أرض/سيارة/ذهب are zakat *assets*, but these sentences are not about owning them
    for text in ("كم عمر الأرض؟", "ذهبت إلى السوق واشتريت سيارة", "ما حكم بيع الذهب بالتقسيط"):
        assert classify_rules(text).verdict != "in_scope", text
    # ...while an owned asset with an amount or a holding period is
    assert classify_rules("عندي سيارة للبيع منذ سنة").verdict == "in_scope"
    assert classify_rules("أملك أرضًا للتجارة").verdict == "in_scope"


def test_asset_word_inside_a_foreign_domain_does_not_make_it_zakat():
    d = classify_rules("ما حكم الربا في البنك")
    assert d.verdict in ("unclear", "out_of_scope")
    d = classify_rules("ما هي عاصمة الأرض المقدسة في التاريخ")
    assert d.verdict != "in_scope"


# ----------------------------------------------------------------------------- guard with / without the model


@pytest.mark.anyio
async def test_guard_without_model_falls_back_to_rules():
    g = ScopeGuard(llm=None)
    assert (await g.classify("ما هو الطقس اليوم")).verdict == "out_of_scope"
    assert (await g.classify("عندي 10,000 دولار كم زكاتي")).verdict == "in_scope"
    assert (await g.classify("نعم")).verdict == "unclear"
    assert (await g.classify("ما حكم الربا في البنك")).verdict == "out_of_scope"  # asset word + foreign fiqh chapter
    assert (await g.classify("كم عمر الأرض؟")).verdict == "unclear"  # asset word alone: ask the user to rephrase


class _FakeLLM:
    enabled = True

    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    async def classify_scope(self, message):
        self.calls.append(message)
        return self.verdict


@pytest.mark.anyio
async def test_model_is_not_consulted_when_rules_are_decisive():
    llm = _FakeLLM(False)
    g = ScopeGuard(llm=llm)
    assert (await g.classify("عندي 10,000 دولار كم زكاتي")).verdict == "in_scope"
    assert (await g.classify("السلام عليكم")).verdict == "greeting"
    assert (await g.classify("نعم", continuation=True)).verdict == "in_scope"
    assert (await g.classify("نعم")).verdict == "unclear"
    assert llm.calls == []


@pytest.mark.anyio
async def test_model_arbitrates_only_ambiguous_messages():
    yes = ScopeGuard(llm=_FakeLLM(True))
    d = await yes.classify("عندي مال كثير هل عليه شيء")
    assert d.verdict == "in_scope" and d.method == "llm"
    no = ScopeGuard(llm=_FakeLLM(False))
    d = await no.classify("كم عمر الأرض؟")
    assert d.verdict == "out_of_scope"
    undecided = ScopeGuard(llm=_FakeLLM(None))
    d = await undecided.classify("عندي مال كثير هل عليه شيء")
    assert d.verdict == "unclear" and d.method == "rules"


# ----------------------------------------------------------------------------- API


def test_unrelated_question_is_refused_before_retrieval(client, monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("pipeline must not run for out-of-scope requests")

    monkeypatch.setattr(main.pipeline, "prepare", must_not_run)
    d = client.post("/api/ask", json={"clone": "albani", "message": "ما هو الطقس اليوم في الرياض؟"}).json()
    assert d["stage"] == "out_of_scope"
    assert d["scope"]["kind"] == "out_of_scope"
    assert "الزكاة" in d["scope"]["message_ar"]
    assert d["scope"]["examples_ar"]
    assert d["sections"] == [] and d["citations"] == [] and d["calculation"] is None


def test_zakat_question_still_goes_through_the_pipeline(client):
    d = client.post("/api/ask", json={"clone": "ibn_baz", "message": "عندي 10,000 دولار، كم زكاتي؟"}).json()
    assert d["stage"] == "clarify"
    assert d["topic"]["id"] == "cash"
    assert d["questions"]


def test_greeting_gets_a_welcome_not_a_refusal(client):
    d = client.post("/api/ask", json={"clone": "albani", "message": "مرحبا"}).json()
    assert d["stage"] == "out_of_scope"
    assert d["scope"]["kind"] == "greeting"


def test_clarification_answers_are_not_refused(client):
    first = client.post("/api/ask", json={"clone": "ibn_baz", "message": "عندي 10,000 دولار، كم زكاتي؟"}).json()
    assert first["stage"] == "clarify"
    second = client.post(
        "/api/ask",
        json={"clone": "ibn_baz", "message": "عندي 10,000 دولار، كم زكاتي؟", "topic": "cash", "answers": {"hawl": "yes"}},
    ).json()
    assert second["stage"] in ("clarify", "answer")
    assert second["topic"]["id"] == "cash"


def test_compare_refuses_unrelated_and_keeps_zakat(client):
    d = client.post("/api/compare", json={"message": "اكتب لي قصيدة عن البحر"}).json()
    assert d["stage"] == "out_of_scope"
    assert "findings" not in d
    d = client.post("/api/compare", json={"message": "هل في حلي المرأة الملبوس زكاة؟"}).json()
    assert len(d["findings"]) == 4
