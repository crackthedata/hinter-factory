from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from app.main import app


def _new_project(client: TestClient, prefix: str = "Sugg") -> str:
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


def _suggest(client: TestClient, project_id: str, tag_id: str, limit: int = 10) -> dict:
    res = client.get(
        "/v1/labeling-functions/suggestions",
        params={"project_id": project_id, "tag_id": tag_id, "limit": limit},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_suggestions_cold_start_returns_tag_name_tokens() -> None:
    """With no gold labels, the panel falls back to tokens parsed from the tag name."""
    client = TestClient(app)
    project_id = _new_project(client, "ColdStart")
    # A small corpus so the cold-start branch can attach example doc ids.
    _ingest(
        client,
        "text\n"
        "Please review this invoice for the warranty department.\n"
        "Quarterly revenue summary attached.\n"
        "Customer complaint about late shipment.\n",
        project_id=project_id,
    )

    tag_id = _create_tag(client, project_id, f"is_invoice_{uuid.uuid4().hex[:6]}")
    body = _suggest(client, project_id, tag_id)

    assert body["tag_id"] == tag_id
    assert body["basis"] == "tag_name"
    keywords = {s["keyword"] for s in body["suggestions"]}
    assert "invoice" in keywords, body
    assert "is" not in keywords  # stopword
    # The matching doc should be attached as an example.
    by_kw = {s["keyword"]: s for s in body["suggestions"]}
    assert by_kw["invoice"]["example_document_ids"], by_kw["invoice"]


def test_suggestions_gold_driven_surfaces_discriminative_token() -> None:
    """Tokens that appear in gold-positive docs but not gold-negative docs rank highly."""
    client = TestClient(app)
    project_id = _new_project(client, "Gold")
    csv_lines = [
        "text",
        "Warranty claim approved for the order.",       # +1
        "Replacement covered under warranty terms.",     # +1
        "Extended warranty contract enclosed.",          # +1
        "Customer service hours are nine to five.",      # -1
        "Lobby decor renovation schedule attached.",     # -1
    ]
    doc_ids = _ingest(client, "\n".join(csv_lines) + "\n", project_id=project_id)
    assert len(doc_ids) == 5

    tag_id = _create_tag(client, project_id, f"warranty_{uuid.uuid4().hex[:6]}")

    pos_ids = doc_ids[:3]
    neg_ids = doc_ids[3:]
    for did in pos_ids:
        res = client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": 1},
        )
        assert res.status_code == 201, res.text
    for did in neg_ids:
        res = client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": -1},
        )
        assert res.status_code == 201, res.text

    body = _suggest(client, project_id, tag_id)
    assert body["basis"] in ("gold", "mixed")
    by_kw = {s["keyword"]: s for s in body["suggestions"]}
    assert "warranty" in by_kw, body
    sugg = by_kw["warranty"]
    assert sugg["positive_hits"] >= 2
    assert sugg["negative_hits"] == 0
    assert set(sugg["example_document_ids"]).issubset(set(pos_ids))


def test_suggestions_skip_already_covered_keyword() -> None:
    """If a keywords LF already mentions 'warranty', it must not be re-suggested."""
    client = TestClient(app)
    project_id = _new_project(client, "Dedup")
    csv_lines = [
        "text",
        "Warranty claim approved for the order.",       # +1
        "Replacement covered under warranty terms.",     # +1
        "Extended warranty contract enclosed.",          # +1
        "Customer service hours are nine to five.",      # -1
        "Lobby decor renovation schedule attached.",     # -1
    ]
    doc_ids = _ingest(client, "\n".join(csv_lines) + "\n", project_id=project_id)
    tag_id = _create_tag(client, project_id, f"warranty_{uuid.uuid4().hex[:6]}")

    for did in doc_ids[:3]:
        client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": 1},
        )
    for did in doc_ids[3:]:
        client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": -1},
        )

    before = _suggest(client, project_id, tag_id)
    assert "warranty" in {s["keyword"] for s in before["suggestions"]}

    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": "has_warranty",
            "type": "keywords",
            "config": {"keywords": ["WARRANTY"], "mode": "any"},
        },
    )
    assert res.status_code == 201, res.text

    after = _suggest(client, project_id, tag_id)
    assert "warranty" not in {s["keyword"] for s in after["suggestions"]}, after


