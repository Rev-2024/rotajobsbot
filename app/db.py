from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import settings
from .models import Base

if settings.database_url.startswith("sqlite"):
    _kwargs = {"connect_args": {"check_same_thread": False}}
else:
    # Serverless Postgres (e.g. Neon) closes idle connections when it sleeps. pool_pre_ping tests a pooled
    # connection before using it and reconnects if it died, so the first visit after a quiet spell still works.
    _kwargs = {"pool_pre_ping": True, "pool_recycle": 280}

engine = create_engine(settings.database_url, **_kwargs)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
