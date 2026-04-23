"""End-to-end tests for the streaming CSV ingest path.

These exercise:
- `iter_csv_batches` against a synthetic CSV that materializes on disk only
  (never as a single Python bytes/str object), and
- the `/v1/documents/upload` endpoint via FastAPI's TestClient, including the
  cross-project ID collision behavior we promised to preserve.

The "bounded memory" test generates a >100 MB file in `tmp_path` and asserts
that peak Python heap allocation during a full streaming pass stays well below
the file size. If someone accidentally re-introduces `file.read()` somewhere in
the path, this test will fail loudly.
"""

from __future__ import annotations

import io
import os
import tracemalloc
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingest import iter_csv_batches
from app.main import app


def _write_csv(path: Path, rows: int, body_chars: int = 200) -> int:
    """Write a CSV with `rows` data lines. Returns the file size in bytes.

    Uses simple ASCII so we exercise the fast UTF-8 path. Each row carries one
    `text` column plus two metadata columns so we also cover metadata
    extraction.
    """
    body = "x" * body_chars
    size = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        header = "id,text,sector,bucket\n"
        fh.write(header)
        size += len(header)
        # Buffer ~64 KiB of rows at a time to keep the test fast without
        # ballooning Python memory in the test process.
        buf: list[str] = []
        for i in range(rows):
            buf.append(f"row-{i},{body},alpha,b{i % 7}\n")
            if len(buf) >= 1024:
                chunk = "".join(buf)
                fh.write(chunk)
                size += len(chunk)
                buf.clear()
        if buf:
            chunk = "".join(buf)
            fh.write(chunk)
            size += len(chunk)
    return size


def test_iter_csv_batches_basic_roundtrip(tmp_path: Path) -> None:
    csv_path = tmp_path / "small.csv"
    _write_csv(csv_path, rows=2500, body_chars=20)

    total = 0
    saw_metadata = False
    for items, errors, dropped in iter_csv_batches(
        str(csv_path), text_column="text", id_column="id", batch_size=500
    ):
        assert not errors
        assert dropped == 0
        for it in items:
            assert it["text"] == "x" * 20
            assert "sector" in it["metadata"] and "bucket" in it["metadata"]
            assert "id" not in it["metadata"]  # consumed as the id column
            assert "text" not in it["metadata"]  # consumed as the text column
            saw_metadata = True
        total += len(items)
    assert total == 2500
    assert saw_metadata


