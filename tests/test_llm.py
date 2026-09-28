import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import GeminiRephraser, Narrative, answer_context, validate


@pytest.fixture
def anyio_backend():
    return "asyncio"


CONTEXT = (
    "سؤال المستخدم: عندي 10,000 دولار\n[الحكم] تجب الزكاة في النقود إذا بلغت النصاب وحال عليها الحول.\n"
    "[الحساب] الزكاة = 10,000 × 2.5% = 250 USD\nالمصدر: https://binbaz.org.sa/fatwas/1"
)


def test_validate_accepts_faithful_restatement():
    assert validate("في مصادر الشيخ تجب الزكاة في النقود إذا بلغت النصاب؛ وحسابيًا 10,000 × 2.5% = 250 دولار. انظر المصادر أدناه.", CONTEXT) is None


@pytest.mark.parametrize(
    "bad,reason",
    [
        ("الزكاة الواجبة 300 دولار.", "unknown_number:300"),
        ("والرأي الصحيح أن الزكاة واجبة.", "preference_language:الرأي الصحيح"),
        ("أنا الشيخ ابن باز وأقول تجب الزكاة.", "impersonation"),
        ("انظر https://example.com/fatwa", "unknown_url"),
        ("", "empty"),
    ],
)
def test_validate_rejects_additions(bad, reason):
    assert validate(bad, CONTEXT) == reason


def test_validate_allows_preference_words_present_in_source():
    ctx = CONTEXT + "\n[قول الشيخ] «والراجح عندي أن الحلي لا زكاة فيه»"
    assert validate("نقل الشيخ أن الراجح عنده أن الحلي لا زكاة فيه.", ctx) is None


def test_answer_context_includes_sections_and_citations():
    ctx = answer_context("س", {
        "topic": {"label_ar": "النقد"}, "clone": {"name_ar": "محاكاة"}, "answers": {"amount": 5, "_want_calc": True},
        "notes": ["ن"], "sections": [{"title": "الحكم", "text": "ت"}], "calculation": {"steps": ["خ"]},
        "citations": [{"lines": ["المصدر: x"], "verification_status": "verified"}],
    })
    for needle in ("النقد", "محاكاة", "amount=5", "_want_calc", "ن", "[الحكم]", "خ", "المصدر: x", "verified"):
        assert (needle in ctx) is not needle.startswith("_")


@pytest.mark.anyio
async def test_disabled_without_key():
    n = await GeminiRephraser(api_key="", model="m").narrate_answer("س", {"sections": []})
    assert n.used is False and n.reason == "no_api_key"


@pytest.mark.anyio
async def test_rejected_output_falls_back(monkeypatch):
    r = GeminiRephraser(api_key="k", model="m")

    async def fake(system, user):
        return "الزكاة 999 دولار"

    monkeypatch.setattr(r, "_generate", fake)
    n = await r.narrate_answer("س", {"sections": [{"title": "الحكم", "text": "تجب الزكاة"}]})
    assert n.used is False and n.reason.startswith("rejected: unknown_number")


@pytest.mark.anyio
async def test_request_failure_falls_back(monkeypatch):
    r = GeminiRephraser(api_key="k", model="m")

    async def boom(system, user):
        raise RuntimeError("down")

    monkeypatch.setattr(r, "_generate", boom)
    n = await r.narrate_compare("س", {"findings": []})
    assert n.used is False and n.reason == "request_failed: RuntimeError"


def test_api_reports_llm_status_and_keeps_template_answer(monkeypatch):
    client = TestClient(main.app)
    assert "llm" in client.get("/api/health").json()

    async def fake_narrate(message, data):
        return Narrative(used=True, text="صياغة", model="m")

    monkeypatch.setattr(main.rephraser, "narrate_compare", fake_narrate)
    d = client.post("/api/compare", json={"message": "هل في حلي المرأة الملبوس زكاة؟"}).json()
    assert d["narrative"] == {"used": True, "text": "صياغة", "reason": None, "model": "m"}
    assert len(d["findings"]) == 4  # the grounded comparison is untouched

    d = client.post("/api/compare", json={"message": "هل في حلي المرأة الملبوس زكاة؟", "use_llm": False}).json()
    assert "narrative" not in d
