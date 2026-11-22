"""Website chat API: sign-up / sign-in, the chat endpoint, and the notification inbox.

The conversation itself is still `flows.handle()`; this module only adds identity (who is typing)
and delivery (how replies and notifications reach the browser).
"""
import hashlib
import hmac
import os
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .flows import handle
from .messages import Incoming, Out
from .models import Account, InboxMessage

router = APIRouter(prefix="/api")

PBKDF2_ROUNDS = 200_000
MAX_FAILS = 5  # wrong passwords allowed...
FAIL_WINDOW = 15 * 60  # ...within this many seconds, per (IP, number)
FAILS: dict[str, list[float]] = {}  # in-memory; fine for one server process


# ---------------------------------------------------------------- passwords & phone numbers
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


DUMMY_HASH = hash_password("not-a-real-password")  # so unknown numbers cost the same time as wrong passwords


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def normalize_phone(raw: str) -> str | None:
    """Digits only, with country code. A bare 10-digit number is treated as Indian (+91)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    return digits if 11 <= len(digits) <= 15 else None


# ---------------------------------------------------------------- request models
class SignupIn(BaseModel):
    phone: str = Field(max_length=30)
    password: str = Field(max_length=100)
    invite_code: str = Field("", max_length=100)


class LoginIn(BaseModel):
    phone: str = Field(max_length=30)
    password: str = Field(max_length=100)


class ChatIn(BaseModel):
    text: str = Field("", max_length=1000)
    reply_id: str = Field("", max_length=200)


def current_phone(request: Request) -> str:
    phone = request.session.get("phone")
    if not phone:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return phone


def serialize(out: Out) -> dict:
    return {
        "kind": out.kind,
        "body": out.body,
        "buttons": [{"id": i, "title": t} for i, t in out.buttons],
        "rows": out.rows,
        "list_button": out.list_button,
    }


# ---------------------------------------------------------------- auth
@router.get("/config")
def config() -> dict:
    return {"invite_required": bool(settings.invite_code)}


@router.post("/signup")
def signup(body: SignupIn, request: Request) -> dict:
    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, "Enter a valid mobile number (10 digits, or with country code).")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if settings.invite_code and phone not in settings.admin_phones:
        if not safe_equal(body.invite_code.strip(), settings.invite_code):
            raise HTTPException(403, "That club invite code isn't right.")
    with SessionLocal() as db:
        if db.get(Account, phone):
            raise HTTPException(409, "This number already has an account. Please sign in.")
        db.add(Account(phone=phone, password_hash=hash_password(body.password)))
        db.commit()
    request.session.clear()
    request.session["phone"] = phone
    return {"phone": phone}


@router.post("/login")
def login(body: LoginIn, request: Request) -> dict:
    phone = normalize_phone(body.phone) or ""
    key = f"{request.client.host if request.client else '-'}:{phone or body.phone[:30]}"
    now = time.time()
    recent = [t for t in FAILS.get(key, []) if now - t < FAIL_WINDOW]
    if len(recent) >= MAX_FAILS:
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")

    with SessionLocal() as db:
        account = db.get(Account, phone) if phone else None
    ok = check_password(body.password, account.password_hash if account else DUMMY_HASH) and account is not None
    if not ok:
        FAILS[key] = recent + [now]
        raise HTTPException(401, "Wrong mobile number or password.")

    FAILS.pop(key, None)
    request.session.clear()
    request.session["phone"] = phone
    return {"phone": phone}


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(phone: str = Depends(current_phone)) -> dict:
    return {"phone": phone}


# ---------------------------------------------------------------- chat
@router.post("/chat")
def chat(body: ChatIn, phone: str = Depends(current_phone)) -> dict:
    """Run one turn. Replies for this user are returned; messages for others go to their inbox."""
    with SessionLocal() as db:
        outs = handle(db, Incoming(phone=phone, text=body.text, reply_id=body.reply_id), settings.admin_phones)
        mine = [serialize(o) for o in outs if o.to == phone]
        for o in outs:
            if o.to != phone:
                db.add(InboxMessage(phone=o.to, payload=serialize(o)))
        db.commit()
    return {"messages": mine}


@router.get("/inbox")
def inbox(phone: str = Depends(current_phone)) -> dict:
    """Notifications that arrived while the user was away. Each is delivered once."""
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(InboxMessage)
                .where(InboxMessage.phone == phone, InboxMessage.delivered.is_(False))
                .order_by(InboxMessage.id)
                .limit(50)
            )
        )
        for row in rows:
            row.delivered = True
        db.commit()
        return {"messages": [r.payload for r in rows]}
