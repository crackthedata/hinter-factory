from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, GoldLabel, Tag

router = APIRouter(prefix="/v1/gold-labels", tags=["goldLabels"])


@router.get("")
def list_gold_labels(
    db: Annotated[Session, Depends(get_db)],
    document_id: str | None = None,
    document_ids: Annotated[list[str] | None, Query()] = None,
    tag_id: str | None = None,
):
    stmt = select(GoldLabel)
    if document_id:
        stmt = stmt.where(GoldLabel.document_id == document_id)
    if document_ids:
        stmt = stmt.where(GoldLabel.document_id.in_(document_ids))
    if tag_id:
        stmt = stmt.where(GoldLabel.tag_id == tag_id)
    stmt = stmt.order_by(GoldLabel.created_at.desc())
    rows = db.scalars(stmt).all()
    return [
        {
            "id": r.id,
            "document_id": r.document_id,
            "tag_id": r.tag_id,
            "value": int(r.value),
            "note": r.note,
            "created_at": r.created_at.isoformat() + "Z",
        }
        for r in rows
    ]


@router.post("", status_code=201)
def create_gold_label(payload: dict, db: Annotated[Session, Depends(get_db)]):
    document_id = str(payload.get("document_id", "")).strip()
    tag_id = str(payload.get("tag_id", "")).strip()
    value = payload.get("value")
    note = payload.get("note")

    if not document_id or not tag_id or value is None:
        raise HTTPException(status_code=400, detail="document_id, tag_id, and value are required")
    try:
        iv = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="value must be an integer") from exc
    if iv not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="value must be -1, 0, or 1 (negative, abstain, positive)")

    if not db.get(Document, document_id):
        raise HTTPException(status_code=404, detail="document not found")
    if not db.get(Tag, tag_id):
        raise HTTPException(status_code=404, detail="tag not found")

    existing = db.scalar(
        select(GoldLabel).where(
            GoldLabel.document_id == document_id,
            GoldLabel.tag_id == tag_id,
        )
    )
    if existing:
        existing.value = iv
        existing.note = str(note) if note is not None else None
        db.commit()
        db.refresh(existing)
        row = existing
    else:
        row = GoldLabel(document_id=document_id, tag_id=tag_id, value=iv, note=str(note) if note else None)
        db.add(row)
        db.commit()
        db.refresh(row)

    return {
        "id": row.id,
        "document_id": row.document_id,
        "tag_id": row.tag_id,
        "value": int(row.value),
        "note": row.note,
        "created_at": row.created_at.isoformat() + "Z",
    }
