from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from app.main import app


def _ingest(client: TestClient, csv: str, project_id: str) -> dict[str, str]:
    files = {"file": ("docs.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")}
    data: dict[str, str] = {"text_column": "text", "project_id": project_id}
    res = client.post("/v1/documents/upload", files=files, data=data)
    assert res.status_code == 200, res.text
    res = client.get("/v1/documents", params={"limit": 500, "project_id": project_id})
    assert res.status_code == 200
    return {d["text"]: d["id"] for d in res.json()["items"]}


def _new_project(client: TestClient, prefix: str = "Proj") -> str:
    res = client.post("/v1/projects", json={"name": f"{prefix}_{uuid.uuid4().hex[:6]}"})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_scoped_endpoints_reject_calls_without_project_id() -> None:
    # See docs/notes-ml.md (services/ml/tests/test_projects.py section): no more "Default" fallback.
    client = TestClient(app)
    res = client.get("/v1/documents")
    assert res.status_code == 400
    assert "project_id" in res.json()["detail"]

    res = client.get("/v1/tags")
    assert res.status_code == 400

    res = client.post("/v1/tags", json={"name": "anything"})
    assert res.status_code == 400


def test_projects_are_isolated_and_share_tag_names() -> None:
    client = TestClient(app)
    alpha_id = _new_project(client, "Alpha")
    beta_id = _new_project(client, "Beta")

    alpha_docs = _ingest(client, "text\nhello alpha\n", project_id=alpha_id)
    beta_docs = _ingest(client, "text\nhello beta\n", project_id=beta_id)
    assert len(alpha_docs) == 1 and len(beta_docs) == 1
    [alpha_doc_id] = list(alpha_docs.values())
    [beta_doc_id] = list(beta_docs.values())
    assert alpha_doc_id != beta_doc_id

    res = client.get("/v1/documents", params={"project_id": alpha_id})
    assert {d["id"] for d in res.json()["items"]} == {alpha_doc_id}
    res = client.get("/v1/documents", params={"project_id": beta_id})
    assert {d["id"] for d in res.json()["items"]} == {beta_doc_id}

    res = client.post(f"/v1/tags?project_id={alpha_id}", json={"name": "is_topic"})
    assert res.status_code == 201, res.text
    res = client.post(f"/v1/tags?project_id={beta_id}", json={"name": "is_topic"})
    assert res.status_code == 201, res.text

    res = client.post(f"/v1/tags?project_id={alpha_id}", json={"name": "is_topic"})
    assert res.status_code == 409


def test_create_tag_honors_project_id_query_parameter() -> None:
    # See docs/notes-ml.md (services/ml/tests/test_projects.py section): query-param project_id is what the web client uses.
    client = TestClient(app)
    alpha_id = _new_project(client, "AlphaQry")
    beta_id = _new_project(client, "BetaQry")

    tag_name = f"qry_only_{uuid.uuid4().hex[:6]}"
    res = client.post(f"/v1/tags?project_id={beta_id}", json={"name": tag_name})
    assert res.status_code == 201, res.text
    assert res.json()["project_id"] == beta_id

    res = client.get("/v1/tags", params={"project_id": beta_id})
    assert res.status_code == 200
    assert any(t["name"] == tag_name for t in res.json())

    res = client.get("/v1/tags", params={"project_id": alpha_id})
    assert res.status_code == 200
    assert all(t["name"] != tag_name for t in res.json())


def test_any_project_can_be_deleted() -> None:
    # See docs/notes-ml.md (services/ml/tests/test_projects.py section): no Default-project guard anymore.
    client = TestClient(app)
    res = client.post("/v1/projects", json={"name": f"Default_{uuid.uuid4().hex[:6]}"})
    assert res.status_code == 201, res.text
    pid = res.json()["id"]

    _ingest(client, "text\nthrowaway\n", project_id=pid)
    res = client.post(f"/v1/tags?project_id={pid}", json={"name": "doomed"})
    assert res.status_code == 201

    res = client.delete(f"/v1/projects/{pid}")
    assert res.status_code == 204

    res = client.get("/v1/projects")
    assert all(p["id"] != pid for p in res.json())


def test_export_then_import_roundtrips_full_workspace() -> None:
    client = TestClient(app)
    res = client.post("/v1/projects", json={"name": f"Source_{uuid.uuid4().hex[:6]}"})
    assert res.status_code == 201
    src_id = res.json()["id"]

    docs = _ingest(client, "text\nbuy now invoice 2026\nfree pizza party\n", project_id=src_id)
    invoice_id = docs["buy now invoice 2026"]
    pizza_id = docs["free pizza party"]

    res = client.post("/v1/tags", json={"name": "is_invoice", "project_id": src_id})
    assert res.status_code == 201
    tag_id = res.json()["id"]

    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": "has_invoice",
            "type": "keywords",
            "config": {"keywords": ["invoice"], "mode": "any"},
        },
    )
    assert res.status_code == 201
    lf_id = res.json()["id"]
    assert res.json()["project_id"] == src_id

    res = client.post(
        "/v1/lf-runs",
        json={"tag_id": tag_id, "labeling_function_ids": [lf_id]},
    )
    assert res.status_code == 202
    run = res.json()
    assert run["status"] == "completed"
    assert run["project_id"] == src_id

    for did, value in [(invoice_id, 1), (pizza_id, -1)]:
        res = client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": value},
        )
        assert res.status_code == 201, res.text

    res = client.get(f"/v1/projects/{src_id}/export")
    assert res.status_code == 200, res.text
    bundle = res.json()
    assert bundle["format"] == "hinter-factory.project"
    assert len(bundle["documents"]) == 2
    assert len(bundle["tags"]) == 1
    assert len(bundle["labeling_functions"]) == 1
    assert len(bundle["gold_labels"]) == 2
    assert len(bundle["lf_runs"]) == 1
    assert len(bundle["lf_runs"][0]["votes"]) == 2

    res = client.post("/v1/projects/import", json=bundle)
    assert res.status_code == 201, res.text
    imported = res.json()
    assert imported["counts"] == {
        "documents": 2,
        "tags": 1,
        "labeling_functions": 1,
        "gold_labels": 2,
        "lf_runs": 1,
        "probabilistic_labels": 0,
    }
    assert imported["name"].startswith(bundle["project"]["name"])
    new_id = imported["id"]
    assert new_id != src_id

    res = client.get("/v1/tags", params={"project_id": new_id})
    assert res.status_code == 200
    new_tag = res.json()[0]
    assert new_tag["name"] == "is_invoice"
    assert new_tag["id"] != tag_id

    res = client.get("/v1/evaluation", params={"tag_id": new_tag["id"]})
    assert res.status_code == 200, res.text
    body = res.json()
    summary = body["summary"]
    # The keyword LF voted +1 on invoice doc, 0 on pizza doc.
    # Gold labels: invoice=+1, pizza=-1. So:
    #   gold +1, pred +1 -> TP
    #   gold -1, pred  0 -> abstain_on_negative
    assert summary["true_positive"] == 1
    assert summary["abstain_on_negative"] == 1
    assert summary["false_positive"] == 0
    assert summary["false_negative"] == 0


def test_import_rejects_unknown_format() -> None:
    client = TestClient(app)
    res = client.post("/v1/projects/import", json={"format": "something else"})
    assert res.status_code == 400
