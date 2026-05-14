"""Topic modeling endpoints.

POST /v1/topic-models           – start a new topic model run (background)
GET  /v1/topic-models           – list runs for a project
GET  /v1/topic-models/{id}      – fetch a run (with topics once completed)
DELETE /v1/topic-models/{id}    – delete a run
GET  /v1/topic-models/{id}/suggestions – keyword suggestions for a tag
"""
from __future__ import annotations

import threading
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Tag, TopicModel
from app.project_scope import resolve_project_id
from app.topic_modeling import get_topic_suggestions, run_topic_model

router = APIRouter(prefix="/v1/topic-models", tags=["topicModels"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TopicModelCreate(BaseModel):
    n_topics: int = Field(10, ge=2, le=100, description="Number of topics to discover")
    algorithm: str = Field("lda", description="'lda' or 'nmf'")
    max_features: int = Field(5000, ge=100, le=50000, description="Vocabulary size cap")


class TopicWordOut(BaseModel):
    word: str
    weight: float


class TopicOut(BaseModel):
    id: int
    top_words: list[TopicWordOut]


class TopicModelOut(BaseModel):
    id: str
    project_id: str
    n_topics: int
    algorithm: str
    max_features: int
    status: str
    error: str | None
    documents_processed: int
    created_at: str
    completed_at: str | None
    topics: list[TopicOut] | None = None


class TopicSuggestionOut(BaseModel):
    word: str
    score: float


class RelevantTopicOut(BaseModel):
    topic_id: int
    relevance_score: float
    top_words: list[TopicWordOut]


class TopicSuggestionsResponse(BaseModel):
    relevant_topics: list[RelevantTopicOut]
    suggestions: list[TopicSuggestionOut]
    basis: str  # "gold" | "corpus" | "no_model"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize(tm: TopicModel, include_topics: bool = False) -> TopicModelOut:
    topics = None
    if include_topics and tm.topics_json:
        topics = [
            TopicOut(
                id=t["id"],
                top_words=[TopicWordOut(**w) for w in t["top_words"]],
            )
            for t in tm.topics_json
        ]
    return TopicModelOut(
        id=tm.id,
        project_id=tm.project_id,
        n_topics=tm.n_topics,
        algorithm=tm.algorithm,
        max_features=tm.max_features,
        status=tm.status,
        error=tm.error,
        documents_processed=tm.documents_processed,
        created_at=tm.created_at.isoformat(),
        completed_at=tm.completed_at.isoformat() if tm.completed_at else None,
        topics=topics,
    )


def _run_in_thread(project_id: str, model_id: str) -> None:
    """Open a fresh DB session and execute topic modeling outside the request thread."""
    db = SessionLocal()
    try:
        run_topic_model(db, project_id=project_id, model_id=model_id)
    except Exception as exc:  # noqa: BLE001
        # Catch anything not already handled inside run_topic_model (e.g. missing deps).
        try:
            tm = db.get(TopicModel, model_id)
            if tm and tm.status not in ("completed", "failed"):
                tm.status = "failed"
                tm.error = f"{type(exc).__name__}: {exc}"
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=TopicModelOut, status_code=202)
def create_topic_model(
    body: TopicModelCreate,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
):
    """Start a topic model run for the active project.

    Returns immediately (HTTP 202) with a ``pending`` record. Poll the
    ``GET /v1/topic-models/{id}`` endpoint until ``status`` is
    ``completed`` or ``failed``.
    """
    project_id = resolve_project_id(db, project_id)
    algorithm = body.algorithm.lower()
    if algorithm not in ("lda", "nmf"):
        raise HTTPException(status_code=400, detail="algorithm must be 'lda' or 'nmf'")

    tm = TopicModel(
        project_id=project_id,
        n_topics=body.n_topics,
        algorithm=algorithm,
        max_features=body.max_features,
        status="pending",
    )
    db.add(tm)
    db.commit()
    db.refresh(tm)

    # Spin up a daemon thread so the DB session used here isn't shared.
    t = threading.Thread(target=_run_in_thread, args=(project_id, tm.id), daemon=True)
    t.start()

    return _serialize(tm)


@router.get("", response_model=list[TopicModelOut])
def list_topic_models(
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
):
    project_id = resolve_project_id(db, project_id)
    rows = db.scalars(
        select(TopicModel)
        .where(TopicModel.project_id == project_id)
        .order_by(TopicModel.created_at.desc())
    ).all()
    return [_serialize(tm) for tm in rows]


@router.get("/{model_id}", response_model=TopicModelOut)
def get_topic_model(
    model_id: str,
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
):
    project_id = resolve_project_id(db, project_id)
    tm = db.get(TopicModel, model_id)
    if not tm or tm.project_id != project_id:
        raise HTTPException(status_code=404, detail="topic model not found")
    return _serialize(tm, include_topics=True)


@router.delete("/{model_id}", status_code=204)
def delete_topic_model(
    model_id: str,
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
):
    project_id = resolve_project_id(db, project_id)
    tm = db.get(TopicModel, model_id)
    if not tm or tm.project_id != project_id:
        raise HTTPException(status_code=404, detail="topic model not found")
    db.delete(tm)
    db.commit()


@router.get("/{model_id}/suggestions", response_model=TopicSuggestionsResponse)
def topic_suggestions(
    model_id: str,
    tag_id: str,
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    exclude: Annotated[list[str] | None, Query()] = None,
):
    """Get keyword suggestions from a completed topic model for a specific tag.

    Uses gold labels (if available) to identify which topics are most aligned
    with the tag, then surfaces top words from those topics as hinter candidates.
    """
    project_id = resolve_project_id(db, project_id)

    tm = db.get(TopicModel, model_id)
    if not tm or tm.project_id != project_id:
        raise HTTPException(status_code=404, detail="topic model not found")
    if tm.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"topic model is not completed yet (status: {tm.status})",
        )

    tag = db.get(Tag, tag_id)
    if not tag or tag.project_id != project_id:
        raise HTTPException(status_code=404, detail="tag not found")

    result = get_topic_suggestions(
        db,
        model_id=model_id,
        tag_id=tag_id,
        limit=limit,
        exclude=set(exclude) if exclude else None,
    )
    return TopicSuggestionsResponse(**result)
