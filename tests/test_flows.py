from sqlalchemy import select

from app.db import SessionLocal
from app.flows import handle
from app.messages import Incoming
from app.models import Application, Job, User

ADMIN = "919000000000"
ROTARIAN = "919111111111"
ROTARACTOR = "919222222222"
OTHER_ROTARIAN = "919333333333"


def say(phone, text="", rid=""):
    with SessionLocal() as db:
        outs = handle(db, Incoming(phone=phone, text=text, reply_id=rid), admin_phones={ADMIN})
        db.commit()
    return outs


def to(outs, phone):
    return [o for o in outs if o.to == phone]


def bodies(outs, phone):
    return "\n".join(o.body for o in to(outs, phone))


def register(phone, role, name, club, city):
    say(phone, "hi")
    say(phone, rid=f"role_{role}")
    say(phone, name)
    say(phone, club)
    return say(phone, city)


def post_job(phone, title="Junior Accountant"):
    say(phone, rid="post_job")
    say(phone, title)
    say(phone, "Sharma & Co")
    say(phone, "Bengaluru")
    say(phone, rid="type_ft")
    say(phone, "Fresher")
    say(phone, "₹20,000/month")
    say(phone, "30 Nov 2026")
    return say(phone, rid="post_yes")


def get_user(phone):
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.phone == phone))


def approve_rotarian(phone):
    return say(ADMIN, f"approve {phone}")


# ---------------------------------------------------------------- registration
def test_new_user_gets_role_buttons():
    outs = say(ROTARIAN, "hi")
    assert outs[0].kind == "buttons"
    assert {b[0] for b in outs[0].buttons} == {"role_rotarian", "role_rotaractor"}


def test_rotarian_registration_needs_approval_and_alerts_admin():
    outs = register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    assert get_user(ROTARIAN).status == "pending"
    assert "verify" in bodies(outs, ROTARIAN)
    assert f"approve {ROTARIAN}" in bodies(outs, ADMIN)


def test_pending_rotarian_cannot_post():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    outs = say(ROTARIAN, rid="post_job")
    assert "verification" in outs[0].body
    outs = say(ROTARIAN, "hi")
    assert "verification" in outs[0].body


def test_admin_approves_and_user_is_notified():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    outs = approve_rotarian(ROTARIAN)
    assert get_user(ROTARIAN).status == "approved"
    assert "verified" in bodies(outs, ROTARIAN)
    menu = say(ROTARIAN, "hi")
    assert menu[0].kind == "list"
    assert "post_job" in {r["id"] for r in menu[0].rows}


def test_non_admin_cannot_approve():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    say(ROTARACTOR, f"approve {ROTARIAN}")  # random user typing the command
    assert get_user(ROTARIAN).status == "pending"


def test_admin_can_list_pending_and_reject():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    assert ROTARIAN in say(ADMIN, "pending")[0].body
    say(ADMIN, f"reject {ROTARIAN}")
    assert get_user(ROTARIAN).status == "rejected"
    assert "could not be verified" in say(ROTARIAN, "hi")[0].body


