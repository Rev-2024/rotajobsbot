import hmac
import json
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from . import web, whatsapp
from .config import settings
from .db import SessionLocal, init_db
from .flows import handle, set_user_status
from .messages import Incoming, say
from .models import Job, ProcessedMessage, User

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if not settings.session_secret_is_set:
        log.warning("SESSION_SECRET is not set: website logins will reset every time the server restarts.")
    yield


app = FastAPI(title="Rotary Jobs Connect", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="rjc_session",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=settings.cookie_secure,
)
app.include_router(web.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={
            "Cache-Control": "no-cache",
            # All scripts and styles are same-origin files, so inline/foreign code is blocked outright.
            "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
        },
    )


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------- WhatsApp webhook
@app.get("/webhook")
def verify_webhook(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Meta calls this once when you register the webhook URL."""
    if mode == "subscribe" and settings.wa_verify_token and token == settings.wa_verify_token:
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="verification failed")


@app.post("/webhook")
async def receive(request: Request, background: BackgroundTasks) -> dict:
    raw = await request.body()
    if not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="bad signature")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    items = whatsapp.parse_webhook(body)
    if items:
        background.add_task(process, items)  # answer Meta fast; do the work after
    return {"status": "ok"}


def process(items: list[tuple[str, Incoming]]) -> None:
    for msg_id, inc in items:
        outs = []
        with SessionLocal() as db:
            if db.get(ProcessedMessage, msg_id):  # Meta retried; already handled
                continue
            db.add(ProcessedMessage(id=msg_id))
            try:
                outs = handle(db, inc, settings.admin_phones)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("failed handling message %s", msg_id)
                outs = [say(inc.phone, "Sorry, something went wrong. Please type *menu* and try again.")]
                with SessionLocal() as db2:  # still record it so a retry doesn't loop on the same crash
                    db2.add(ProcessedMessage(id=msg_id))
                    db2.commit()
        for out in outs:
            whatsapp.send(out)


# ---------------------------------------------------------------- optional REST admin
def require_admin(token: str | None) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
    if not hmac.compare_digest(token or "", settings.admin_token):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/admin/users")
def admin_users(status: str | None = None, x_admin_token: str | None = Header(None)) -> list[dict]:
    require_admin(x_admin_token)
    with SessionLocal() as db:
        q = select(User).order_by(User.id.desc())
        if status:
            q = q.where(User.status == status)
        return [
            {"phone": u.phone, "name": u.name, "role": u.role, "club": u.club, "city": u.city, "status": u.status}
            for u in db.scalars(q.limit(200))
        ]


@app.post("/admin/users/{phone}/{action}")
def admin_decide(phone: str, action: str, x_admin_token: str | None = Header(None)) -> dict:
    require_admin(x_admin_token)
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be approve or reject")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.phone == phone))
        if not user:
            raise HTTPException(status_code=404, detail="user not found")
        outs = set_user_status(user, "approved" if action == "approve" else "rejected")
        db.commit()
    for out in outs:
        whatsapp.send(out)
    return {"phone": phone, "status": user.status}


@app.get("/admin/jobs")
def admin_jobs(x_admin_token: str | None = Header(None)) -> list[dict]:
    require_admin(x_admin_token)
    with SessionLocal() as db:
        return [
            {"id": j.id, "title": j.title, "company": j.company, "status": j.status, "poster": j.poster.name}
            for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(200))
        ]
