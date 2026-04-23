# See docs/notes-ml.md#servicesmltestsconftestpy for the env-var-before-import isolation contract.

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="hinter-ml-tests-"))
_TMP_DB = _TMP_DIR / "test.db"
os.environ["HINTER_DATABASE_URL"] = f"sqlite:///{_TMP_DB.as_posix()}"

import pytest  # noqa: E402  (must come after the env var assignment above)


@pytest.fixture(autouse=True)
def _reset_database() -> None:
    from app.database import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
