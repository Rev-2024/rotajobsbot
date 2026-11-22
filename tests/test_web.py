from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Account, Application, InboxMessage, Job
from app.web import normalize_phone

ADMIN = "9000000000"  # normalises to 919000000000
ROTARIAN = "9111111111"
ROTARACTOR = "9222222222"
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


@pytest.fixture(autouse=True)
def web_settings(monkeypatch):
    monkeypatch.setattr(settings, "admin_phones", {"91" + ADMIN})
    monkeypatch.setattr(settings, "invite_code", "")


def new_client():
    return TestClient(app)  # each client has its own cookie jar = its own browser


def signup(client, phone, password="secret1", invite=""):
    return client.post("/api/signup", json={"phone": phone, "password": password, "invite_code": invite})


def chat(client, text="", rid=""):
    r = client.post("/api/chat", json={"text": text, "reply_id": rid})
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def inbox(client):
    r = client.get("/api/inbox")
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def bodies(msgs):
    return "\n".join(m["body"] for m in msgs)


def register(client, phone, role, name, club, city):
    signup(client, phone)
    chat(client, "hi")
    chat(client, rid=f"role_{role}")
    chat(client, name)
    chat(client, club)
    return chat(client, city)


def approve(admin_client, rotarian_phone):
    return chat(admin_client, f"approve 91{rotarian_phone}")


def post_job(client, title="Junior Accountant"):
    chat(client, rid="post_job")
    chat(client, title)
    chat(client, "Sharma & Co")
    chat(client, "Bengaluru")
    chat(client, rid="type_ft")
    chat(client, "Fresher")
    chat(client, "₹20,000/month")
    chat(client, "30 Nov 2026")
    return chat(client, rid="post_yes")