def test_suggestions_reappear_after_lf_deleted() -> None:
    """Deleting a keywords LF must un-suppress its keyword from the suggestions panel."""
    client = TestClient(app)
    project_id = _new_project(client, "Reappear")
    csv_lines = [
        "text",
        "Warranty claim approved for the order.",       # +1
        "Replacement covered under warranty terms.",     # +1
        "Extended warranty contract enclosed.",          # +1
        "Customer service hours are nine to five.",      # -1
        "Lobby decor renovation schedule attached.",     # -1
    ]
    doc_ids = _ingest(client, "\n".join(csv_lines) + "\n", project_id=project_id)
    tag_id = _create_tag(client, project_id, f"warranty_{uuid.uuid4().hex[:6]}")

    for did in doc_ids[:3]:
        client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": 1},
        )
    for did in doc_ids[3:]:
        client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": -1},
        )

    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": "has_warranty",
            "type": "keywords",
            "config": {"keywords": ["warranty"], "mode": "any"},
        },
    )
    assert res.status_code == 201, res.text
    lf_id = res.json()["id"]

    suppressed = _suggest(client, project_id, tag_id)
    assert "warranty" not in {s["keyword"] for s in suppressed["suggestions"]}

    res = client.delete(f"/v1/labeling-functions/{lf_id}")
    assert res.status_code == 204, res.text

    after = _suggest(client, project_id, tag_id)
    assert "warranty" in {s["keyword"] for s in after["suggestions"]}, after


def test_suggestions_reflect_lf_config_edit() -> None:
    """Editing a keywords LF's config to drop a keyword must re-surface that keyword."""
    client = TestClient(app)
    project_id = _new_project(client, "Edit")
    csv_lines = [
        "text",
        "Warranty claim approved for the order.",       # +1
        "Replacement covered under warranty terms.",     # +1
        "Extended warranty contract enclosed.",          # +1
        "Customer service hours are nine to five.",      # -1
        "Lobby decor renovation schedule attached.",     # -1
    ]
    doc_ids = _ingest(client, "\n".join(csv_lines) + "\n", project_id=project_id)
    tag_id = _create_tag(client, project_id, f"warranty_{uuid.uuid4().hex[:6]}")

    for did in doc_ids[:3]:
        client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": 1},
        )
    for did in doc_ids[3:]:
        client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": -1},
        )

    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": "covers_warranty",
            "type": "keywords",
            "config": {"keywords": ["warranty"], "mode": "any"},
        },
    )
    assert res.status_code == 201
    lf_id = res.json()["id"]

    assert "warranty" not in {
        s["keyword"] for s in _suggest(client, project_id, tag_id)["suggestions"]
    }

    res = client.patch(
        f"/v1/labeling-functions/{lf_id}",
        json={"config": {"keywords": ["unrelated"], "mode": "any"}},
    )
    assert res.status_code == 200, res.text

    after = _suggest(client, project_id, tag_id)
    assert "warranty" in {s["keyword"] for s in after["suggestions"]}, after


