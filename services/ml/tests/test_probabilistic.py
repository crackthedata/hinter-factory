from __future__ import annotations

import io
import math
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.probabilistic_aggregator import aggregate_one, aggregate_votes


def _new_project(client: TestClient, prefix: str = "Prob") -> str:
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


def test_aggregate_one_zero_votes_is_uninformed_prior() -> None:
    p, conflict, entropy = aggregate_one(0, 0)
    assert p == 0.5
    assert conflict == 0.0
    assert math.isclose(entropy, 1.0, rel_tol=1e-9)


def test_aggregate_one_single_positive_is_smoothed() -> None:
    # Laplace smoothing: 1 positive vote shouldn't push us all the way to 1.0
    p, conflict, _ = aggregate_one(1, 0)
    assert math.isclose(p, 2 / 3, rel_tol=1e-9)
    assert conflict == 0.0


def test_aggregate_one_split_votes_have_conflict() -> None:
    p, conflict, entropy = aggregate_one(2, 2)
    # 2 positive + 2 negative -> probability = (1+2)/(2+2+2) = 3/6 = 0.5
    assert math.isclose(p, 0.5, rel_tol=1e-9)
    # Conflict ratio: min/max = 2/2 = 1.0 (perfect tie)
    assert math.isclose(conflict, 1.0, rel_tol=1e-9)
    # Entropy at p=0.5 is exactly 1 bit
    assert math.isclose(entropy, 1.0, rel_tol=1e-9)


def test_aggregate_one_unanimous_positive_high_confidence() -> None:
    p, conflict, _ = aggregate_one(5, 0)
    # 5 positive: (1+5)/(2+5) = 6/7
    assert math.isclose(p, 6 / 7, rel_tol=1e-9)
    assert conflict == 0.0


def test_aggregate_one_negative_majority_under_50() -> None:
    p, _, _ = aggregate_one(0, 3)
    # 0 positive, 3 negative: (1+0)/(2+0+3) = 1/5 = 0.2
    assert math.isclose(p, 0.2, rel_tol=1e-9)


def test_aggregate_votes_includes_zero_vote_documents() -> None:
    # An empty docs list with no votes returns no aggregates; a doc with no
    # votes still gets a 0.5 probability so the corpus-wide view is complete.
    class V:
        def __init__(self, doc: str, vote: int) -> None:
            self.document_id = doc
            self.vote = vote

    aggs = aggregate_votes(
        document_ids=["d1", "d2", "d3"],
        tag_id="t",
        votes=[V("d1", 1), V("d1", 1), V("d2", -1)],
    )
    by_doc = {a.document_id: a for a in aggs}
    assert math.isclose(by_doc["d1"].probability, 3 / 4, rel_tol=1e-9)
    assert math.isclose(by_doc["d2"].probability, 1 / 3, rel_tol=1e-9)
    # Doc with no votes -> 0.5 prior, even though it never appeared in votes.
    assert by_doc["d3"].probability == 0.5
    assert by_doc["d3"].positive_votes == 0
    assert by_doc["d3"].negative_votes == 0


