"""Test isolation: redirect the API's database to a temporary file before any
application module is imported.

Without this, tests share the dev DB because `app/database.py` calls
`get_settings()` (and creates the SQLAlchemy engine) at import time. A
`Base.metadata.drop_all()` from a test would then wipe whatever the developer
has in `services/ml/data/hinter.db`.

Setting `HINTER_DATABASE_URL` here — at the very top of the conftest, before
pytest collects any test modules — guarantees the engine binds to a temp file
instead. The temp directory is removed at the end of the session.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# IMPORTANT: assign before importing anything from `app.*` (directly or via
# test modules). Pytest loads conftest.py first, then collects tests, so this
# runs before `from app.database import ...` ever happens.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="hinter-ml-tests-"))
_TMP_DB = _TMP_DIR / "test.db"
os.environ["HINTER_DATABASE_URL"] = f"sqlite:///{_TMP_DB.as_posix()}"

import pytest  # noqa: E402  (must come after the env var assignment above)


@pytest.fixture(autouse=True)
def _reset_database() -> None:
    """Wipe and recreate the schema before every test for full isolation.

    Imported lazily so that the `HINTER_DATABASE_URL` override above is in
    effect by the time `app.database` evaluates `get_settings()`.
    """
    from app.database import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """Best-effort cleanup of the temp DB directory after the session ends."""
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
