"""Tests for /v1/documents/coverage-stats."""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from app.main import app


def _new_project(client: TestClient, prefix: str = "Cov") -> str:
    res = client.post("/v1/projects", json={"name": f"{prefix}_{uuid.uuid4().hex[:6]}"})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _ingest(client: TestClient, csv: str, project_id: str) -> list[str]:
    files = {"file": ("docs.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")}
    res = client.post(
        "/v1/documents/upload",
        files=files,
        data={"text_column": "text", "project_id": project_id},
    )
    assert res.status_code == 200, res.text
    res = client.get("/v1/documents", params={"limit": 500, "project_id": project_id})
    assert res.status_code == 200
    return [d["id"] for d in res.json()["items"]]


def _create_tag(client: TestClient, project_id: str, name: str) -> str:
    res = client.post(f"/v1/tags?project_id={project_id}", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _create_keyword_lf(client: TestClient, tag_id: str, keywords: list[str]) -> str:
    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": f"kw_{uuid.uuid4().hex[:6]}",
            "type": "keywords",
            "config": {"keywords": keywords, "mode": "any"},
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _run(client: TestClient, tag_id: str, lf_ids: list[str]) -> str:
    res = client.post(
        "/v1/lf-runs",
        json={"tag_id": tag_id, "labeling_function_ids": lf_ids},
    )
    assert res.status_code == 202, res.text
    return res.json()["id"]


def _stats(client: TestClient, project_id: str, tag_id: str, **extra: object) -> dict:
    params: dict[str, object] = {"project_id": project_id, "tag_id": tag_id, **extra}
    res = client.get("/v1/documents/coverage-stats", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_coverage_stats_no_run_yet_returns_message() -> None:
    client = TestClient(app)
    project_id = _new_project(client, "NoRun")
    _ingest(client, "text\nalpha\nbeta\n", project_id=project_id)
    tag_id = _create_tag(client, project_id, f"nr_{uuid.uuid4().hex[:6]}")

    body = _stats(client, project_id, tag_id)
    assert body["run_id"] is None
    assert body["sample_size"] == 0
    assert body["estimated_recall_ceiling"] is None
    assert body["message"]


def test_coverage_stats_perfect_coverage() -> None:
    """When every sampled doc has at least one LF vote, ceiling is 1.0."""
    client = TestClient(app)
    project_id = _new_project(client, "Full")
    _ingest(
        client,
        "text\napple one\napple two\napple three\napple four\n",
        project_id=project_id,
    )
    tag_id = _create_tag(client, project_id, f"f_{uuid.uuid4().hex[:6]}")
    lf = _create_keyword_lf(client, tag_id, ["apple"])
    _run(client, tag_id, [lf])

    body = _stats(client, project_id, tag_id, sample_size=200)
    assert body["sample_size"] == 4
    assert body["sample_no_lf_fires"] == 0
    assert body["no_lf_fires_rate"] == 0.0
    assert body["estimated_recall_ceiling"] == 1.0


def test_coverage_stats_partial_coverage_reports_ceiling() -> None:
    """Half the docs uncovered -> ceiling 0.5."""
    client = TestClient(app)
    project_id = _new_project(client, "Half")
    _ingest(
        client,
        "text\napple one\napple two\nzzz three\nzzz four\n",
        project_id=project_id,
    )
    tag_id = _create_tag(client, project_id, f"h_{uuid.uuid4().hex[:6]}")
    lf = _create_keyword_lf(client, tag_id, ["apple"])
    _run(client, tag_id, [lf])

    body = _stats(client, project_id, tag_id, sample_size=200)
    assert body["sample_size"] == 4
    assert body["sample_no_lf_fires"] == 2
    assert body["no_lf_fires_rate"] == 0.5
    assert body["estimated_recall_ceiling"] == 0.5


def test_coverage_stats_sample_size_caps_to_available_docs() -> None:
    """Asking for more docs than exist returns the available count."""
    client = TestClient(app)
    project_id = _new_project(client, "Cap")
    _ingest(client, "text\napple one\nzzz two\n", project_id=project_id)
    tag_id = _create_tag(client, project_id, f"c_{uuid.uuid4().hex[:6]}")
    lf = _create_keyword_lf(client, tag_id, ["apple"])
    _run(client, tag_id, [lf])

    body = _stats(client, project_id, tag_id, sample_size=2000)
    assert body["sample_size"] == 2


def test_coverage_stats_reports_existing_gold_in_sample() -> None:
    """sample_with_gold lets the UI show 'X of N already labeled'."""
    client = TestClient(app)
    project_id = _new_project(client, "Gold")
    doc_ids = _ingest(
        client, "text\napple one\napple two\napple three\n", project_id=project_id
    )
    tag_id = _create_tag(client, project_id, f"g_{uuid.uuid4().hex[:6]}")
    lf = _create_keyword_lf(client, tag_id, ["apple"])
    _run(client, tag_id, [lf])

    res = client.post(
        "/v1/gold-labels",
        json={"document_id": doc_ids[0], "tag_id": tag_id, "value": 1},
    )
    assert res.status_code == 201, res.text

    body = _stats(client, project_id, tag_id)
    assert body["sample_with_gold"] == 1


def test_coverage_stats_validates_tag_belongs_to_project() -> None:
    client = TestClient(app)
    project_id = _new_project(client, "Val")
    other = _new_project(client, "Other")
    alien_tag = _create_tag(client, other, f"alien_{uuid.uuid4().hex[:6]}")

    res = client.get(
        "/v1/documents/coverage-stats",
        params={"project_id": project_id, "tag_id": alien_tag},
    )
    assert res.status_code == 404, res.text

    res = client.get(
        "/v1/documents/coverage-stats",
        params={"project_id": project_id, "tag_id": "nope"},
    )
    assert res.status_code == 404, res.text
