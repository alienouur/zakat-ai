import json
from email import message_from_bytes

from chatbot.escalation import EscalationService
from chatbot.tests.conftest import valid_args


def svc(cfg, **env):
    return EscalationService(cfg)


def test_rejects_missing_contact_and_short_details(cfg):
    s = svc(cfg)
    r = s.send(valid_args(contact_phone="", contact_email=""), session_id="s1", client_ip="1.1.1.1")
    assert r["status"] == "failed" and "هاتف" in r["error"]
    r = s.send(valid_args(details="قصير"), session_id="s1", client_ip="1.1.1.1")
    assert r["status"] == "failed" and "10" in r["error"]
    r = s.send(valid_args(contact_phone="", contact_email="not-an-email"), session_id="s1", client_ip="1.1.1.1")
    assert r["status"] == "failed" and "البريد" in r["error"]
    assert s.recent() == []  # nothing logged for invalid input


def test_dry_run_writes_eml_with_fixed_recipient(cfg, tmp_path):
    s = svc(cfg)
    r = s.send(valid_args(contact_email="citizen@example.com"), session_id="s1", client_ip="1.1.1.1")
    assert r["status"] == "sent" and r["reference"].startswith("ZKT-") and len(r["reference"]) == 17
    eml = cfg.data_dir / "outbox" / f"{r['reference']}.eml"
    msg = message_from_bytes(eml.read_bytes())
    assert msg["To"] == "complaints@zakatfund.gov.ly"
    assert msg["Reply-To"] == "citizen@example.com"
    subj = str(msg["Subject"])
    body = msg.get_payload(decode=True).decode("utf-8")
    assert "تأخر صرف الإعانة" in body and r["reference"] in body and "رقم الجلسة: s1" in body and "المادة 7" in body
    assert s.recent()[0]["status"] == "sent" and s.recent()[0]["office"] == "مكتب طرابلس"


def test_recipient_cannot_be_overridden_by_model_input(cfg):
    s = svc(cfg)
    args = valid_args(to="attacker@example.com", recipient="attacker@example.com", contact_email="attacker@example.com")
    r = s.send(args, session_id="s2", client_ip="1.1.1.2")
    assert r["status"] == "sent"
    msg = message_from_bytes((cfg.data_dir / "outbox" / f"{r['reference']}.eml").read_bytes())
    assert msg["To"] == "complaints@zakatfund.gov.ly" and "attacker" not in msg["To"]


def test_per_session_and_per_ip_limits(cfg):
    s = svc(cfg)
    for _ in range(3):
        assert s.send(valid_args(), session_id="s3", client_ip="9.9.9.9")["status"] == "sent"
    r = s.send(valid_args(), session_id="s3", client_ip="9.9.9.9")
    assert r["status"] == "failed" and "الحد الأقصى" in r["error"]
    for i in range(17):  # 3 already sent from this IP -> 20 total
        assert s.send(valid_args(), session_id=f"ip-{i}", client_ip="9.9.9.9")["status"] == "sent"
    r = s.send(valid_args(), session_id="fresh", client_ip="9.9.9.9")
    assert r["status"] == "failed" and "الساعة" in r["error"]
    assert s.send(valid_args(), session_id="fresh", client_ip="8.8.8.8")["status"] == "sent"


def test_smtp_failure_is_reported_not_raised(cfg, monkeypatch):
    from chatbot.config import Settings
    monkeypatch.setenv("EMAIL_DRY_RUN", "false")
    monkeypatch.setenv("SMTP_HOST", "")
    s = EscalationService(Settings())
    r = s.send(valid_args(), session_id="s4", client_ip="1.1.1.4")
    assert r["status"] == "failed" and "تعذّر" in r["error"]
    assert s.recent()[0]["status"] == "failed"


def test_fatwa_request_goes_to_complaints_inbox_marked_for_sharia_office(cfg):
    s = EscalationService(cfg)
    r = s.send(valid_args(request_type="consultation", subject="زكاة الحلي", details="هل في حلي زوجتي زكاة؟ لم أجد الجواب في اللائحة."), session_id="c1", client_ip="1.1.1.9")
    assert r["status"] == "sent"
    msg = message_from_bytes((s.cfg.data_dir / "outbox" / f"{r['reference']}.eml").read_bytes())
    assert msg["To"] == "complaints@zakatfund.gov.ly"
    body = msg.get_payload(decode=True).decode("utf-8")
    assert "طلب فتوى" in body and "مكتب المستشارين الشرعيين" in body
