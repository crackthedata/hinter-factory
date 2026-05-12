from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.lf_executor import LfConfigError, execute_labeling_function
from app.models import Document, LabelingFunction, LfRun, LfRunLabelingFunction, LfRunVote, Tag
from app.probabilistic_aggregator import write_probabilistic_labels_for_run
from app.project_scope import resolve_project_id

router = APIRouter(prefix="/v1/lf-runs", tags=["lfRuns"])

# Flush votes to DB after this many documents to bound peak memory usage.
_VOTE_FLUSH_BATCH = 500


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
    db.flush()

    # Extract everything we need from ORM objects into plain Python values so
    # that the execution loop below never touches the SQLAlchemy identity map
    # and we can safely expunge between batches without DetachedInstanceError.
    run_id = run.id
    project_id = run.project_id
    lf_specs = [
        {"id": lf.id, "name": lf.name, "type": lf.type, "config": dict(lf.config or {})}
        for lf in lf_rows
    ]
    lf_ids_for_result = [lf.id for lf in lf_rows]

    # Select only the columns we need so results are plain Row objects that
    # never enter the ORM identity map — safe to iterate while flushing votes.
    doc_stmt = (
        select(Document.id, Document.text)
        .where(Document.project_id == tag.project_id)
        .order_by(Document.created_at.asc())
    )

    pending_votes: list[LfRunVote] = []
    total_votes = 0
    scanned = 0
    fatal: str | None = None

    for row in db.execute(doc_stmt):
        doc_id: str = row.id
        doc_text: str = row.text
        scanned += 1
        for spec in lf_specs:
            try:
                vote = execute_labeling_function(spec["type"], spec["config"], doc_text)
            except LfConfigError as exc:
                fatal = str(exc)
                break
            except Exception as exc:  # noqa: BLE001
                fatal = f"unexpected error in LF '{spec['name']}': {type(exc).__name__}: {exc}"
                break
            pending_votes.append(
                LfRunVote(
                    run_id=run_id,
                    document_id=doc_id,
                    labeling_function_id=spec["id"],
                    vote=int(vote),
                )
            )
        if fatal:
            break

        # Flush votes periodically and evict them from the identity map so
        # SQLAlchemy doesn't accumulate millions of ORM objects in memory.
        if len(pending_votes) >= _VOTE_FLUSH_BATCH:
            db.add_all(pending_votes)
            db.flush()
            for v in pending_votes:
                db.expunge(v)
            total_votes += len(pending_votes)
            pending_votes = []

    if fatal:
        run.status = "failed"
        run.error = fatal
        run.documents_scanned = scanned
        run.completed_at = datetime.utcnow()
        db.commit()
        return _serialize_run(run, lf_ids_for_result)

    # Flush the final batch.
    if pending_votes:
        db.add_all(pending_votes)
        db.flush()
        total_votes += len(pending_votes)

    run.status = "completed"
    run.documents_scanned = scanned
    run.votes_written = total_votes
    run.completed_at = datetime.utcnow()
    # Flush so the aggregator can read the votes back via the session.
    db.flush()
    try:
        write_probabilistic_labels_for_run(
            db,
            project_id=project_id,
            tag_id=tag_id,
            run_id=run_id,
        )
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = f"aggregation error: {type(exc).__name__}: {exc}"
        db.commit()
        db.refresh(run)
        return _serialize_run(run, lf_ids_for_result)
    db.commit()
    db.refresh(run)
    return _serialize_run(run, lf_ids_for_result)


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
