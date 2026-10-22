from fastapi.testclient import TestClient

import chatbot.server as server
from chatbot.llm import ChatResult


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.draft = {"subject": "زكاة الحلي المستعمل", "question": "أنا أملك حليًا لزوجتي للاستعمال الشخصي، فهل تجب فيه الزكاة؟",
                      "missing_info": ["وزن الحلي بالغرام", "هل مرّ عليه حول"], "related_articles": [4]}

    def draft_fatwa_request(self, questions, last_reply):
        self.last_draft_input = (questions, last_reply)
        return self.draft

    def chat(self, history, user_text, execute_tool):
        self.calls.append((list(history), user_text))
        if "اشتكي" in user_text:
            out = execute_tool("send_escalation_email", {"request_type": "complaint", "subject": "شكوى", "user_name": "أحمد",
                                                         "contact_phone": "0911111111", "details": "تفاصيل الشكوى هنا بما يكفي من الطول."})
            return ChatResult("أُرسلت شكواك", [{"role": "user", "parts": [{"text": user_text}]}, {"role": "model", "parts": [{"text": "أُرسلت"}]}],
                              {"reference": out["reference"]})
        if "الحلي" in user_text:
            reply = "اللائحة لا تتناول زكاة الحلي؛ يمكنني إرسال سؤالك إلى المستشار الشرعي."
            return ChatResult(reply, [{"role": "user", "parts": [{"text": user_text}]}, {"role": "model", "parts": [{"text": reply}]}])
        return ChatResult("نصاب الغنم أربعون شاة (المادة 4، ص 4).",
                          [{"role": "user", "parts": [{"text": user_text}]}, {"role": "model", "parts": [{"text": "r"}]}])


def client():
    server.llm = FakeLLM()
    return TestClient(server.app), server.llm


def test_health_and_index():
    c, _ = client()
    h = c.get("/api/health").json()
    assert h["status"] == "ok" and h["records"] == 45 and h["email_dry_run"] is True
    assert "مساعد صندوق الزكاة" in c.get("/").text


def test_chat_flow_sessions_and_knowledge_injection():
    c, fake = client()
    r = c.post("/api/chat", json={"message": "كم نصاب الغنم"}).json()
    assert r["session_id"] and r["reply"].startswith("نصاب") and r["escalation"] is None
    assert r["citations"] and all("المادة (4)" in x["article"] for x in r["citations"])
    assert fake.calls[0][1].startswith("<knowledge>") and "سؤال المستخدم: كم نصاب الغنم" in fake.calls[0][1]
    r2 = c.post("/api/chat", json={"session_id": r["session_id"], "message": "وكم للإبل؟"}).json()
    assert r2["session_id"] == r["session_id"] and len(fake.calls[1][0]) == 2  # history passed
    assert server.sessions.get(r["session_id"])["questions"] == ["كم نصاب الغنم", "وكم للإبل؟"]


def test_escalation_reference_returned():
    c, _ = client()
    r = c.post("/api/chat", json={"message": "اريد ان اشتكي"}).json()
    assert r["escalation"]["reference"].startswith("ZKT-")


def test_limits_and_validation():
    c, _ = client()
    assert c.post("/api/chat", json={"message": "x" * 2000}).status_code == 413
    assert c.post("/api/chat", json={"message": "   "}).status_code == 400
    sid = c.post("/api/session/new").json()["session_id"]
    server.sessions.append(sid, [{"role": "user", "parts": [{"text": "m"}]}] * 40)
    r = c.post("/api/chat", json={"session_id": sid, "message": "سؤال"}).json()
    assert r["limit_reached"] is True and "محادثة جديدة" in r["reply"]
    unknown = c.post("/api/chat", json={"session_id": "does-not-exist", "message": "كم نصاب الغنم"}).json()
    assert unknown["session_id"] != "does-not-exist"


def test_no_answer_flag_and_direct_consultation():
    c, _ = client()
    ok = c.post("/api/chat", json={"message": "كم نصاب الغنم"}).json()
    assert ok["no_answer"] is False
    r = c.post("/api/chat", json={"session_id": ok["session_id"], "message": "هل في الحلي زكاة"}).json()
    assert r["no_answer"] is True
    bad = c.post("/api/consult", json={"session_id": r["session_id"], "name": "سالم", "question": "هل في حلي زوجتي زكاة وهو للاستعمال؟"})
    assert bad.status_code == 400 and "هاتف" in bad.json()["detail"]
    d = c.post("/api/consult/draft", json={"session_id": r["session_id"]}).json()
    assert d["drafted"] is True and d["subject"] == "زكاة الحلي المستعمل" and d["missing_info"] == ["وزن الحلي بالغرام", "هل مرّ عليه حول"]
    assert server.llm.last_draft_input[0] == ["كم نصاب الغنم", "هل في الحلي زكاة"] and "لا تتناول" in server.llm.last_draft_input[1]
    server.llm.draft = None  # model unavailable -> plain fallback, never an error
    fb = c.post("/api/consult/draft", json={"session_id": r["session_id"]}).json()
    assert fb["drafted"] is False and fb["question"] == "هل في الحلي زكاة"
    assert c.post("/api/consult/draft", json={}).json()["question"] == ""
    good = c.post("/api/consult", json={"session_id": r["session_id"], "name": "سالم", "contact_phone": "0921234567",
                                        "question": "هل في حلي زوجتي زكاة وهو للاستعمال؟", "subject": d["subject"], "related_articles": [4]}).json()
    assert good["reference"].startswith("ZKT-")
    eml = (server.settings.data_dir / "outbox" / f"{good['reference']}.eml").read_bytes().decode("utf-8", "replace")
    body = __import__("email").message_from_string(eml).get_payload(decode=True).decode("utf-8")
    assert "حلي زوجتي" in body and "آخر أسئلة المستخدم" in body and "كم نصاب الغنم" in body and "المادة 4" in body