def test_suggestions_exclude_param_suppresses_tokens_and_surfaces_others() -> None:
    """?exclude=tok must drop that token from the response and let the next-best one take its slot."""
    client = TestClient(app)
    project_id = _new_project(client, "Exclude")
    csv_lines = [
        "text",
        # Three positives mention 'warranty'; two also mention 'replacement'.
        "Warranty claim approved for the order.",
        "Replacement covered under warranty terms.",
        "Extended warranty contract enclosed; replacement available.",
        "Customer service hours are nine to five.",
        "Lobby decor renovation schedule attached.",
    ]
    doc_ids = _ingest(client, "\n".join(csv_lines) + "\n", project_id=project_id)
    tag_id = _create_tag(client, project_id, f"warranty_{uuid.uuid4().hex[:6]}")

    for did in doc_ids[:3]:
        client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": 1},
        )
    for did in doc_ids[3:]:
        client.post(
            "/v1/gold-labels",
            json={"document_id": did, "tag_id": tag_id, "value": -1},
        )

    baseline = _suggest(client, project_id, tag_id)
    keywords = {s["keyword"] for s in baseline["suggestions"]}
    assert "warranty" in keywords
    assert "replacement" in keywords

    res = client.get(
        "/v1/labeling-functions/suggestions",
        params=[
            ("project_id", project_id),
            ("tag_id", tag_id),
            ("limit", 10),
            ("exclude", "warranty"),
        ],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    keywords_after = {s["keyword"] for s in body["suggestions"]}
    assert "warranty" not in keywords_after, body
    # 'replacement' must still surface because nothing covers it.
    assert "replacement" in keywords_after, body


def test_suggestions_exclude_is_case_insensitive() -> None:
    """exclude tokens are normalised to lowercase to match the miner."""
    client = TestClient(app)
    project_id = _new_project(client, "ExcludeCase")
    csv_lines = [
        "text",
        "Warranty claim approved for the order.",
        "Replacement covered under warranty terms.",
        "Extended warranty contract enclosed.",
        "Customer service hours are nine to five.",
        "Lobby decor renovation schedule attached.",
    ]
    doc_ids = _ingest(client, "\n".join(csv_lines) + "\n", project_id=project_id)
    tag_id = _create_tag(client, project_id, f"warranty_{uuid.uuid4().hex[:6]}")
    for did in doc_ids[:3]:
        client.post("/v1/gold-labels", json={"document_id": did, "tag_id": tag_id, "value": 1})
    for did in doc_ids[3:]:
        client.post("/v1/gold-labels", json={"document_id": did, "tag_id": tag_id, "value": -1})

    res = client.get(
        "/v1/labeling-functions/suggestions",
        params=[
            ("project_id", project_id),
            ("tag_id", tag_id),
            ("exclude", "WARRANTY"),
            ("exclude", "  "),
        ],
    )
    assert res.status_code == 200, res.text
    keywords = {s["keyword"] for s in res.json()["suggestions"]}
    assert "warranty" not in keywords


def test_suggestions_endpoint_validates_inputs() -> None:
    client = TestClient(app)
    project_id = _new_project(client, "Validate")

    res = client.get("/v1/labeling-functions/suggestions", params={"tag_id": "missing"})
    assert res.status_code == 400, res.text  # missing project_id

    res = client.get(
        "/v1/labeling-functions/suggestions",
        params={"project_id": project_id, "tag_id": "does-not-exist"},
    )
    assert res.status_code == 404, res.text

    other_project_id = _new_project(client, "Other")
    tag_id = _create_tag(client, other_project_id, f"alien_{uuid.uuid4().hex[:6]}")
    res = client.get(
        "/v1/labeling-functions/suggestions",
        params={"project_id": project_id, "tag_id": tag_id},
    )
    assert res.status_code == 404, res.text


def test_suggestions_response_shape() -> None:
    client = TestClient(app)
    project_id = _new_project(client, "Shape")
    tag_id = _create_tag(client, project_id, f"shape_{uuid.uuid4().hex[:6]}")
    body = _suggest(client, project_id, tag_id, limit=5)
    assert set(body.keys()) == {"tag_id", "generated_at", "basis", "suggestions"}
    assert body["tag_id"] == tag_id
    assert isinstance(body["suggestions"], list)
    for s in body["suggestions"]:
        assert set(s.keys()) == {
            "keyword",
            "score",
            "positive_hits",
            "negative_hits",
            "example_document_ids",
        }
        assert isinstance(s["keyword"], str)
        assert isinstance(s["positive_hits"], int)
        assert isinstance(s["negative_hits"], int)
        assert isinstance(s["example_document_ids"], list)
