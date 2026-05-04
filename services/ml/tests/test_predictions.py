"""Tests for batch prediction endpoint."""
from __future__ import annotations

import io
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.predictions import _format_as_csv


@pytest.fixture
def client():
    return TestClient(app)


def _new_project(client: TestClient, prefix: str = "Pred") -> str:
    res = client.post("/v1/projects", json={"name": f"{prefix}_{uuid.uuid4().hex[:6]}"})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _create_tag(client: TestClient, project_id: str, name: str) -> str:
    res = client.post(f"/v1/tags?project_id={project_id}", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _create_lf(
    client: TestClient, tag_id: str, name: str, lf_type: str, config: dict[str, Any]
) -> str:
    res = client.post(
        "/v1/labeling-functions",
        json={"tag_id": tag_id, "name": name, "type": lf_type, "config": config},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.fixture
def project_with_tags_and_lfs(client: TestClient) -> dict[str, Any]:
    """Create a project with tags and labeling functions."""
    project_id = _new_project(client, "PredTest")

    # Create tags
    tag1_id = _create_tag(client, project_id, "is_invoice")
    tag2_id = _create_tag(client, project_id, "is_complaint")

    # Create LFs for tag1 (is_invoice)
    lf1_id = _create_lf(
        client,
        tag1_id,
        "invoice_regex",
        "regex",
        {"pattern": "invoice", "flags": "i"},
    )
    lf2_id = _create_lf(
        client,
        tag1_id,
        "invoice_keywords",
        "keywords",
        {"keywords": ["invoice", "receipt"], "mode": "any"},
    )

    # Create LF for tag2 (is_complaint)
    lf3_id = _create_lf(
        client,
        tag2_id,
        "complaint_keywords",
        "keywords",
        {"keywords": ["complaint", "unhappy", "dissatisfied"], "mode": "any"},
    )

    return {
        "project_id": project_id,
        "tag1_id": tag1_id,
        "tag2_id": tag2_id,
        "lf1_id": lf1_id,
        "lf2_id": lf2_id,
        "lf3_id": lf3_id,
    }


def test_batch_predict_json_format(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test batch prediction with JSON response format."""
    project_id = project_with_tags_and_lfs["project_id"]

    csv_data = "id,text\n1,This is an invoice\n2,I am very unhappy\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }
    data = {
        "text_column": "text",
        "id_column": "id",
        "project_id": project_id,
    }

    response = client.post(
        "/v1/predictions?format=json",
        files=files,
        data=data,
    )

    assert response.status_code == 200
    result = response.json()

    assert "documents" in result
    documents = result["documents"]
    assert len(documents) == 2

    # Check first document (invoice)
    doc1 = documents[0]
    assert doc1["original_id"] == "1"
    assert "This is an invoice" in doc1["text"]
    assert len(doc1["predictions"]) == 2  # Two tags

    # Check predictions for is_invoice
    invoice_pred = next(p for p in doc1["predictions"] if p["tag_name"] == "is_invoice")
    assert invoice_pred["predicted_label"] == 1  # Should predict positive
    assert 0 <= invoice_pred["probability"] <= 1

    # Check predictions for is_complaint
    complaint_pred = next(p for p in doc1["predictions"] if p["tag_name"] == "is_complaint")
    assert complaint_pred["predicted_label"] == 0  # No votes, so abstain

    # Check second document (complaint)
    doc2 = documents[1]
    assert doc2["original_id"] == "2"
    assert "I am very unhappy" in doc2["text"]

    complaint_pred = next(p for p in doc2["predictions"] if p["tag_name"] == "is_complaint")
    assert complaint_pred["predicted_label"] == 1  # Should predict positive


def test_batch_predict_csv_format(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test batch prediction with CSV response format."""
    project_id = project_with_tags_and_lfs["project_id"]

    csv_data = "id,text\n1,Invoice #12345\n2,Happy customer\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }
    data = {
        "text_column": "text",
        "id_column": "id",
        "project_id": project_id,
    }

    response = client.post(
        "/v1/predictions?format=csv",
        files=files,
        data=data,
    )

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]

    # Parse CSV response
    csv_text = response.text
    lines = csv_text.strip().split("\n")

    # Check header contains expected columns (order may vary)
    header_line = lines[0]
    assert "id" in header_line
    assert "original_id" in header_line
    assert "text" in header_line
    assert "is_invoice_label" in header_line
    assert "is_invoice_probability" in header_line
    assert "is_complaint_label" in header_line
    assert "is_complaint_probability" in header_line

    # Check data rows
    assert len(lines) == 3  # Header + 2 documents


def test_batch_predict_default_format(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test that default format is JSON."""
    project_id = project_with_tags_and_lfs["project_id"]

    csv_data = "id,text\n1,Test document\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }
    data = {
        "text_column": "text",
        "id_column": "id",
        "project_id": project_id,
    }

    response = client.post(
        "/v1/predictions",  # No format parameter
        files=files,
        data=data,
    )

    assert response.status_code == 200
    result = response.json()
    assert "documents" in result


def test_batch_predict_missing_project_id(client: TestClient):
    """Test that missing project_id returns 400."""
    csv_data = "id,text\n1,Test\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }

    response = client.post(
        "/v1/predictions",
        files=files,
        data={"text_column": "text"},  # No project_id
    )

    assert response.status_code == 400


def test_batch_predict_invalid_format(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test that invalid format parameter returns 422."""
    project_id = project_with_tags_and_lfs["project_id"]

    csv_data = "id,text\n1,Test\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }
    data = {"text_column": "text", "project_id": project_id}

    response = client.post(
        "/v1/predictions?format=invalid",
        files=files,
        data=data,
    )

    assert response.status_code == 422  # Validation error


def test_batch_predict_missing_file(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test that missing file returns 400."""
    project_id = project_with_tags_and_lfs["project_id"]

    response = client.post(
        "/v1/predictions",
        data={"text_column": "text", "project_id": project_id},
    )

    assert response.status_code == 400
    assert "file" in response.json()["detail"]


def test_batch_predict_with_metadata(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test batch prediction preserves metadata."""
    project_id = project_with_tags_and_lfs["project_id"]

    csv_data = "id,text,source\n1,Invoice here,email\n2,Happy,chat\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }
    data = {
        "text_column": "text",
        "id_column": "id",
        "project_id": project_id,
    }

    response = client.post(
        "/v1/predictions?format=json",
        files=files,
        data=data,
    )

    assert response.status_code == 200
    result = response.json()
    documents = result["documents"]

    # Check metadata is preserved
    doc1 = documents[0]
    assert doc1["metadata"]["source"] == "email"

    doc2 = documents[1]
    assert doc2["metadata"]["source"] == "chat"


def test_batch_predict_probability_values(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test that probability values are correct."""
    project_id = project_with_tags_and_lfs["project_id"]

    csv_data = "id,text\n1,invoice invoice invoice\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }
    data = {
        "text_column": "text",
        "id_column": "id",
        "project_id": project_id,
    }

    response = client.post(
        "/v1/predictions?format=json",
        files=files,
        data=data,
    )

    assert response.status_code == 200
    result = response.json()
    doc = result["documents"][0]

    invoice_pred = next(p for p in doc["predictions"] if p["tag_name"] == "is_invoice")
    # Both LFs for invoice should fire (regex and keywords both match)
    assert invoice_pred["positive_votes"] == 2
    assert invoice_pred["negative_votes"] == 0
    # With 2 positive votes: P = (1 + 2) / (2 + 2 + 0) = 3/4 = 0.75
    assert abs(invoice_pred["probability"] - 0.75) < 0.01


def test_batch_predict_with_no_matching_lfs(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test batch prediction when no LFs match."""
    project_id = project_with_tags_and_lfs["project_id"]

    csv_data = "id,text\n1,random text\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }
    data = {
        "text_column": "text",
        "id_column": "id",
        "project_id": project_id,
    }

    response = client.post(
        "/v1/predictions?format=json",
        files=files,
        data=data,
    )

    assert response.status_code == 200
    result = response.json()
    doc = result["documents"][0]

    for pred in doc["predictions"]:
        # When no votes, probability should be 0.5 (uninformed prior)
        assert abs(pred["probability"] - 0.5) < 0.01
        assert pred["positive_votes"] == 0
        assert pred["negative_votes"] == 0
        # Label should be abstain (0)
        assert pred["predicted_label"] == 0


def test_format_as_csv():
    """Test CSV formatting function."""
    results = [
        {
            "id": "doc-1",
            "original_id": "1",
            "text": "Invoice here",
            "metadata": {"source": "email"},
            "predictions": [
                {
                    "tag_name": "is_invoice",
                    "predicted_label": 1,
                    "probability": 0.75,
                    "positive_votes": 2,
                    "negative_votes": 0,
                    "conflict_score": 0.0,
                    "entropy": 0.811,
                }
            ],
        }
    ]

    response = _format_as_csv(results)

    assert response.status_code == 200
    assert response.media_type == "text/csv"


def test_batch_predict_documents_are_saved(client: TestClient, project_with_tags_and_lfs: dict[str, Any]):
    """Test that documents are actually saved to the database."""
    project_id = project_with_tags_and_lfs["project_id"]

    csv_data = "id,text\n1,Invoice document\n2,Complaint document\n"
    files = {
        "file": ("test.csv", io.BytesIO(csv_data.encode()), "text/csv"),
    }
    data = {
        "text_column": "text",
        "id_column": "id",
        "project_id": project_id,
    }

    response = client.post(
        "/v1/predictions?format=json",
        files=files,
        data=data,
    )

    assert response.status_code == 200

    # Check documents via API
    doc_response = client.get("/v1/documents", params={"limit": 50, "project_id": project_id})
    assert doc_response.status_code == 200
    docs = doc_response.json()["items"]
    assert len(docs) == 2
    assert any("Invoice" in d["text"] for d in docs)
    assert any("Complaint" in d["text"] for d in docs)
