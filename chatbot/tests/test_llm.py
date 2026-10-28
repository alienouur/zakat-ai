import copy
import logging

from chatbot.config import Settings
from chatbot.llm import MAX_TOOL_ROUNDS, MSG_REFUSAL, GeminiChat, GeminiError

DECL = {"name": "send_escalation_email", "description": "d", "parameters": {"type": "object", "properties": {}}}


def text_resp(text, finish="STOP"):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}]}


def call_resp(*calls):
    parts = [{"functionCall": {"name": n, "args": a}, "thoughtSignature": "sig"} for n, a in calls]
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]}


class Script:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []

    def __call__(self, body, model=None):
        self.bodies.append(copy.deepcopy(body))
        self.models = getattr(self, "models", []) + [model]
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(script, **env):
    import os
    for k, v in env.items():
        os.environ[k] = v
    chat = GeminiChat("sys", DECL, Settings(), transport=script)
    for k in env:
        os.environ.pop(k, None)
    chat._sleep = lambda s: chat.__dict__.setdefault("slept", []).append(s)
    return chat


def test_tool_loop_terminates_and_results_in_one_message():
    script = Script([call_resp(("send_escalation_email", {"a": 1}), ("send_escalation_email", {"a": 2})), text_resp("تم الإرسال، الرقم المرجعي ZKT-1")])
    calls = []

    def tool(name, args):
        calls.append(args)
        return {"status": "sent", "reference": f"ZKT-{args['a']}"}

    res = make(script).chat([], "<knowledge>k</knowledge>\n\nسؤال", tool)
    assert res.reply.startswith("تم الإرسال") and res.escalation == {"reference": "ZKT-2"} and res.rounds == 2
    assert calls == [{"a": 1}, {"a": 2}]
    roles = [m["role"] for m in res.new_messages]
    assert roles == ["user", "model", "user", "model"]
    fr = res.new_messages[2]["parts"]
    assert len(fr) == 2 and all("functionResponse" in p for p in fr)
    assert res.new_messages[1]["parts"][0]["thoughtSignature"] == "sig"  # model turn stored verbatim
    assert script.bodies[0]["system_instruction"]["parts"][0]["text"] == "sys"
    assert script.bodies[0]["tools"][0]["functionDeclarations"] == [DECL]
    assert script.bodies[1]["contents"] == res.new_messages[:3]


