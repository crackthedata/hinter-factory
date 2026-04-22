from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProbabilisticLabel, Tag
from app.project_scope import resolve_project_id

router = APIRouter(prefix="/v1/probabilistic-labels", tags=["probabilisticLabels"])


@router.get("")
def list_probabilistic_labels(
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
    tag_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    project_id = resolve_project_id(db, project_id)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    stmt = select(ProbabilisticLabel).where(ProbabilisticLabel.project_id == project_id)
    if tag_id:
        stmt = stmt.where(ProbabilisticLabel.tag_id == tag_id)
    stmt = stmt.order_by(ProbabilisticLabel.updated_at.desc()).offset(offset).limit(limit)
    rows = db.scalars(stmt).all()
    return [
        {
            "document_id": r.document_id,
            "tag_id": r.tag_id,
            "probability": r.probability,
            "conflict_score": r.conflict_score,
            "entropy": r.entropy,
            "updated_at": r.updated_at.isoformat() + "Z",
        }
        for r in rows
    ]


@router.post("")
def upsert_probabilistic_label(payload: dict, db: Annotated[Session, Depends(get_db)]):
    document_id = str(payload.get("document_id", "")).strip()
    tag_id = str(payload.get("tag_id", "")).strip()
    probability = payload.get("probability")
    if not document_id or not tag_id or probability is None:
        raise HTTPException(status_code=400, detail="document_id, tag_id, and probability are required")
    try:
        p = float(probability)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="probability must be a number") from exc
    if p < 0 or p > 1:
        raise HTTPException(status_code=400, detail="probability must be between 0 and 1")

    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="tag not found")

    row = db.scalar(
        select(ProbabilisticLabel).where(
            ProbabilisticLabel.document_id == document_id,
            ProbabilisticLabel.tag_id == tag_id,
        )
    )
    conflict = payload.get("conflict_score")
    entropy = payload.get("entropy")
    if row:
        row.probability = p
        row.conflict_score = float(conflict) if conflict is not None else None
        row.entropy = float(entropy) if entropy is not None else None
        row.updated_at = datetime.utcnow()
    else:
        row = ProbabilisticLabel(
            project_id=tag.project_id,
            document_id=document_id,
            tag_id=tag_id,
            probability=p,
            conflict_score=float(conflict) if conflict is not None else None,
            entropy=float(entropy) if entropy is not None else None,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "document_id": row.document_id,
        "tag_id": row.tag_id,
        "probability": row.probability,
        "conflict_score": row.conflict_score,
        "entropy": row.entropy,
        "updated_at": row.updated_at.isoformat() + "Z",
    }
