from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


def setup_module() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_healthz() -> None:
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_ingest_and_lf_run() -> None:
    client = TestClient(app)
    csv_body = "text,sector\nhello world,alpha\nSHORT,alpha\n"
    files = {"file": ("sample.csv", io.BytesIO(csv_body.encode("utf-8")), "text/csv")}
    data = {"text_column": "text"}
    res = client.post("/v1/documents/upload", files=files, data=data)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inserted"] == 2

    res = client.post("/v1/tags", json={"name": f"t_{uuid.uuid4().hex[:8]}"})
    assert res.status_code == 201, res.text
    tag_id = res.json()["id"]

    lf_body = {
        "tag_id": tag_id,
        "name": "has world",
        "type": "keywords",
        "config": {"keywords": ["world"], "mode": "any"},
    }
    res = client.post("/v1/labeling-functions", json=lf_body)
    assert res.status_code == 201, res.text
    lf_id = res.json()["id"]

    res = client.post("/v1/lf-runs", json={"tag_id": tag_id, "labeling_function_ids": [lf_id]})
    assert res.status_code == 202, res.text
    run = res.json()
    assert run["status"] == "completed"

    res = client.get(f"/v1/lf-runs/{run['id']}/matrix")
    assert res.status_code == 200, res.text
    matrix = res.json()
    assert matrix["labeling_function_ids"] == [lf_id]
    assert matrix["entries"]
