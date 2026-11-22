"""Conversation logic.

`handle()` is pure with respect to WhatsApp: it takes a DB session and one incoming
message, updates the database, and returns the messages to send. That makes every
flow testable without touching the network.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .messages import Incoming, Out, btns, menu_list, say
from .models import Application, ChatSession, Job, User

RESET_WORDS = {"hi", "hii", "hello", "hey", "menu", "start", "0", "cancel", "namaste"}
REG_STATES = {"reg_role", "reg_name", "reg_club", "reg_city"}
PAGE_SIZE = 9  # 9 jobs + a "More" row = WhatsApp's 10-row list limit
JOB_TYPES = {"type_ft": "Full-time", "type_pt": "Part-time", "type_int": "Internship"}
STATUS_LABEL = {"applied": "Applied", "shortlisted": "⭐ Shortlisted", "rejected": "Not selected"}

WELCOME = (
    "👋 Welcome to *Rotary Jobs Connect*!\n"
    "Rotarians share job openings and Rotaractors apply — all in this chat.\n\n"
    "First, tell me who you are:"
)
PENDING_MSG = (
    "⏳ Your Rotarian account is waiting for verification by the club coordinator. "
    "Send *hi* here again later to check."
)
REJECTED_MSG = "Sorry, your Rotarian account could not be verified. Please contact your club coordinator."


# ---------------------------------------------------------------- helpers
def clip(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def first_name(user: User) -> str:
    return user.name.split()[0] if user.name else "there"


def num(reply_id: str) -> int | None:
    try:
        return int(reply_id.split("_", 1)[1])
    except (IndexError, ValueError):
        return None


def get_session(db: Session, phone: str) -> ChatSession:
    sess = db.get(ChatSession, phone)
    if sess is None:
        sess = ChatSession(phone=phone, state="", data={})
        db.add(sess)
    return sess


def go(sess: ChatSession, state: str, **data) -> None:
    sess.state = state
    sess.data = {**(sess.data or {}), **data}  # reassign so the JSON change is tracked


def clear(sess: ChatSession) -> None:
    sess.state = ""
    sess.data = {}


def job_text(job: Job) -> str:
    return (
        f"*{job.title}*\n"
        f"🏢 {job.company}\n"
        f"📍 {job.location}\n"
        f"🕒 {job.job_type}\n"
        f"🎓 Experience: {job.experience}\n"
        f"💰 Pay: {job.salary}\n"
        f"⏳ Apply by: {job.deadline}\n"
        f"🤝 Shared by {job.poster.name} ({job.poster.club})"
    )


# ---------------------------------------------------------------- entry point
def handle(db: Session, inc: Incoming, admin_phones: Iterable[str] = ()) -> list[Out]:
    phone = inc.phone
    low = inc.text.strip().lower()
    sess = get_session(db, phone)
    admin_phones = set(admin_phones)

    if phone in admin_phones:
        result = admin_command(db, phone, low)
        if result is not None:
            return result

    user = db.scalar(select(User).where(User.phone == phone))
    if user is None:
        return registration(db, sess, inc, low, admin_phones)

    # Verification gate comes first so unverified users never see the menu.
    if user.role == "rotarian" and user.status == "pending":
        return [say(phone, PENDING_MSG)]
    if user.status == "rejected":
        return [say(phone, REJECTED_MSG)]

    if low in RESET_WORDS and not inc.reply_id:
        clear(sess)
        return home(user)

    if inc.reply_id:
        result = on_reply(db, sess, user, inc)
    elif sess.state:
        result = on_text(db, sess, user, inc)
    else:
        result = None
    return result if result is not None else home(user)


# ---------------------------------------------------------------- registration
def registration(db: Session, sess: ChatSession, inc: Incoming, low: str, admin_phones) -> list[Out]:
    phone = inc.phone
    state = sess.state

    role_buttons = [("role_rotarian", "I'm a Rotarian"), ("role_rotaractor", "I'm a Rotaractor")]

    if low in RESET_WORDS or state not in REG_STATES:
        go(sess, "reg_role")
        return [btns(phone, WELCOME, role_buttons)]

    if state == "reg_role":
        if inc.reply_id in ("role_rotarian", "role_rotaractor"):
            go(sess, "reg_name", role=inc.reply_id.split("_", 1)[1])
            return [say(phone, "Great! What's your full name?")]
        return [btns(phone, "Please tap one of the options:", role_buttons)]

    if state == "reg_name":
        name = clip(inc.text, 100)
        if len(name) < 2:
            return [say(phone, "Please type your full name.")]
        go(sess, "reg_club", name=name)
        club_kind = "Rotary" if sess.data["role"] == "rotarian" else "Rotaract"
        return [say(phone, f"Which {club_kind} club are you part of? (e.g. Rotary Club of Bengaluru Central)")]

    if state == "reg_club":
        club = clip(inc.text, 100)
        if len(club) < 2:
            return [say(phone, "Please type your club name.")]
        go(sess, "reg_city", club=club)
        return [say(phone, "Which city are you based in?")]

    # state == "reg_city"
    city = clip(inc.text, 100)
    if len(city) < 2:
        return [say(phone, "Please type your city.")]
    d = sess.data
    is_rotarian = d["role"] == "rotarian"
    user = User(
        phone=phone,
        role=d["role"],
        name=d["name"],
        club=d["club"],
        city=city,
        status="pending" if is_rotarian else "approved",
    )
    db.add(user)
    clear(sess)

    if is_rotarian:
        outs = [
            say(
                phone,
                f"Thanks, {first_name(user)}! 🙏 A club coordinator will verify your Rotarian "
                "account so only genuine members can post jobs. Send *hi* later to check.",
            )
        ]
        for admin in admin_phones:
            outs.append(
                say(
                    admin,
                    f"🆕 Rotarian awaiting approval:\n{user.name}, {user.club}, {user.city}\n\n"
                    f"Reply: *approve {phone}* or *reject {phone}*",
                )
            )
        return outs
    return [say(phone, f"Welcome, {first_name(user)}! 🎉")] + home(user)


# ---------------------------------------------------------------- menus
def home(user: User) -> list[Out]:
    if user.role == "rotarian":
        rows = [
            {"id": "post_job", "title": "Post a job", "description": "Share an opening with Rotaractors"},
            {"id": "my_jobs", "title": "My posted jobs", "description": "See applicants, close jobs"},
            {"id": "profile", "title": "My profile", "description": "Your details"},
        ]
    else:
        rows = [
            {"id": "browse", "title": "Browse jobs", "description": "Openings shared by Rotarians"},
            {"id": "my_apps", "title": "My applications", "description": "Track where you applied"},
            {"id": "profile", "title": "My profile", "description": "Your details"},
        ]
    return [menu_list(user.phone, f"Hi {first_name(user)}! What would you like to do?", "Menu", rows)]


def profile(user: User) -> list[Out]:
    role = "Rotarian" if user.role == "rotarian" else "Rotaractor"
    body = f"👤 *{user.name}*\n{role} • {user.club}\n📍 {user.city}\n📞 +{user.phone}\n\nType *menu* to go back."
    return [say(user.phone, body)]


# ---------------------------------------------------------------- button / list taps
def on_reply(db: Session, sess: ChatSession, user: User, inc: Incoming) -> list[Out] | None:
    rid = inc.reply_id
    phone = user.phone
    is_rotarian = user.role == "rotarian"

    if rid == "profile":
        return profile(user)

    # Rotarian actions
    if is_rotarian:
        if rid == "post_job":
            go(sess, "post_title", job={})
            return [say(phone, "Let's post a job. What's the *job title*? (e.g. Junior Accountant)\n\nType *cancel* anytime to stop.")]
        if sess.state == "post_type" and rid in JOB_TYPES:
            go(sess, "post_exp", job={**sess.data["job"], "job_type": JOB_TYPES[rid]})
            return [say(phone, "What *experience* is needed? (e.g. Fresher, 2 years)")]
        if sess.state == "post_confirm" and rid in ("post_yes", "post_no"):
            return finish_post(db, sess, user, rid == "post_yes")
        if rid == "my_jobs":
            return my_jobs(db, user)
        if rid.startswith("myjob_"):
            return job_manage(db, user, num(rid))
        if rid.startswith("applicants_"):
            return applicants(db, user, num(rid))
        if rid.startswith("app_"):
            return application_detail(db, user, num(rid))
        if rid.startswith("close_"):
            return close_job(db, user, num(rid))
        if rid.startswith(("short_", "rej_")):
            return decide(db, user, num(rid), shortlist=rid.startswith("short_"))
        return None

    # Rotaractor actions
    if rid == "browse":
        return browse(db, user, 0)
    if rid.startswith("more_"):
        return browse(db, user, num(rid) or 0)
    if rid.startswith("job_"):
        return job_detail(db, user, num(rid))
    if rid.startswith("apply_"):
        return start_apply(db, sess, user, num(rid))
    if rid == "my_apps":
        return my_apps(db, user)
    return None


# ---------------------------------------------------------------- free-text answers
def on_text(db: Session, sess: ChatSession, user: User, inc: Incoming) -> list[Out] | None:
    phone = user.phone
    state = sess.state
    answer = inc.text.strip()

    if user.role == "rotarian" and state.startswith("post_"):
        job = dict(sess.data.get("job", {}))

        if state == "post_title":
            if len(answer) < 3:
                return [say(phone, "Please type a job title (at least 3 characters).")]
            go(sess, "post_company", job={**job, "title": clip(answer, 150)})
            return [say(phone, "Which *company / organisation* is hiring?")]
        if state == "post_company":
            if len(answer) < 2:
                return [say(phone, "Please type the company name.")]
            go(sess, "post_location", job={**job, "company": clip(answer, 150)})
            return [say(phone, "Where is the job? (city, or *Remote*)")]
        if state == "post_location":
            if len(answer) < 2:
                return [say(phone, "Please type the job location.")]
            go(sess, "post_type", job={**job, "location": clip(answer, 100)})
            return [btns(phone, "What *type* of job is it?", [(k, v) for k, v in JOB_TYPES.items()])]
        if state == "post_type":
            return [btns(phone, "Please tap one of the options:", [(k, v) for k, v in JOB_TYPES.items()])]
        if state == "post_exp":
            go(sess, "post_salary", job={**job, "experience": clip(answer, 100) or "Not specified"})
            return [say(phone, "What's the *salary / stipend*? (e.g. ₹20,000/month, or *Negotiable*)")]
        if state == "post_salary":
            go(sess, "post_deadline", job={**job, "salary": clip(answer, 100) or "Not specified"})
            return [say(phone, "What's the *last date to apply*? (e.g. 30 Nov 2026)")]
        if state == "post_deadline":
            job = {**job, "deadline": clip(answer, 50) or "Open until filled"}
            go(sess, "post_confirm", job=job)
            summary = (
                "Please confirm your job post:\n\n"
                f"*{job['title']}*\n🏢 {job['company']}\n📍 {job['location']}\n🕒 {job['job_type']}\n"
                f"🎓 {job['experience']}\n💰 {job['salary']}\n⏳ Apply by: {job['deadline']}"
            )
            return [btns(phone, summary, [("post_yes", "Post job"), ("post_no", "Cancel")])]
        if state == "post_confirm":
            return [btns(phone, "Post this job?", [("post_yes", "Post job"), ("post_no", "Cancel")])]

    if user.role == "rotaractor" and state == "apply_note":
        return finish_apply(db, sess, user, answer)

    return None


# ---------------------------------------------------------------- Rotarian: jobs
def finish_post(db: Session, sess: ChatSession, user: User, confirmed: bool) -> list[Out]:
    if not confirmed:
        clear(sess)
        return [say(user.phone, "Cancelled. Type *menu* to go back.")]
    d = sess.data["job"]
    job = Job(
        poster_id=user.id,
        title=d["title"],
        company=d["company"],
        location=d["location"],
        job_type=d["job_type"],
        experience=d["experience"],
        salary=d["salary"],
        deadline=d["deadline"],
    )
    db.add(job)
    clear(sess)
    return [
        say(
            user.phone,
            "✅ Your job is live! Rotaractors can find it under *Browse jobs*.\n"
            "Check applicants any time under *My posted jobs*. Type *menu* to go back.",
        )
    ]


def my_jobs(db: Session, user: User) -> list[Out]:
    jobs = list(db.scalars(select(Job).where(Job.poster_id == user.id).order_by(Job.id.desc()).limit(10)))
    if not jobs:
        return [say(user.phone, "You haven't posted any jobs yet. Type *menu* and choose *Post a job*.")]
    rows = []
    for j in jobs:
        count = db.scalar(select(func.count()).select_from(Application).where(Application.job_id == j.id))
        rows.append(
            {
                "id": f"myjob_{j.id}",
                "title": clip(j.title, 24),
                "description": f"{j.status.capitalize()} • {count} applicant(s)",
            }
        )
    return [menu_list(user.phone, "Your latest jobs — tap one to manage it:", "My jobs", rows)]


def _owned_job(db: Session, user: User, job_id: int | None) -> Job | None:
    job = db.get(Job, job_id) if job_id else None
    return job if job and job.poster_id == user.id else None


def job_manage(db: Session, user: User, job_id: int | None) -> list[Out]:
    job = _owned_job(db, user, job_id)
    if not job:
        return [say(user.phone, "I couldn't find that job.")]
    count = db.scalar(select(func.count()).select_from(Application).where(Application.job_id == job.id))
    buttons = [(f"applicants_{job.id}", "View applicants")]
    if job.status == "open":
        buttons.append((f"close_{job.id}", "Close job"))
    buttons.append(("my_jobs", "Back"))
    return [btns(user.phone, f"{job_text(job)}\n\nStatus: {job.status} • {count} applicant(s)", buttons)]


def close_job(db: Session, user: User, job_id: int | None) -> list[Out]:
    job = _owned_job(db, user, job_id)
    if not job:
        return [say(user.phone, "I couldn't find that job.")]
    job.status = "closed"
    return [say(user.phone, f"🔒 *{job.title}* is now closed. Type *menu* to go back.")]


def applicants(db: Session, user: User, job_id: int | None) -> list[Out]:
    job = _owned_job(db, user, job_id)
    if not job:
        return [say(user.phone, "I couldn't find that job.")]
    apps = list(
        db.scalars(select(Application).where(Application.job_id == job.id).order_by(Application.id.desc()).limit(10))
    )
    if not apps:
        return [say(user.phone, f"No applications yet for *{job.title}*. Type *menu* to go back.")]
    rows = [
        {
            "id": f"app_{a.id}",
            "title": clip(a.applicant.name, 24),
            "description": clip(f"{STATUS_LABEL[a.status]} • {a.applicant.club}, {a.applicant.city}", 72),
        }
        for a in apps
    ]
    return [menu_list(user.phone, f"Applicants for *{job.title}* (latest 10):", "Applicants", rows)]


def application_detail(db: Session, user: User, app_id: int | None) -> list[Out]:
    app = db.get(Application, app_id) if app_id else None
    if not app or app.job.poster_id != user.id:
        return [say(user.phone, "I couldn't find that application.")]
    return [btns(user.phone, application_text(app), [
        (f"short_{app.id}", "Shortlist"),
        (f"rej_{app.id}", "Reject"),
        (f"applicants_{app.job_id}", "Back"),
    ])]


def application_text(app: Application) -> str:
    a = app.applicant
    note = f"\n📝 {app.note}" if app.note else ""
    return (
        f"📩 Application for *{app.job.title}*\n\n"
        f"👤 {a.name}\n🏛️ {a.club}\n📍 {a.city}\n"
        f"📞 https://wa.me/{a.phone}{note}\n\n"
        f"Status: {STATUS_LABEL[app.status]}"
    )


def decide(db: Session, user: User, app_id: int | None, shortlist: bool) -> list[Out]:
    app = db.get(Application, app_id) if app_id else None
    if not app or app.job.poster_id != user.id:  # only the poster may decide
        return [say(user.phone, "I couldn't find that application.")]
    app.status = "shortlisted" if shortlist else "rejected"
    job = app.job
    if shortlist:
        to_applicant = (
            f"🎉 Good news! You've been *shortlisted* for *{job.title}* at {job.company}. "
            f"{user.name} may contact you soon."
        )
    else:
        to_applicant = (
            f"Update on *{job.title}* at {job.company}: the Rotarian has decided not to move ahead "
            "this time. Thank you for applying — more openings are under *Browse jobs*."
        )
    verb = "shortlisted" if shortlist else "rejected"
    return [
        say(user.phone, f"Done — {app.applicant.name} is {verb}. Type *menu* to go back."),
        say(app.applicant.phone, to_applicant),
    ]


# ---------------------------------------------------------------- Rotaractor: browse & apply
def browse(db: Session, user: User, offset: int) -> list[Out]:
    q = select(Job).where(Job.status == "open").order_by(Job.id.desc()).offset(offset).limit(PAGE_SIZE + 1)
    jobs = list(db.scalars(q))
    if not jobs:
        return [say(user.phone, "No open jobs right now — please check back soon! Type *menu* to go back.")]
    more = len(jobs) > PAGE_SIZE
    rows = [
        {
            "id": f"job_{j.id}",
            "title": clip(j.title, 24),
            "description": clip(f"{j.company} • {j.location}", 72),
        }
        for j in jobs[:PAGE_SIZE]
    ]
    if more:
        rows.append({"id": f"more_{offset + PAGE_SIZE}", "title": "More jobs ➡️", "description": "See older openings"})
    return [menu_list(user.phone, "Open jobs — tap one to see details:", "View jobs", rows)]


def job_detail(db: Session, user: User, job_id: int | None) -> list[Out]:
    job = db.get(Job, job_id) if job_id else None
    if not job or job.status != "open":
        return [say(user.phone, "Sorry, that job is no longer available. Type *menu* to see other jobs.")]
    return [btns(user.phone, job_text(job), [(f"apply_{job.id}", "Apply now"), ("browse", "Back to jobs")])]


def start_apply(db: Session, sess: ChatSession, user: User, job_id: int | None) -> list[Out]:
    job = db.get(Job, job_id) if job_id else None
    if not job or job.status != "open":
        return [say(user.phone, "Sorry, that job is no longer available. Type *menu* to see other jobs.")]
    existing = db.scalar(
        select(Application).where(Application.job_id == job.id, Application.applicant_id == user.id)
    )
    if existing:
        return [say(user.phone, f"You've already applied for *{job.title}* ({STATUS_LABEL[existing.status]}).")]
    go(sess, "apply_note", job_id=job.id)
    return [
        say(
            user.phone,
            f"Applying for *{job.title}* at {job.company}.\n\n"
            "Send a short note to the Rotarian (your skills, availability, a link to your resume) "
            "or type *SKIP*.",
        )
    ]


def finish_apply(db: Session, sess: ChatSession, user: User, answer: str) -> list[Out]:
    job = db.get(Job, sess.data.get("job_id"))
    clear(sess)
    if not job or job.status != "open":
        return [say(user.phone, "Sorry, that job just closed. Type *menu* to see other jobs.")]
    already = db.scalar(
        select(Application).where(Application.job_id == job.id, Application.applicant_id == user.id)
    )
    if already:
        return [say(user.phone, f"You've already applied for *{job.title}*.")]
    note = "" if answer.lower() == "skip" else clip(answer, 500)
    app = Application(job_id=job.id, applicant_id=user.id, note=note)
    db.add(app)
    db.flush()  # assign app.id for the Rotarian's buttons
    notify = [
        btns(
            job.poster.phone,
            application_text(app),
            [(f"short_{app.id}", "Shortlist"), (f"rej_{app.id}", "Reject")],
        )
    ]
    confirm = say(
        user.phone,
        f"✅ Application sent for *{job.title}* at {job.company}! "
        "You'll get a message here when the Rotarian responds.\n\nType *menu* to keep browsing.",
    )
    return [confirm] + notify


def my_apps(db: Session, user: User) -> list[Out]:
    apps = list(
        db.scalars(select(Application).where(Application.applicant_id == user.id).order_by(Application.id.desc()).limit(10))
    )
    if not apps:
        return [say(user.phone, "You haven't applied anywhere yet. Type *menu* and choose *Browse jobs*.")]
    lines = [f"• *{a.job.title}* @ {a.job.company} — {STATUS_LABEL[a.status]}" for a in apps]
    return [say(user.phone, "📋 Your latest applications:\n\n" + "\n".join(lines) + "\n\nType *menu* to go back.")]


# ---------------------------------------------------------------- coordinators
def set_user_status(user: User, status: str) -> list[Out]:
    user.status = status
    if status == "approved":
        return [say(user.phone, "✅ You're verified! Send *hi* to start posting jobs.")]
    return [say(user.phone, REJECTED_MSG)]


def admin_command(db: Session, admin_phone: str, low: str) -> list[Out] | None:
    parts = low.split()
    if not parts or parts[0] not in {"pending", "approve", "reject"}:
        return None

    if parts[0] == "pending":
        users = list(db.scalars(select(User).where(User.status == "pending").limit(20)))
        if not users:
            return [say(admin_phone, "No Rotarians are waiting for approval. ✅")]
        lines = [f"• {u.name}, {u.club}, {u.city}\n  approve {u.phone}" for u in users]
        return [say(admin_phone, "Waiting for approval:\n\n" + "\n".join(lines))]

    target = re.sub(r"\D", "", parts[1]) if len(parts) > 1 else ""
    user = db.scalar(select(User).where(User.phone == target)) if target else None
    if not user:
        return [say(admin_phone, f"No user found for “{parts[1] if len(parts) > 1 else ''}”. Use: approve <number>")]
    new_status = "approved" if parts[0] == "approve" else "rejected"
    return [say(admin_phone, f"{user.name} is now {new_status}.")] + set_user_status(user, new_status)
