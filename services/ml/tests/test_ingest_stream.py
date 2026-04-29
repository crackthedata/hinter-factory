# See docs/notes-ml.md (services/ml/tests/test_ingest_stream.py section) for what this file regression-guards.

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
    body = "x" * body_chars
    size = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        header = "id,text,sector,bucket\n"
        fh.write(header)
        size += len(header)
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
        next(iter(iter_csv_batches(str(csv_path), text_column="nope")))


def test_iter_csv_batches_bounded_memory(tmp_path: Path) -> None:
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

    csv_body2 = "id,text\nshared-1,hello from p2\n"
    res = client.post(
        "/v1/documents/upload",
        files={"file": ("b.csv", io.BytesIO(csv_body2.encode("utf-8")), "text/csv")},
        data={"text_column": "text", "id_column": "id", "project_id": p2},
    )
    assert res.status_code == 200, res.text
    assert res.json()["inserted"] == 1
    assert res.json()["skipped"] == 0

    res = client.get(f"/v1/documents/shared-1")
    assert res.status_code == 200, res.text
    doc = res.json()
    assert doc["text"] == "hello from p1"

    res = client.get(f"/v1/documents?project_id={p2}")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert any(d["text"] == "hello from p2" and d["id"] != "shared-1" for d in items)


def test_upload_truncates_excess_errors(tmp_path: Path) -> None:
    client = TestClient(app)
    project_id = _make_project(client)

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
    # See docs/notes-ml.md (services/ml/tests/test_ingest_stream.py section) for the Starlette 1 MiB regression context.
    client = TestClient(app)
    project_id = _make_project(client)

    csv_path = tmp_path / "above_limit.csv"
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


@pytest.fixture(autouse=True)
def _tracemalloc_off() -> None:
    if tracemalloc.is_tracing():
        tracemalloc.stop()
