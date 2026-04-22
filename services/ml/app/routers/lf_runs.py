from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.lf_executor import LfConfigError, execute_labeling_function
from app.models import Document, LabelingFunction, LfRun, LfRunLabelingFunction, LfRunVote, Tag
from app.project_scope import resolve_project_id

router = APIRouter(prefix="/v1/lf-runs", tags=["lfRuns"])


@router.post("", status_code=202)
def create_lf_run(payload: dict, db: Annotated[Session, Depends(get_db)]):
    tag_id = str(payload.get("tag_id", "")).strip()
    lf_ids = payload.get("labeling_function_ids")
    if not tag_id or not isinstance(lf_ids, list) or not lf_ids:
        raise HTTPException(status_code=400, detail="tag_id and labeling_function_ids are required")

    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="tag not found")

    lf_rows: list[LabelingFunction] = []
    for raw_id in lf_ids:
        lf_id = str(raw_id)
        lf = db.get(LabelingFunction, lf_id)
        if not lf:
            raise HTTPException(status_code=404, detail=f"labeling function not found: {lf_id}")
        if lf.tag_id != tag_id:
            raise HTTPException(status_code=400, detail=f"LF {lf_id} does not belong to tag {tag_id}")
        if not lf.enabled:
            raise HTTPException(status_code=400, detail=f"LF {lf_id} is disabled")
        lf_rows.append(lf)

    run = LfRun(
        project_id=tag.project_id,
        tag_id=tag_id,
        status="running",
        documents_scanned=0,
        votes_written=0,
    )
    db.add(run)
    db.flush()

    for pos, lf in enumerate(lf_rows):
        db.add(LfRunLabelingFunction(run_id=run.id, labeling_function_id=lf.id, position=pos))

    docs = list(
        db.scalars(
            select(Document)
            .where(Document.project_id == tag.project_id)
            .order_by(Document.created_at.asc())
        )
    )
    votes: list[LfRunVote] = []
    scanned = 0
    fatal: str | None = None

    for doc in docs:
        scanned += 1
        for lf in lf_rows:
            try:
                vote = execute_labeling_function(lf.type, dict(lf.config or {}), doc.text)
            except LfConfigError as exc:
                fatal = str(exc)
                break
            votes.append(
                LfRunVote(
                    run_id=run.id,
                    document_id=doc.id,
                    labeling_function_id=lf.id,
                    vote=int(vote),
                )
            )
        if fatal:
            break

    if fatal:
        run.status = "failed"
        run.error = fatal
        run.documents_scanned = scanned
        run.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        return _serialize_run(run, [lf.id for lf in lf_rows])

    db.add_all(votes)
    run.status = "completed"
    run.documents_scanned = scanned
    run.votes_written = len(votes)
    run.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return _serialize_run(run, [lf.id for lf in lf_rows])


@router.get("")
def list_lf_runs(
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
    tag_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    project_id = resolve_project_id(db, project_id)
    stmt = select(LfRun).where(LfRun.project_id == project_id)
    if tag_id:
        stmt = stmt.where(LfRun.tag_id == tag_id)
    if status:
        stmt = stmt.where(LfRun.status == status)
    stmt = stmt.order_by(LfRun.created_at.desc()).limit(limit)
    runs = list(db.scalars(stmt))
    return [_serialize_run(r, _ordered_lf_ids(db, r.id)) for r in runs]


@router.get("/{run_id}")
def get_lf_run(run_id: str, db: Annotated[Session, Depends(get_db)]):
    run = db.get(LfRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    lf_ids = _ordered_lf_ids(db, run_id)
    return _serialize_run(run, lf_ids)


@router.get("/{run_id}/matrix")
def export_matrix(run_id: str, db: Annotated[Session, Depends(get_db)]):
    run = db.get(LfRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != "completed":
        raise HTTPException(status_code=409, detail="run is not completed")

    lf_ids = _ordered_lf_ids(db, run_id)
    doc_ids = [
        d.id
        for d in db.scalars(
            select(Document)
            .where(Document.project_id == run.project_id)
            .order_by(Document.created_at.asc())
        )
    ]

    doc_index = {did: i for i, did in enumerate(doc_ids)}
    lf_index = {lid: i for i, lid in enumerate(lf_ids)}

    votes = db.scalars(select(LfRunVote).where(LfRunVote.run_id == run_id)).all()
    entries: list[dict] = []
    for v in votes:
        if v.document_id not in doc_index or v.labeling_function_id not in lf_index:
            continue
        entries.append(
            {"d": doc_index[v.document_id], "l": lf_index[v.labeling_function_id], "v": int(v.vote)}
        )

    return {
        "run_id": run.id,
        "document_ids": doc_ids,
        "labeling_function_ids": lf_ids,
        "entries": entries,
    }


def _ordered_lf_ids(db: Session, run_id: str) -> list[str]:
    rows = db.scalars(
        select(LfRunLabelingFunction)
        .where(LfRunLabelingFunction.run_id == run_id)
        .order_by(LfRunLabelingFunction.position.asc())
    ).all()
    return [r.labeling_function_id for r in rows]


def _serialize_run(run: LfRun, lf_ids: list[str]) -> dict:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "tag_id": run.tag_id,
        "labeling_function_ids": lf_ids,
        "status": run.status,
        "error": run.error,
        "documents_scanned": run.documents_scanned,
        "votes_written": run.votes_written,
        "created_at": run.created_at.isoformat() + "Z",
        "completed_at": run.completed_at.isoformat() + "Z" if run.completed_at else None,
    }
