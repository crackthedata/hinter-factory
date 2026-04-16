from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Tag

router = APIRouter(prefix="/v1/tags", tags=["tags"])


@router.get("")
def list_tags(db: Annotated[Session, Depends(get_db)]):
    rows = db.scalars(select(Tag).order_by(Tag.created_at.desc())).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "taxonomy_version": t.taxonomy_version,
            "created_at": t.created_at.isoformat() + "Z",
        }
        for t in rows
    ]


@router.post("", status_code=201)
def create_tag(payload: dict, db: Annotated[Session, Depends(get_db)]):
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    taxonomy_version = str(payload.get("taxonomy_version") or "v1")
    existing = db.scalar(select(Tag).where(Tag.name == name))
    if existing:
        raise HTTPException(status_code=409, detail="tag name already exists")
    tag = Tag(name=name, taxonomy_version=taxonomy_version)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {
        "id": tag.id,
        "name": tag.name,
        "taxonomy_version": tag.taxonomy_version,
        "created_at": tag.created_at.isoformat() + "Z",
    }
