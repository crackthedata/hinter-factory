from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Tag
from app.project_scope import resolve_project_id

router = APIRouter(prefix="/v1/tags", tags=["tags"])


@router.get("")
def list_tags(
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
):
    project_id = resolve_project_id(db, project_id)
    rows = db.scalars(
        select(Tag).where(Tag.project_id == project_id).order_by(Tag.created_at.desc())
    ).all()
    return [_serialize(t) for t in rows]


@router.post("", status_code=201)
def create_tag(
    payload: dict,
    db: Annotated[Session, Depends(get_db)],
    project_id: Annotated[str | None, Query()] = None,
):
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    taxonomy_version = str(payload.get("taxonomy_version") or "v1")
    # See docs/notes-ml.md#servicesmlapprouterstagspy for query-vs-body project_id precedence.
    project_id = resolve_project_id(db, project_id or payload.get("project_id"))
    existing = db.scalar(
        select(Tag).where(Tag.project_id == project_id, Tag.name == name)
    )
    if existing:
        raise HTTPException(status_code=409, detail="tag name already exists in this project")
    tag = Tag(project_id=project_id, name=name, taxonomy_version=taxonomy_version)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return _serialize(tag)


def _serialize(t: Tag) -> dict:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "name": t.name,
        "taxonomy_version": t.taxonomy_version,
        "created_at": t.created_at.isoformat() + "Z",
    }