def test_iter_csv_batches_missing_text_column(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    from app.ingest import IngestError

    with pytest.raises(IngestError):
        # Pulling the first batch is enough to surface the validation error.
        next(iter(iter_csv_batches(str(csv_path), text_column="nope")))


def test_iter_csv_batches_bounded_memory(tmp_path: Path) -> None:
    """Stream a >100 MB CSV and assert peak Python heap stays modest.

    The generator must not keep all rows in memory; if it does, this test will
    flag the regression immediately.
    """
    csv_path = tmp_path / "big.csv"
    file_size = _write_csv(csv_path, rows=500_000, body_chars=220)
    assert file_size > 100 * 1024 * 1024, (
        f"expected >100 MB synthetic CSV, got {file_size}"
    )

    tracemalloc.start()
    try:
        rows = 0
        for items, _errors, _dropped in iter_csv_batches(
            str(csv_path), text_column="text", batch_size=10_000
        ):
            rows += len(items)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert rows == 500_000
    # File is >100 MiB; if anything buffered the whole thing we'd be far above
    # this threshold. 80 MiB leaves headroom for batch overhead and Polars
    # internals while still catching "buffer everything" regressions.
    assert peak < 80 * 1024 * 1024, (
        f"peak Python allocation {peak} bytes exceeded streaming budget"
    )


def _make_project(client: TestClient) -> str:
    res = client.post("/v1/projects", json={"name": f"stream_{uuid.uuid4().hex[:6]}"})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_upload_endpoint_streams_csv(tmp_path: Path) -> None:
    client = TestClient(app)
    project_id = _make_project(client)

    csv_path = tmp_path / "upload.csv"
    _write_csv(csv_path, rows=5000, body_chars=120)

    with open(csv_path, "rb") as fh:
        res = client.post(
            "/v1/documents/upload",
            files={"file": ("upload.csv", fh, "text/csv")},
            data={"text_column": "text", "id_column": "id", "project_id": project_id},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inserted"] == 5000
    assert body["skipped"] == 0
    assert body["errors"] == []
    assert body["truncated_errors_count"] == 0

    # Re-upload the same file: every row should now hit the same-project
    # update path (skipped count == 5000, no new inserts).
    with open(csv_path, "rb") as fh:
        res = client.post(
            "/v1/documents/upload",
            files={"file": ("upload.csv", fh, "text/csv")},
            data={"text_column": "text", "id_column": "id", "project_id": project_id},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inserted"] == 0
    assert body["skipped"] == 5000


def test_upload_endpoint_cross_project_collision_mints_new_id() -> None:
    client = TestClient(app)
    p1 = _make_project(client)
    p2 = _make_project(client)

    csv_body = "id,text\nshared-1,hello from p1\n"
    res = client.post(
        "/v1/documents/upload",
        files={"file": ("a.csv", io.BytesIO(csv_body.encode("utf-8")), "text/csv")},
        data={"text_column": "text", "id_column": "id", "project_id": p1},
    )
    assert res.status_code == 200, res.text
    assert res.json()["inserted"] == 1

    # Same id, different project: should mint a fresh UUID rather than clobber p1.
    csv_body2 = "id,text\nshared-1,hello from p2\n"
    res = client.post(
        "/v1/documents/upload",
        files={"file": ("b.csv", io.BytesIO(csv_body2.encode("utf-8")), "text/csv")},
        data={"text_column": "text", "id_column": "id", "project_id": p2},
    )
    assert res.status_code == 200, res.text
    assert res.json()["inserted"] == 1
    assert res.json()["skipped"] == 0

    # The original p1 row is intact.
    res = client.get(f"/v1/documents/shared-1")
    assert res.status_code == 200, res.text
    doc = res.json()
    assert doc["text"] == "hello from p1"

    # And p2 has a row with the supplied text but a fresh (UUID) id.
    res = client.get(f"/v1/documents?project_id={p2}")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert any(d["text"] == "hello from p2" and d["id"] != "shared-1" for d in items)


def test_upload_truncates_excess_errors(tmp_path: Path) -> None:
    client = TestClient(app)
    project_id = _make_project(client)

    # 250 rows where every row has an empty text -> 250 errors. We cap returned
    # errors at MAX_RETURNED_ERRORS (100); the rest must be reported as a count.
    lines = ["id,text"] + [f"row-{i}," for i in range(250)]
    csv_bytes = ("\n".join(lines) + "\n").encode("utf-8")

    res = client.post(
        "/v1/documents/upload",
        files={"file": ("bad.csv", io.BytesIO(csv_bytes), "text/csv")},
        data={"text_column": "text", "id_column": "id", "project_id": project_id},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inserted"] == 0
    assert body["skipped"] == 0
    assert len(body["errors"]) == 100
    assert body["truncated_errors_count"] == 150


def test_upload_endpoint_accepts_part_larger_than_starlette_default(tmp_path: Path) -> None:
    """Regression: Starlette's MultiPartParser defaults `max_part_size` to 1 MiB
    and aborts the connection on any larger file part. The upload route must
    raise that ceiling itself; otherwise multi-MB CSVs surface as ECONNRESET on
    the proxy side. We send a ~5 MiB part to prove the limit was lifted."""
    client = TestClient(app)
    project_id = _make_project(client)

    csv_path = tmp_path / "above_limit.csv"
    # ~5 MiB on disk: 25_000 rows * ~210 bytes each.
    file_size = _write_csv(csv_path, rows=25_000, body_chars=200)
    assert file_size > 2 * 1024 * 1024, "test setup should produce a >2 MiB part"

    with open(csv_path, "rb") as fh:
        res = client.post(
            "/v1/documents/upload",
            files={"file": ("above_limit.csv", fh, "text/csv")},
            data={"text_column": "text", "id_column": "id", "project_id": project_id},
        )
    assert res.status_code == 200, res.text
    assert res.json()["inserted"] == 25_000


def test_unsupported_content_type_returns_400() -> None:
    client = TestClient(app)
    project_id = _make_project(client)
    res = client.post(
        "/v1/documents/upload",
        files={"file": ("notes.docx", io.BytesIO(b"binary blob"), "application/zip")},
        data={"text_column": "text", "project_id": project_id},
    )
    assert res.status_code == 400


# Keep a tracemalloc-related import side-effect from leaking if a test fails
# mid-flight. tracemalloc.stop() in a finally above handles the happy path,
# but pytest may abort earlier; this guards subsequent tests.
@pytest.fixture(autouse=True)
def _tracemalloc_off() -> None:
    if tracemalloc.is_tracing():
        tracemalloc.stop()
