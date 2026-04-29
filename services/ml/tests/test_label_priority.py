"""Tests for /v1/documents/label-priority active-learning ordering."""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient

from app.main import app


def _new_project(client: TestClient, prefix: str = "LP") -> str:
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


def _ingest_by_text(client: TestClient, csv: str, project_id: str) -> dict[str, str]:
    """Returns {doc_text: doc_id}. Single-batch ingests share timestamps, so
    list order isn't a reliable handle on individual docs."""
    files = {"file": ("docs.csv", io.BytesIO(csv.encode("utf-8")), "text/csv")}
    res = client.post(
        "/v1/documents/upload",
        files=files,
        data={"text_column": "text", "project_id": project_id},
    )
    assert res.status_code == 200, res.text
    res = client.get("/v1/documents", params={"limit": 500, "project_id": project_id})
    assert res.status_code == 200
    return {d["text"]: d["id"] for d in res.json()["items"]}


def _create_tag(client: TestClient, project_id: str, name: str) -> str:
    res = client.post(f"/v1/tags?project_id={project_id}", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _create_lf(
    client: TestClient,
    *,
    tag_id: str,
    name: str,
    keywords: list[str],
) -> str:
    res = client.post(
        "/v1/labeling-functions",
        json={
            "tag_id": tag_id,
            "name": name,
            "type": "keywords",
            "config": {"keywords": keywords, "mode": "any"},
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _run(client: TestClient, *, tag_id: str, lf_ids: list[str]) -> str:
    res = client.post(
        "/v1/lf-runs",
        json={"tag_id": tag_id, "labeling_function_ids": lf_ids},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "completed"
    return body["id"]


def _priority(
    client: TestClient,
    *,
    project_id: str,
    tag_id: str,
    mode: str,
    **extra: object,
) -> dict:
    params: dict[str, object] = {
        "project_id": project_id,
        "tag_id": tag_id,
        "mode": mode,
        **extra,
    }
    res = client.get("/v1/documents/label-priority", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_label_priority_no_run_returns_empty_with_message() -> None:
    """Before any LF run completes, priority lists are empty but informative."""
    client = TestClient(app)
    project_id = _new_project(client, "NoRun")
    _ingest(client, "text\nalpha\nbeta\n", project_id=project_id)
    tag_id = _create_tag(client, project_id, f"t_{uuid.uuid4().hex[:6]}")

    body = _priority(client, project_id=project_id, tag_id=tag_id, mode="uncertain")
    assert body["run_id"] is None
    assert body["items"] == []
    assert body["message"]


def test_label_priority_uncertain_sorts_by_abs_vote_sum_then_count() -> None:
    """Closest splits surface first. With +1-only LFs, |sum| == count, so the
    doc only one LF voted on is the most uncertain."""
    client = TestClient(app)
    project_id = _new_project(client, "Unc")
    csv = (
        "text\n"
        "apple zzz\n"             # 1 LF fires -> |sum|=1, count=1 (most uncertain)
        "apple banana qqq\n"      # 2 LFs fire -> |sum|=2, count=2
        "apple banana cherry\n"   # 3 LFs fire -> |sum|=3, count=3 (most confident)
        "qqq\n"                   # 0 LFs fire  -> excluded from `uncertain`
    )
    by_text = _ingest_by_text(client, csv, project_id=project_id)
    tag_id = _create_tag(client, project_id, f"unc_{uuid.uuid4().hex[:6]}")

    pos_apple = _create_lf(client, tag_id=tag_id, name="pos_apple", keywords=["apple"])
    pos_banana = _create_lf(client, tag_id=tag_id, name="pos_banana", keywords=["banana"])
    pos_cherry = _create_lf(client, tag_id=tag_id, name="pos_cherry", keywords=["cherry"])
    _run(client, tag_id=tag_id, lf_ids=[pos_apple, pos_banana, pos_cherry])

    body = _priority(
        client, project_id=project_id, tag_id=tag_id, mode="uncertain", limit=10
    )
    items = body["items"]
    by_id = {r["id"]: r for r in items}

    az = by_id[by_text["apple zzz"]]
    assert az["vote_sum"] == 1 and az["vote_count"] == 1, az
    abq = by_id[by_text["apple banana qqq"]]
    assert abq["vote_sum"] == 2 and abq["vote_count"] == 2, abq
    abc = by_id[by_text["apple banana cherry"]]
    assert abc["vote_sum"] == 3 and abc["vote_count"] == 3, abc

    ordered_ids = [r["id"] for r in items]
    assert ordered_ids[0] == by_text["apple zzz"], items
    assert ordered_ids[1] == by_text["apple banana qqq"], items
    assert ordered_ids[2] == by_text["apple banana cherry"], items
    assert by_text["qqq"] not in ordered_ids, items
    # Confirm vote breakdown only includes non-zero votes (no abstains leak in).
    assert {v["labeling_function_name"] for v in abq["votes"]} == {"pos_apple", "pos_banana"}


def test_label_priority_no_lf_fires_lists_only_zero_vote_docs() -> None:
    """`no_lf_fires` mode is the coverage-hole list."""
    client = TestClient(app)
    project_id = _new_project(client, "NoFire")
    csv = "text\napple one\napple two\nzzz nothing\nyyy nothing\n"
    by_text = _ingest_by_text(client, csv, project_id=project_id)
    tag_id = _create_tag(client, project_id, f"nf_{uuid.uuid4().hex[:6]}")
    lf = _create_lf(client, tag_id=tag_id, name="apple", keywords=["apple"])
    _run(client, tag_id=tag_id, lf_ids=[lf])

    body = _priority(
        client, project_id=project_id, tag_id=tag_id, mode="no_lf_fires", limit=10
    )
    listed = {r["id"] for r in body["items"]}
    assert listed == {by_text["zzz nothing"], by_text["yyy nothing"]}, body
    for item in body["items"]:
        assert item["vote_count"] == 0
        assert item["vote_sum"] == 0
        assert item["votes"] == []


def test_label_priority_weak_positive_only_lists_single_vote_positives() -> None:
    """`weak_positive` requires sum>=1 AND exactly one LF voting."""
    client = TestClient(app)
    project_id = _new_project(client, "Weak")
    csv = (
        "text\n"
        "apple alone\n"          # +1 from apple, count 1 -> weak positive
        "apple banana\n"         # +2 from apple+banana, count 2 -> NOT weak
        "banana alone\n"         # +1 from banana, count 1 -> weak positive
        "qqq nothing\n"          # 0 votes -> excluded
    )
    by_text = _ingest_by_text(client, csv, project_id=project_id)
    tag_id = _create_tag(client, project_id, f"wp_{uuid.uuid4().hex[:6]}")
    apple = _create_lf(client, tag_id=tag_id, name="apple", keywords=["apple"])
    banana = _create_lf(client, tag_id=tag_id, name="banana", keywords=["banana"])
    _run(client, tag_id=tag_id, lf_ids=[apple, banana])

    body = _priority(
        client, project_id=project_id, tag_id=tag_id, mode="weak_positive", limit=10
    )
    listed = {r["id"] for r in body["items"]}
    assert listed == {by_text["apple alone"], by_text["banana alone"]}, body
    for item in body["items"]:
        assert item["vote_count"] == 1
        assert item["vote_sum"] == 1


def test_label_priority_excludes_already_labeled_docs() -> None:
    """Once a doc has a gold label for this tag, it disappears from priority lists."""
    client = TestClient(app)
    project_id = _new_project(client, "Skip")
    csv = "text\napple one\napple two\napple three\n"
    by_text = _ingest_by_text(client, csv, project_id=project_id)
    tag_id = _create_tag(client, project_id, f"skip_{uuid.uuid4().hex[:6]}")
    lf = _create_lf(client, tag_id=tag_id, name="apple", keywords=["apple"])
    _run(client, tag_id=tag_id, lf_ids=[lf])

    before = _priority(client, project_id=project_id, tag_id=tag_id, mode="uncertain")
    assert {r["id"] for r in before["items"]} == set(by_text.values())
    assert before["total"] == 3

    res = client.post(
        "/v1/gold-labels",
        json={"document_id": by_text["apple one"], "tag_id": tag_id, "value": 1},
    )
    assert res.status_code == 201, res.text

    after = _priority(client, project_id=project_id, tag_id=tag_id, mode="uncertain")
    assert {r["id"] for r in after["items"]} == {
        by_text["apple two"],
        by_text["apple three"],
    }
    assert after["total"] == 2


def test_label_priority_respects_explore_filters_and_pagination() -> None:
    """`q`, `length_bucket`, and `limit/offset` compose with the priority sort."""
    client = TestClient(app)
    project_id = _new_project(client, "Filter")
    long_text = "apple " + ("really long text " * 30)
    medium_text = "apple medium " + ("x " * 30)
    csv = "text\n" + "apple short\n" + medium_text + "\n" + long_text + "\n"
    by_text = _ingest_by_text(client, csv, project_id=project_id)
    tag_id = _create_tag(client, project_id, f"f_{uuid.uuid4().hex[:6]}")
    lf = _create_lf(client, tag_id=tag_id, name="apple", keywords=["apple"])
    _run(client, tag_id=tag_id, lf_ids=[lf])

    body = _priority(
        client,
        project_id=project_id,
        tag_id=tag_id,
        mode="uncertain",
        q="apple",
        limit=2,
        offset=0,
    )
    assert len(body["items"]) == 2
    assert body["total"] == 3

    page2 = _priority(
        client,
        project_id=project_id,
        tag_id=tag_id,
        mode="uncertain",
        q="apple",
        limit=2,
        offset=2,
    )
    assert len(page2["items"]) == 1
    assert page2["total"] == 3

    res = client.get(
        "/v1/documents/label-priority",
        params=[
            ("project_id", project_id),
            ("tag_id", tag_id),
            ("mode", "uncertain"),
            ("length_bucket", "long"),
        ],
    )
    assert res.status_code == 200, res.text
    long_only = res.json()
    # Map text -> id by prefix to dodge any CSV trailing-whitespace stripping.
    long_id = next(
        doc_id for text, doc_id in by_text.items() if text.startswith("apple really long")
    )
    assert {r["id"] for r in long_only["items"]} == {long_id}, long_only


def test_label_priority_validates_inputs() -> None:
    client = TestClient(app)
    project_id = _new_project(client, "Val")
    tag_id = _create_tag(client, project_id, f"v_{uuid.uuid4().hex[:6]}")

    res = client.get(
        "/v1/documents/label-priority",
        params={"project_id": project_id, "tag_id": tag_id, "mode": "bogus"},
    )
    assert res.status_code == 400, res.text

    res = client.get(
        "/v1/documents/label-priority",
        params={"project_id": project_id, "tag_id": "missing", "mode": "uncertain"},
    )
    assert res.status_code == 404, res.text

    other = _new_project(client, "Other")
    alien = _create_tag(client, other, f"alien_{uuid.uuid4().hex[:6]}")
    res = client.get(
        "/v1/documents/label-priority",
        params={"project_id": project_id, "tag_id": alien, "mode": "uncertain"},
    )
    assert res.status_code == 404, res.text
