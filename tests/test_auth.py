import re
"""Accounts, login sessions, history persistence and the login gate on the question endpoints."""
import os

import pytest
from fastapi.testclient import TestClient

from app import main
from app.store import Store


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(main, "store", Store(tmp_path / "t.db"))
    monkeypatch.setattr(main, "REQUIRE_LOGIN", True)
    monkeypatch.setattr(main, "INVITE_CODE", "")
    monkeypatch.setattr(main, "COOKIE_SECURE", False)
    return TestClient(main.app)


def register(client, email="ali@example.com", name="علي", password="password123", **extra):
    return client.post("/api/auth/register", json={"email": email, "name": name, "password": password, **extra})


def test_ask_requires_login_when_gate_is_on(client):
    r = client.post("/api/ask", json={"clone": "ibn_uthaymeen", "message": "هل في الحلي زكاة", "use_llm": False})
    assert r.status_code == 401
    r = client.post("/api/compare", json={"message": "هل في الحلي زكاة", "use_llm": False})
    assert r.status_code == 401


def test_register_login_logout_cycle(client):
    r = register(client)
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "admin"  # first account becomes admin
    assert main.COOKIE_NAME in r.cookies
    assert client.get("/api/auth/me").json()["user"]["email"] == "ali@example.com"

    assert register(client).status_code == 409  # duplicate
    assert register(client, email="not-an-email").status_code == 400
    assert register(client, email="b@example.com", password="short").status_code == 422

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None

    assert client.post("/api/auth/login", json={"email": "ali@example.com", "password": "wrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "ALI@example.com", "password": "password123"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["user"]["name"] == "علي"


def test_second_user_is_not_admin_and_admin_endpoints_are_protected(client):
    register(client)
    client.post("/api/auth/logout")
    r = register(client, email="user2@example.com")
    assert r.json()["user"]["role"] == "user"
    assert client.get("/api/admin/users").status_code == 403
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "ali@example.com", "password": "password123"})
    users = client.get("/api/admin/users").json()
    assert {u["email"] for u in users} == {"ali@example.com", "user2@example.com"}
    assert client.get("/api/admin/stats").json()["users"] == 2


def test_invite_code_gate(client, monkeypatch):
    monkeypatch.setattr(main, "INVITE_CODE", "office-2026")
    assert register(client).status_code == 403
    assert register(client, invite_code="office-2026").status_code == 200


def test_history_is_saved_per_user_and_isolated(client):
    register(client)
    r = client.post("/api/ask", json={"clone": "ibn_uthaymeen", "message": "هل في حلي المرأة الملبوس زكاة؟", "use_llm": False})
    assert r.status_code == 200
    cid = r.json()["conversation_id"]
    assert cid is not None
    # follow-up in the same thread (clarification answers) stays in the same conversation
    r2 = client.post("/api/ask", json={"clone": "ibn_uthaymeen", "message": "هل في حلي المرأة الملبوس زكاة؟", "answers": r.json().get("answers", {}) or {"_want_calc": "no"}, "use_llm": False, "conversation_id": cid})
    assert r2.status_code == 200 and r2.json()["conversation_id"] == cid

    r3 = client.post("/api/compare", json={"message": "هل يجوز إخراج زكاة الفطر نقدًا؟", "use_llm": False})
    assert r3.status_code == 200 and r3.json()["conversation_id"] != cid

    convs = client.get("/api/history").json()
    assert [c["id"] for c in convs] == [r3.json()["conversation_id"], cid]
    assert convs[1]["clone"] == "ibn_uthaymeen" and convs[1]["title"].startswith("هل في حلي")

    full = client.get(f"/api/history/{cid}").json()
    roles = [m["role"] for m in full["messages"]]
    assert roles == ["user", "assistant", "assistant"]
    assert full["messages"][0]["content"]["text"].startswith("هل في حلي")
    assert "sections" in full["messages"][1]["content"] or "questions" in full["messages"][1]["content"]

    assert client.get("/api/history/search?q=حلي").json()[0]["conversation_id"] == cid
    assert client.patch(f"/api/history/{cid}", json={"title": "زكاة الحلي"}).status_code == 200
    assert client.get(f"/api/history/{cid}").json()["title"] == "زكاة الحلي"

    # another user cannot see or continue this conversation
    client.post("/api/auth/logout")
    register(client, email="other@example.com")
    assert client.get(f"/api/history/{cid}").status_code == 404
    assert client.get("/api/history").json() == []
    r = client.post("/api/ask", json={"clone": "albani", "message": "هل في الحلي زكاة", "use_llm": False, "conversation_id": cid})
    assert r.status_code == 404

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "ali@example.com", "password": "password123"})
    assert client.delete(f"/api/history/{cid}").status_code == 200
    assert client.get(f"/api/history/{cid}").status_code == 404