def test_lf_run_writes_probabilistic_labels() -> None:
    """End-to-end: an LF run should populate `ProbabilisticLabel` rows for
    every document in the project and the GET endpoint should expose them."""
    client = TestClient(app)
    project_id = _new_project(client, "ProbE2E")

    csv = (
        "text\n"
        "alpha apple banana\n"
        "alpha cherry date\n"
        "beta apple grape\n"
        "beta lemon mango\n"
    )
    doc_ids = _ingest(client, csv, project_id=project_id)
    assert len(doc_ids) == 4

    res = client.post(
        f"/v1/tags?project_id={project_id}",
        json={"name": f"fruit_{uuid.uuid4().hex[:8]}"},
    )
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

    res = client.get(
        "/v1/probabilistic-labels",
        params={"project_id": project_id, "tag_id": tag_id, "limit": 500},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 4
    assert body["run_id"] == run["id"]
    assert len(body["items"]) == 4
    by_doc = {item["document_id"]: item for item in body["items"]}

    # Two documents contained "apple": (1+1)/(2+1+0) = 2/3 -> predicted +1
    apple_docs = [doc_ids[0], doc_ids[2]]
    no_apple = [doc_ids[1], doc_ids[3]]
    for did in apple_docs:
        item = by_doc[did]
        assert math.isclose(item["probability"], 2 / 3, rel_tol=1e-9), item
        assert item["predicted"] == 1
        assert item["positive_votes"] == 1
        assert item["negative_votes"] == 0
        assert item["text_preview"]
    # No-apple docs: zero votes -> 0.5 -> predicted abstain (0)
    for did in no_apple:
        item = by_doc[did]
        assert item["probability"] == 0.5
        assert item["predicted"] == 0
        assert item["positive_votes"] == 0
        assert item["negative_votes"] == 0


def test_distribution_endpoint_summarizes_corpus() -> None:
    client = TestClient(app)
    project_id = _new_project(client, "ProbDist")

    csv = (
        "text\n"
        "alpha apple\n"
        "alpha apple\n"
        "alpha apple\n"
        "no fruit here\n"
    )
    doc_ids = _ingest(client, csv, project_id=project_id)
    assert len(doc_ids) == 4

    res = client.post(
        f"/v1/tags?project_id={project_id}",
        json={"name": f"fruit_{uuid.uuid4().hex[:8]}"},
    )
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
    lf_id = res.json()["id"]
    client.post("/v1/lf-runs", json={"tag_id": tag_id, "labeling_function_ids": [lf_id]})

    res = client.get(
        "/v1/probabilistic-labels/distribution",
        params={"project_id": project_id, "tag_id": tag_id, "bins": 10},
    )
    assert res.status_code == 200, res.text
    dist = res.json()
    assert dist["total"] == 4
    assert dist["predicted_positive"] == 3
    assert dist["predicted_abstain"] == 1
    assert dist["predicted_negative"] == 0
    # Bin counts sum to total.
    assert sum(b["count"] for b in dist["bins"]) == 4


def test_predicted_filter_narrows_to_positive() -> None:
    client = TestClient(app)
    project_id = _new_project(client, "ProbFilter")

    csv = "text\napple one\napple two\nplain three\n"
    _ingest(client, csv, project_id=project_id)

    res = client.post(
        f"/v1/tags?project_id={project_id}",
        json={"name": f"fruit_{uuid.uuid4().hex[:8]}"},
    )
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
    lf_id = res.json()["id"]
    client.post("/v1/lf-runs", json={"tag_id": tag_id, "labeling_function_ids": [lf_id]})

    res = client.get(
        "/v1/probabilistic-labels",
        params={
            "project_id": project_id,
            "tag_id": tag_id,
            "predicted": "positive",
        },
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 2
    assert all(it["predicted"] == 1 for it in items)


def test_rerun_overwrites_probabilities() -> None:
    """A second LF run for the same tag must replace the prior probabilities,
    not stack with them."""
    client = TestClient(app)
    project_id = _new_project(client, "ProbReRun")

    csv = "text\napple one\nplain two\n"
    doc_ids = _ingest(client, csv, project_id=project_id)

    res = client.post(
        f"/v1/tags?project_id={project_id}",
        json={"name": f"fruit_{uuid.uuid4().hex[:8]}"},
    )
    tag_id = res.json()["id"]

    # First LF: keyword "apple"
    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": "has_apple",
            "type": "keywords",
            "config": {"keywords": ["apple"], "mode": "any"},
        },
    )
    lf_apple = res.json()["id"]
    client.post("/v1/lf-runs", json={"tag_id": tag_id, "labeling_function_ids": [lf_apple]})

    # Second LF run with two LFs that both fire on "apple"
    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": "has_apple_2",
            "type": "keywords",
            "config": {"keywords": ["apple"], "mode": "any"},
        },
    )
    lf_apple_2 = res.json()["id"]
    client.post(
        "/v1/lf-runs",
        json={"tag_id": tag_id, "labeling_function_ids": [lf_apple, lf_apple_2]},
    )

    res = client.get(
        "/v1/probabilistic-labels",
        params={"project_id": project_id, "tag_id": tag_id},
    )
    items = res.json()["items"]
    assert len(items) == 2
    by_doc = {it["document_id"]: it for it in items}
    apple_doc = doc_ids[0]
    plain_doc = doc_ids[1]
    # After re-run with two firing LFs: (1+2)/(2+2) = 0.75
    assert math.isclose(by_doc[apple_doc]["probability"], 0.75, rel_tol=1e-9)
    assert by_doc[apple_doc]["positive_votes"] == 2
    # Plain doc still has zero votes -> 0.5
    assert by_doc[plain_doc]["probability"] == 0.5