# ---------------------------------------------------------------- phone numbers
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("98765 43210", "919876543210"),
        ("+91 98765-43210", "919876543210"),
        ("09876543210", "919876543210"),
        ("919876543210", "919876543210"),
        ("12345", None),
        ("", None),
        ("abc", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


# ---------------------------------------------------------------- accounts
def test_signup_login_logout_cycle():
    c = new_client()
    assert c.get("/api/me").status_code == 401
    r = signup(c, "98765 43210")
    assert r.status_code == 200 and r.json()["phone"] == "919876543210"
    assert c.get("/api/me").json()["phone"] == "919876543210"

    assert c.post("/api/logout").status_code == 200
    assert c.get("/api/me").status_code == 401

    assert c.post("/api/login", json={"phone": "9876543210", "password": "wrong!!"}).status_code == 401
    ok = c.post("/api/login", json={"phone": "+91 9876543210", "password": "secret1"})
    assert ok.status_code == 200
    assert c.get("/api/me").status_code == 200


def test_password_is_stored_hashed():
    signup(new_client(), ROTARIAN, password="secret1")
    with SessionLocal() as db:
        acct = db.scalar(select(Account))
        assert acct.password_hash.startswith("pbkdf2$")
        assert "secret1" not in acct.password_hash


def test_signup_validation():
    c = new_client()
    assert signup(c, "123").status_code == 400
    assert signup(c, ROTARIAN, password="abc").status_code == 400
    assert signup(c, ROTARIAN).status_code == 200
    assert signup(new_client(), ROTARIAN).status_code == 409  # already registered


def test_wrong_password_and_unknown_number_look_identical():
    signup(new_client(), ROTARIAN)
    a = new_client().post("/api/login", json={"phone": ROTARIAN, "password": "nope-nope"})
    b = new_client().post("/api/login", json={"phone": "9999999999", "password": "nope-nope"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_login_is_locked_after_repeated_failures():
    signup(new_client(), ROTARIAN)
    c = new_client()
    for _ in range(5):
        assert c.post("/api/login", json={"phone": ROTARIAN, "password": "bad-guess"}).status_code == 401
    # even the right password is refused while locked
    assert c.post("/api/login", json={"phone": ROTARIAN, "password": "secret1"}).status_code == 429


def test_invite_code_required_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "invite_code", "ROTARY2026")
    assert new_client().get("/api/config").json() == {"invite_required": True}
    assert signup(new_client(), ROTARACTOR).status_code == 403
    assert signup(new_client(), ROTARACTOR, invite="wrong").status_code == 403
    assert signup(new_client(), ROTARACTOR, invite=" ROTARY2026 ").status_code == 200
    assert signup(new_client(), ADMIN).status_code == 200  # coordinators don't need the code


def test_chat_and_inbox_require_sign_in():
    c = new_client()
    assert c.post("/api/chat", json={"text": "hi"}).status_code == 401
    assert c.get("/api/inbox").status_code == 401


# ---------------------------------------------------------------- chat journeys
def test_first_message_shows_welcome_with_role_buttons():
    c = new_client()
    signup(c, ROTARACTOR)
    msgs = chat(c, "hi")
    assert msgs[0]["kind"] == "buttons"
    assert {b["id"] for b in msgs[0]["buttons"]} == {"role_rotarian", "role_rotaractor"}
    assert "WhatsApp" not in msgs[0]["body"]


def test_approval_flow_with_notifications():
    rot, admin = new_client(), new_client()
    msgs = register(rot, ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    assert "verify" in bodies(msgs)

    # The coordinator signs up afterwards and still sees the approval request waiting in their inbox.
    signup(admin, ADMIN)
    waiting = inbox(admin)
    assert any("approve 91" + ROTARIAN in m["body"] for m in waiting)
    assert inbox(admin) == []  # each notification is delivered once

    assert "approved" in bodies(approve(admin, ROTARIAN))
    assert "verified" in bodies(inbox(rot))

    menu = chat(rot, "hi")
    assert "post_job" in {r["id"] for r in menu[0]["rows"]}


def test_full_journey_over_the_web():
    rot, act, admin = new_client(), new_client(), new_client()
    register(rot, ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    signup(admin, ADMIN)
    approve(admin, ROTARIAN)
    inbox(rot)  # clear the "verified" notice
    post_job(rot)

    register(act, ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    listing = chat(act, rid="browse")
    assert listing[0]["rows"][0]["id"] == "job_1"
    assert "Junior Accountant" in chat(act, rid="job_1")[0]["body"]

    chat(act, rid="apply_1")
    assert "Application sent" in bodies(chat(act, "I know Tally and GST"))

    # The Rotarian gets the application in their inbox, with decision buttons.
    alerts = inbox(rot)
    assert len(alerts) == 1 and "Anita Rao" in alerts[0]["body"]
    assert {b["id"] for b in alerts[0]["buttons"]} == {"short_1", "rej_1"}

    own = chat(rot, rid="short_1")
    assert "shortlisted" in bodies(own)
    assert "shortlisted" in bodies(inbox(act))
    assert inbox(act) == []

    with SessionLocal() as db:
        assert db.get(Application, 1).status == "shortlisted"
        assert db.get(Job, 1).status == "open"


def test_users_only_ever_act_as_themselves():
    rot, other = new_client(), new_client()
    register(rot, ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    signup(other, ROTARACTOR)
    # A forged "phone" in the body is ignored: identity comes only from the signed session cookie.
    r = other.post("/api/chat", json={"text": "hi", "reply_id": "", "phone": "91" + ROTARIAN})
    assert r.status_code == 200
    assert r.json()["messages"][0]["kind"] == "buttons"  # welcome for a new user, not Ravi's menu
    # A tampered session cookie is rejected.
    other.cookies.set("rjc_session", "forged.value.here")
    assert other.get("/api/me").status_code == 401


def test_other_users_notifications_are_not_visible():
    a, b = new_client(), new_client()
    signup(a, ROTARIAN)
    signup(b, ROTARACTOR)
    with SessionLocal() as db:
        db.add(InboxMessage(phone="91" + ROTARIAN, payload={"kind": "text", "body": "secret", "buttons": [], "rows": [], "list_button": ""}))
        db.commit()
    assert inbox(b) == []
    assert bodies(inbox(a)) == "secret"


# ---------------------------------------------------------------- static page
def test_page_and_assets_are_served_with_security_headers():
    c = new_client()
    page = c.get("/")
    assert page.status_code == 200 and "Rotary Jobs Connect" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/style.css").status_code == 200


def test_client_code_never_inserts_html():
    js = (STATIC / "app.js").read_text()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert banned not in js, f"app.js must not use {banned}"
    assert "<script>" not in (STATIC / "index.html").read_text().replace('<script src=', "")