def test_rotaractor_is_approved_immediately():
    register(ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    assert get_user(ROTARACTOR).status == "approved"
    menu = say(ROTARACTOR, "hi")
    assert "browse" in {r["id"] for r in menu[0].rows}


# ---------------------------------------------------------------- full journey
def test_full_post_browse_apply_shortlist_journey():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    approve_rotarian(ROTARIAN)
    post_job(ROTARIAN)
    with SessionLocal() as db:
        job = db.scalar(select(Job))
        assert job.title == "Junior Accountant" and job.status == "open"
        job_id = job.id

    register(ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    listing = say(ROTARACTOR, rid="browse")
    assert listing[0].kind == "list"
    assert f"job_{job_id}" in {r["id"] for r in listing[0].rows}

    detail = say(ROTARACTOR, rid=f"job_{job_id}")
    assert "Junior Accountant" in detail[0].body
    assert f"apply_{job_id}" in {b[0] for b in detail[0].buttons}

    say(ROTARACTOR, rid=f"apply_{job_id}")
    outs = say(ROTARACTOR, "I know Tally and GST filing")
    assert "Application sent" in bodies(outs, ROTARACTOR)
    notice = to(outs, ROTARIAN)[0]
    assert "Anita Rao" in notice.body and "Tally" in notice.body
    app_id = int(notice.buttons[0][0].split("_")[1])

    # duplicate applications are refused
    again = say(ROTARACTOR, rid=f"apply_{job_id}")
    assert "already applied" in again[0].body

    # the Rotarian can also pull applicants later (no need to have seen the push message)
    lst = say(ROTARIAN, rid=f"applicants_{job_id}")
    assert f"app_{app_id}" in {r["id"] for r in lst[0].rows}
    det = say(ROTARIAN, rid=f"app_{app_id}")
    assert {b[0] for b in det[0].buttons} >= {f"short_{app_id}", f"rej_{app_id}"}

    outs = say(ROTARIAN, rid=f"short_{app_id}")
    assert "shortlisted" in bodies(outs, ROTARACTOR)
    with SessionLocal() as db:
        assert db.get(Application, app_id).status == "shortlisted"
    assert "Shortlisted" in say(ROTARACTOR, rid="my_apps")[0].body


def test_rejection_notifies_applicant():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    approve_rotarian(ROTARIAN)
    post_job(ROTARIAN)
    register(ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    say(ROTARACTOR, rid="apply_1")
    say(ROTARACTOR, "skip")
    outs = say(ROTARIAN, rid="rej_1")
    assert "not to move ahead" in bodies(outs, ROTARACTOR) or "decided not" in bodies(outs, ROTARACTOR)
    with SessionLocal() as db:
        assert db.get(Application, 1).status == "rejected"


# ---------------------------------------------------------------- permissions
def test_other_rotarian_cannot_decide_or_view_someone_elses_application():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    approve_rotarian(ROTARIAN)
    post_job(ROTARIAN)
    register(ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    say(ROTARACTOR, rid="apply_1")
    say(ROTARACTOR, "skip")

    register(OTHER_ROTARIAN, "rotarian", "Mallory", "RC Elsewhere", "Mysuru")
    approve_rotarian(OTHER_ROTARIAN)
    assert "couldn't find" in say(OTHER_ROTARIAN, rid="short_1")[0].body
    assert "couldn't find" in say(OTHER_ROTARIAN, rid="app_1")[0].body
    assert "couldn't find" in say(OTHER_ROTARIAN, rid="applicants_1")[0].body
    assert "couldn't find" in say(OTHER_ROTARIAN, rid="close_1")[0].body
    with SessionLocal() as db:
        assert db.get(Application, 1).status == "applied"
        assert db.get(Job, 1).status == "open"


def test_rotaractor_cannot_post_jobs():
    register(ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    outs = say(ROTARACTOR, rid="post_job")
    assert outs[0].kind == "list"  # just the menu again
    assert say(ROTARACTOR, "Some title")[0].kind == "list"
    with SessionLocal() as db:
        assert db.scalar(select(Job)) is None


def test_closed_job_cannot_be_applied_to():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    approve_rotarian(ROTARIAN)
    post_job(ROTARIAN)
    assert "closed" in say(ROTARIAN, rid="close_1")[0].body
    register(ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    assert "no longer available" in say(ROTARACTOR, rid="apply_1")[0].body
    assert "No open jobs" in say(ROTARACTOR, rid="browse")[0].body


def test_cancel_resets_posting_flow():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    approve_rotarian(ROTARIAN)
    say(ROTARIAN, rid="post_job")
    say(ROTARIAN, "Some title")
    assert say(ROTARIAN, "cancel")[0].kind == "list"
    with SessionLocal() as db:
        assert db.scalar(select(Job)) is None


# ---------------------------------------------------------------- limits
def test_browse_paginates_beyond_nine_jobs():
    register(ROTARIAN, "rotarian", "Ravi Kumar", "RC Bengaluru Central", "Bengaluru")
    approve_rotarian(ROTARIAN)
    for i in range(11):
        post_job(ROTARIAN, title=f"Job number {i}")
    register(ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    page1 = say(ROTARACTOR, rid="browse")[0]
    assert len(page1.rows) == 10 and page1.rows[-1]["id"] == "more_9"
    page2 = say(ROTARACTOR, rid="more_9")[0]
    assert len(page2.rows) == 2


def test_unexpected_input_falls_back_to_menu():
    register(ROTARACTOR, "rotaractor", "Anita Rao", "Rotaract Club of Indiranagar", "Bengaluru")
    assert say(ROTARACTOR, "what is this??")[0].kind == "list"
    assert say(ROTARACTOR, rid="nonsense_99")[0].kind == "list"