def test_tool_error_is_marked_and_loop_is_capped():
    script = Script([call_resp(("send_escalation_email", {})) for _ in range(MAX_TOOL_ROUNDS + 1)])
    res = make(script).chat([], "q", lambda n, a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert res.stop_reason == "TOOL_LIMIT" and res.escalation is None
    assert len(script.bodies) == MAX_TOOL_ROUNDS + 1
    assert res.new_messages[2]["parts"][0]["functionResponse"]["response"]["is_error"] is True


def test_history_is_append_only():
    history = [{"role": "user", "parts": [{"text": "old"}]}, {"role": "model", "parts": [{"text": "old reply"}]}]
    snapshot = copy.deepcopy(history)
    res = make(Script([text_resp("جديد")])).chat(history, "q", lambda n, a: {})
    assert history == snapshot and res.new_messages[0]["parts"][0]["text"] == "q" and res.new_messages[-1]["parts"][0]["text"] == "جديد"


def test_refusal_and_blocked_prompt():
    res = make(Script([text_resp("", "SAFETY")])).chat([], "q", lambda n, a: {})
    assert res.reply == MSG_REFUSAL and res.stop_reason == "REFUSAL"
    res = make(Script([{"promptFeedback": {"blockReason": "OTHER"}, "candidates": []}])).chat([], "q", lambda n, a: {})
    assert res.reply == MSG_REFUSAL


def test_max_tokens_logged_and_partial_shown(caplog):
    with caplog.at_level(logging.WARNING, logger="chatbot.llm"):
        res = make(Script([text_resp("جواب مقطوع", "MAX_TOKENS")])).chat([], "q", lambda n, a: {})
    assert res.reply == "جواب مقطوع" and res.stop_reason == "MAX_TOKENS" and "MAX_TOKENS" in caplog.text


def test_api_errors_become_polite_arabic():
    for err, needle in [(GeminiError(429, "quota"), "مزدحمة"), (GeminiError(503, "high demand"), "مزدحمة"), (GeminiError(500, "x"), "خلل"), (GeminiError(None, "ConnectError"), "الاتصال")]:
        res = make(Script([err] * 3)).chat([], "q", lambda n, a: {})
        assert needle in res.reply and res.stop_reason == "ERROR" and "quota" not in res.reply
    res = make(Script([GeminiError(400, "bad key")])).chat([], "q", lambda n, a: {})  # not transient: no retry
    assert res.stop_reason == "ERROR" and "خلل" in res.reply


def test_transient_errors_are_retried_with_backoff_then_fallback_model():
    script = Script([GeminiError(503, "busy"), GeminiError(None, "ConnectError"), text_resp("نجح")])
    chat = make(script)
    res = chat.chat([], "q", lambda n, a: {})
    assert res.reply == "نجح" and res.stop_reason == "STOP"
    assert chat.slept == [1.0, 2.0] and len(script.bodies) == 3
    # primary fails 3 times -> fallback model is tried
    script = Script([GeminiError(503, "busy")] * 3 + [text_resp("من الاحتياطي")])
    chat = make(script, GEMINI_FALLBACK_MODEL="gemini-2.5-flash")
    res = chat.chat([], "q", lambda n, a: {})
    assert res.reply == "من الاحتياطي" and script.models == ["gemini-3.8-flash"] * 3 + ["gemini-2.5-flash"]
    assert chat.slept == [1.0, 2.0]  # no sleep after the last primary attempt or before the fallback
    # everything fails -> busy message, 6 attempts total
    script = Script([GeminiError(503, "busy")] * 6)
    res = make(script, GEMINI_FALLBACK_MODEL="gemini-2.5-flash").chat([], "q", lambda n, a: {})
    assert "مزدحمة" in res.reply and len(script.bodies) == 6


def test_draft_fatwa_request_parses_json_and_fails_soft():
    good = text_resp('{"subject": "زكاة الحلي", "question": "أنا أملك حليًا…", "missing_info": ["الوزن"], "related_articles": ["4", 7]}')
    script = Script([good])
    d = make(script).draft_fatwa_request(["هل في الحلي زكاة"], "لا تتناول اللائحة")
    assert d == {"subject": "زكاة الحلي", "question": "أنا أملك حليًا…", "missing_info": ["الوزن"], "related_articles": [4, 7]}
    assert "tools" not in script.bodies[0] and script.bodies[0]["generationConfig"]["responseMimeType"] == "application/json"
    assert make(Script([text_resp("not json")])).draft_fatwa_request(["q"], "") is None
    assert make(Script([GeminiError(500, "x")])).draft_fatwa_request(["q"], "") is None


def test_probe_reports_google_error_details():
    assert make(Script([text_resp("جاهز")])).probe()["ok"] is True
    bad = make(Script([GeminiError(400, '{"error": {"message": "User location is not supported for the API use."}}')])).probe()
    assert bad["ok"] is False and bad["status"] == 400 and "location" in bad["error"]


def test_fallback_chain_is_comma_separated_and_deduplicated():
    chat = make(Script([]), GEMINI_FALLBACK_MODEL="gemini-3.6-flash, gemini-3.1-pro-preview,gemini-3.8-flash")
    assert chat._models() == ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.1-pro-preview"]
    script = Script([GeminiError(503, "busy")] * 6 + [text_resp("من الثالث")])
    res = make(script, GEMINI_FALLBACK_MODEL="gemini-3.6-flash,gemini-3.1-pro-preview").chat([], "q", lambda n, a: {})
    assert res.reply == "من الثالث" and script.models[-1] == "gemini-3.1-pro-preview"
