import os
import sys
import tempfile
from pathlib import Path

# Must be set before the app modules are imported.
# Tests use a throwaway SQLite file. Set TEST_DATABASE_URL to run them against Postgres instead.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["DRY_RUN"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    from app import web

    web.FAILS.clear()  # login throttle state is in memory
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
