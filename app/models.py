from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(12))  # rotarian | rotaractor
    name: Mapped[str] = mapped_column(String(100))
    club: Mapped[str] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(100))
    # Rotaractors are approved on signup; Rotarians wait for a coordinator.
    status: Mapped[str] = mapped_column(String(10), default="approved")  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    poster_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    company: Mapped[str] = mapped_column(String(150))
    location: Mapped[str] = mapped_column(String(100))
    job_type: Mapped[str] = mapped_column(String(20))
    experience: Mapped[str] = mapped_column(String(100))
    salary: Mapped[str] = mapped_column(String(100))
    deadline: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open|closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    poster: Mapped[User] = relationship()


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id", "applicant_id", name="uq_one_application"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="applied")  # applied|shortlisted|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship()
    applicant: Mapped[User] = relationship()


class ChatSession(Base):
    """Where each phone number is in the conversation."""

    __tablename__ = "chat_sessions"

    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    state: Mapped[str] = mapped_column(String(30), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProcessedMessage(Base):
    """WhatsApp retries webhooks; remember message ids so we never handle one twice."""

    __tablename__ = "processed_messages"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Account(Base):
    """Website login. The phone number is the same id used for WhatsApp, so both channels share one user."""

    __tablename__ = "accounts"

    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboxMessage(Base):
    """Messages meant for someone who isn't in the current request (e.g. an applicant alert for a Rotarian).

    The website delivers them the next time that person opens or polls the chat, so there is no
    24-hour window to worry about.
    """

    __tablename__ = "inbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
