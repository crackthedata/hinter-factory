from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


def setup_module() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _ingest(client: TestClient, csv: str) -> list[str]:
    files = {"file": ("docs.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")}
    res = client.post("/v1/documents/upload", files=files, data={"text_column": "text"})
    assert res.status_code == 200, res.text
    res = client.get("/v1/documents", params={"limit": 500})
    assert res.status_code == 200
    return [d["id"] for d in res.json()["items"]]


def test_evaluation_reports_fp_fn_breakdown() -> None:
    client = TestClient(app)

    csv = (
        "text\n"
        "alpha apple banana\n"   # gold +1, has 'apple'
        "alpha cherry date\n"    # gold +1, no 'apple'  -> FN (abstain on positive)
        "beta apple grape\n"     # gold -1, has 'apple' -> FP
        "beta lemon mango\n"     # gold -1, no keyword  -> TN-ish (abstain on negative)
        "gamma apple kiwi\n"     # no gold              -> ignored entirely
    )
    doc_ids = _ingest(client, csv)
    assert len(doc_ids) == 5

    res = client.post("/v1/tags", json={"name": f"fruit_{uuid.uuid4().hex[:8]}"})
    assert res.status_code == 201
    tag_id = res.json()["id"]

    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": "has_apple",
            "type": "keywords",
            "config": {"keywords": ["apple"], "mode": "any"},
        },
    )
    assert res.status_code == 201
    lf_id = res.json()["id"]

    res = client.post(
        "/v1/lf-runs",
        json={"tag_id": tag_id, "labeling_function_ids": [lf_id]},
    )
    assert res.status_code == 202
    run = res.json()
    assert run["status"] == "completed"

    # Gold-label four docs, leave the fifth ungolden.
    gold_values = [1, 1, -1, -1]
    for did, val in zip(doc_ids[:4], gold_values, strict=True):
        res = client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": val},
        )
        assert res.status_code == 201, res.text

    res = client.get("/v1/evaluation", params={"tag_id": tag_id})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["run_id"] == run["id"]
    summary = body["summary"]
    assert summary["total_gold"] == 4
    assert summary["considered"] == 4
    # The keyword LF only votes 0 or 1, so the "negative" categories collapse to abstains.
    assert summary["true_positive"] == 1, body
    assert summary["false_positive"] == 1, body
    assert summary["abstain_on_positive"] == 1, body
    assert summary["abstain_on_negative"] == 1, body
    assert summary["false_negative"] == 0, body
    assert summary["true_negative"] == 0, body
    assert summary["gold_abstain"] == 0
    # Recall = TP / (TP + FN + abstain_on_positive) = 1 / 2 = 0.5
    assert abs(summary["recall"] - 0.5) < 1e-9
    # Precision = TP / (TP + FP) = 1 / 2 = 0.5
    assert abs(summary["precision"] - 0.5) < 1e-9
    assert abs(summary["f1"] - 0.5) < 1e-9

    by_cat: dict[str, list[dict]] = {}
    for row in body["rows"]:
        by_cat.setdefault(row["category"], []).append(row)

    assert {r["document_id"] for r in by_cat["false_positive"]} == {doc_ids[2]}
    assert {r["document_id"] for r in by_cat["abstain_on_positive"]} == {doc_ids[1]}
    assert {r["document_id"] for r in by_cat["true_positive"]} == {doc_ids[0]}
    # The ungolden doc must not appear at all.
    seen = {r["document_id"] for r in body["rows"]}
    assert doc_ids[4] not in seen


def test_evaluation_404_when_tag_missing() -> None:
    client = TestClient(app)
    res = client.get("/v1/evaluation", params={"tag_id": "nope"})
    assert res.status_code == 404


def test_evaluation_handles_no_completed_run() -> None:
    client = TestClient(app)
    res = client.post("/v1/tags", json={"name": f"empty_{uuid.uuid4().hex[:8]}"})
    assert res.status_code == 201
    tag_id = res.json()["id"]
    res = client.get("/v1/evaluation", params={"tag_id": tag_id})
    assert res.status_code == 200
    body = res.json()
    assert body["run_id"] is None
    assert body["summary"]["considered"] == 0
    assert "message" in body
