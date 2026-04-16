from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


def setup_module() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_gold_label_ternary_and_batch_list() -> None:
    client = TestClient(app)
    csv_body = "text\nalpha\nbeta\n"
    files = {"file": ("sample.csv", io.BytesIO(csv_body.encode("utf-8")), "text/csv")}
    assert client.post("/v1/documents/upload", files=files, data={"text_column": "text"}).status_code == 200

    res = client.get("/v1/documents", params={"limit": 10})
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 2
    doc_a, doc_b = items[0]["id"], items[1]["id"]

    res = client.post("/v1/tags", json={"name": f"g_{uuid.uuid4().hex[:8]}"})
    assert res.status_code == 201
    tag_id = res.json()["id"]

    res = client.post("/v1/gold-labels", json={"document_id": doc_a, "tag_id": tag_id, "value": -1})
    assert res.status_code == 201, res.text
    assert res.json()["value"] == -1

    res = client.post("/v1/gold-labels", json={"document_id": doc_b, "tag_id": tag_id, "value": 1})
    assert res.status_code == 201

    res = client.post("/v1/gold-labels", json={"document_id": doc_a, "tag_id": tag_id, "value": 0})
    assert res.status_code == 201
    assert res.json()["value"] == 0

    res = client.get("/v1/gold-labels", params={"tag_id": tag_id, "document_ids": [doc_a, doc_b]})
    assert res.status_code == 200
    rows = res.json()
    by_doc = {r["document_id"]: r["value"] for r in rows}
    assert by_doc[doc_a] == 0
    assert by_doc[doc_b] == 1

    res = client.post("/v1/gold-labels", json={"document_id": doc_a, "tag_id": tag_id, "value": 2})
    assert res.status_code == 400
