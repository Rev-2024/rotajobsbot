import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import whatsapp
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.messages import Out, btns, menu_list, say
from app.models import ProcessedMessage

SECRET = "s3cret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "wa_app_secret", SECRET)
    monkeypatch.setattr(settings, "wa_verify_token", "verify-me")
    monkeypatch.setattr(settings, "admin_token", "adm")
    monkeypatch.setattr(settings, "admin_phones", set())
    sent: list[Out] = []
    monkeypatch.setattr(whatsapp, "send", lambda out: sent.append(out) or True)
    with TestClient(app) as c:
        c.sent = sent
        yield c


def signed(body: dict, secret: str = SECRET):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def text_event(msg_id="wamid.1", phone="919111111111", body="hi"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "contacts": [{"profile": {"name": "Test"}, "wa_id": phone}],
            "messages": [{"from": phone, "id": msg_id, "type": "text", "text": {"body": body}}],
        }}]}],
    }


def test_verification_handshake(client):
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"})
    assert bad.status_code == 403


def test_rejects_bad_or_missing_signature(client):
    raw, _ = signed(text_event())
    assert client.post("/webhook", content=raw).status_code == 403
    raw, headers = signed(text_event(), secret="wrong")
    assert client.post("/webhook", content=raw, headers=headers).status_code == 403
    assert client.sent == []


def test_valid_message_gets_a_reply(client):
    raw, headers = signed(text_event())
    assert client.post("/webhook", content=raw, headers=headers).status_code == 200
    assert len(client.sent) == 1
    assert client.sent[0].to == "919111111111" and client.sent[0].kind == "buttons"


def test_duplicate_delivery_is_processed_once(client):
    raw, headers = signed(text_event(msg_id="wamid.dup"))
    client.post("/webhook", content=raw, headers=headers)
    client.post("/webhook", content=raw, headers=headers)
    assert len(client.sent) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ProcessedMessage)) == 1


def test_interactive_reply_is_parsed(client):
    event = text_event(msg_id="wamid.a")
    client.post("/webhook", content=signed(event)[0], headers=signed(event)[1])  # user -> role buttons
    tap = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [{
        "from": "919111111111", "id": "wamid.b", "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": "role_rotaractor", "title": "I'm a Rotaractor"}},
    }]}}]}]}
    raw, headers = signed(tap)
    client.post("/webhook", content=raw, headers=headers)
    assert "full name" in client.sent[-1].body


def test_status_callbacks_are_ignored(client):
    status = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "delivered"}]}}]}]}
    raw, headers = signed(status)
    assert client.post("/webhook", content=raw, headers=headers).status_code == 200
    assert client.sent == []


def test_admin_endpoints_require_token(client):
    assert client.get("/admin/users").status_code == 401
    assert client.get("/admin/users", headers={"X-Admin-Token": "adm"}).status_code == 200


# ---------------------------------------------------------------- payload limits
def test_payloads_respect_whatsapp_limits():
    b = whatsapp.to_payload(btns("1", "x" * 2000, [(f"id{i}", "T" * 50) for i in range(5)]))
    buttons = b["interactive"]["action"]["buttons"]
    assert len(buttons) == 3 and all(len(x["reply"]["title"]) <= 20 for x in buttons)
    assert len(b["interactive"]["body"]["text"]) <= 1024

    rows = [{"id": f"r{i}", "title": "T" * 50, "description": "D" * 200} for i in range(15)]
    l = whatsapp.to_payload(menu_list("1", "body", "L" * 40, rows))
    out_rows = l["interactive"]["action"]["sections"][0]["rows"]
    assert len(out_rows) == 10
    assert all(len(r["title"]) <= 24 and len(r["description"]) <= 72 for r in out_rows)
    assert len(l["interactive"]["action"]["button"]) <= 20

    t = whatsapp.to_payload(say("1", "hello"))
    assert t["type"] == "text" and t["to"] == "1"