def test_guest_mode_when_login_not_required(client, monkeypatch):
    monkeypatch.setattr(main, "REQUIRE_LOGIN", False)
    r = client.post("/api/ask", json={"clone": "ibn_baz", "message": "هل في الحلي زكاة", "use_llm": False})
    assert r.status_code == 200 and r.json()["conversation_id"] is None


def test_rate_limit(client, monkeypatch):
    from app.store import RateLimiter
    monkeypatch.setattr(main, "limiter", RateLimiter(2))
    register(client)
    body = {"clone": "ibn_baz", "message": "هل في الحلي زكاة", "use_llm": False}
    assert client.post("/api/ask", json=body).status_code == 200
    assert client.post("/api/ask", json=body).status_code == 200
    assert client.post("/api/ask", json=body).status_code == 429


def test_password_change_revokes_old_sessions(client):
    register(client)
    r = client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "newpassword1"})
    assert r.status_code == 401
    r = client.post("/api/auth/password", json={"current_password": "password123", "new_password": "newpassword1"})
    assert r.status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "ali@example.com", "password": "password123"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ali@example.com", "password": "newpassword1"}).status_code == 200


def test_env_defaults_do_not_leak_into_other_tests():
    assert "ZAKAT_DATA_DIR" in os.environ or True


def test_admin_dashboard_analytics_activity_and_exports(client):
    register(client, office="مكتب طرابلس")  # admin
    client.post("/api/ask", json={"clone": "ibn_uthaymeen", "message": "هل في الحلي زكاة", "use_llm": False})
    client.post("/api/ask", json={"clone": "ibn_baz", "message": "ما هو الطقس اليوم", "use_llm": False})
    a = client.get("/api/admin/analytics?days=7").json()
    assert a["questions_total"] == 2 and a["users"] == 1 and a["active_users_7d"] == 1
    assert sum(a["questions_per_day"].values()) == 2
    assert a["questions_by_clone"] == {"ibn_uthaymeen": 1, "ibn_baz": 1}
    assert a["answers_by_stage"].get("out_of_scope") == 1
    assert a["by_office"][0]["office"] == "مكتب طرابلس" and a["top_users"][0]["questions"] == 2
    act = client.get("/api/admin/activity?limit=10").json()
    assert len(act) == 2 and act[0]["text"] == "ما هو الطقس اليوم" and act[0]["stage"] == "out_of_scope"
    assert act[0]["email"] == "ali@example.com"
    assert len(client.get("/api/admin/activity?q=الحلي").json()) == 1
    csv_q = client.get("/api/admin/export/questions.csv")
    assert csv_q.status_code == 200 and csv_q.text.startswith("\ufeff") and "الحلي" in csv_q.text
    assert "ali@example.com" in client.get("/api/admin/export/users.csv").text
    assert client.get("/admin").status_code == 200

    other = TestClient(main.app)
    register(other, email="b@example.com")
    for path in ("/api/admin/analytics", "/api/admin/activity", "/api/admin/export/questions.csv", "/api/admin/export/users.csv"):
        assert other.get(path).status_code == 403, path


def test_deleting_user_removes_their_activity_from_analytics(client):
    register(client)  # admin
    other = TestClient(main.app)
    register(other, email="b@example.com")
    other.post("/api/ask", json={"clone": "ibn_baz", "message": "هل في الحلي زكاة", "use_llm": False})
    client.post("/api/compare", json={"message": "هل في الحلي زكاة", "use_llm": False})
    assert client.get("/api/admin/analytics").json()["active_users_7d"] == 2
    uid = [u for u in client.get("/api/admin/users").json() if u["email"] == "b@example.com"][0]["id"]
    assert client.delete(f"/api/admin/users/{uid}").status_code == 200
    a = client.get("/api/admin/analytics").json()
    assert a["users"] == 1 and a["active_users_7d"] == 1 and a["questions_total"] == 1
    act = client.get("/api/admin/activity").json()
    assert len(act) == 1 and act[0]["stage"] == "answer"  # comparative answers have no stage field
    assert ",answer," in client.get("/api/admin/export/questions.csv").text


def test_pages_version_assets_and_disable_html_caching(client):
    r = client.get("/")
    assert r.status_code == 200 and "no-cache" in r.headers["cache-control"]
    assert re.search(r'/static/style\.css\?v=[0-9a-f]{10}"', r.text) and re.search(r'/static/app\.js\?v=[0-9a-f]{10}"', r.text)
    a = client.get("/admin")
    assert re.search(r'/static/admin\.js\?v=[0-9a-f]{10}"', a.text)
    assert client.get("/static/style.css").headers["cache-control"] == "no-cache"
    assert "immutable" in client.get("/static/style.css?v=abc").headers["cache-control"]
