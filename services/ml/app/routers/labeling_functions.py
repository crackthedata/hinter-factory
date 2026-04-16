from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.lf_executor import LfConfigError, execute_labeling_function
from app.models import Document, LabelingFunction, Tag

router = APIRouter(prefix="/v1/labeling-functions", tags=["labelingFunctions"])


ALLOWED_TYPES = {"regex", "keywords", "structural", "zeroshot", "llm_prompt"}


@router.get("")
def list_labeling_functions(
    db: Annotated[Session, Depends(get_db)],
    tag_id: str | None = None,
):
    stmt = select(LabelingFunction)
    if tag_id:
        stmt = stmt.where(LabelingFunction.tag_id == tag_id)
    stmt = stmt.order_by(LabelingFunction.created_at.desc())
    rows = db.scalars(stmt).all()
    return [_serialize(lf) for lf in rows]


@router.post("", status_code=201)
def create_labeling_function(payload: dict, db: Annotated[Session, Depends(get_db)]):
    tag_id = str(payload.get("tag_id", "")).strip()
    name = str(payload.get("name", "")).strip()
    lf_type = str(payload.get("type", "")).strip()
    config = payload.get("config")
    enabled = bool(payload.get("enabled", True))

    if not tag_id or not name or not lf_type:
        raise HTTPException(status_code=400, detail="tag_id, name, and type are required")
    if lf_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported type: {lf_type}")
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be an object")

    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="tag not found")

    lf = LabelingFunction(tag_id=tag_id, name=name, type=lf_type, config=config, enabled=enabled)
    db.add(lf)
    db.commit()
    db.refresh(lf)
    return _serialize(lf)


@router.patch("/{labeling_function_id}")
def update_labeling_function(
    labeling_function_id: str,
    payload: dict,
    db: Annotated[Session, Depends(get_db)],
):
    lf = db.get(LabelingFunction, labeling_function_id)
    if not lf:
        raise HTTPException(status_code=404, detail="labeling function not found")
    if "name" in payload and payload["name"] is not None:
        lf.name = str(payload["name"])
    if "config" in payload and payload["config"] is not None:
        if not isinstance(payload["config"], dict):
            raise HTTPException(status_code=400, detail="config must be an object")
        lf.config = payload["config"]
    if "enabled" in payload and payload["enabled"] is not None:
        lf.enabled = bool(payload["enabled"])
    db.commit()
    db.refresh(lf)
    return _serialize(lf)


@router.delete("/{labeling_function_id}", status_code=204)
def delete_labeling_function(labeling_function_id: str, db: Annotated[Session, Depends(get_db)]):
    lf = db.get(LabelingFunction, labeling_function_id)
    if not lf:
        raise HTTPException(status_code=404, detail="labeling function not found")
    db.delete(lf)
    db.commit()
    return None


@router.post("/{labeling_function_id}/preview")
def preview_labeling_function(
    labeling_function_id: str,
    db: Annotated[Session, Depends(get_db)],
    payload: dict | None = None,
):
    lf = db.get(LabelingFunction, labeling_function_id)
    if not lf:
        raise HTTPException(status_code=404, detail="labeling function not found")
    body = payload or {}
    limit = int(body.get("limit") or 25)
    limit = max(1, min(limit, 200))
    doc_ids = body.get("document_ids")
    stmt = select(Document).order_by(Document.created_at.desc())
    if isinstance(doc_ids, list) and doc_ids:
        stmt = stmt.where(Document.id.in_([str(x) for x in doc_ids]))
    docs = list(db.scalars(stmt.limit(limit)))

    rows: list[dict] = []
    for d in docs:
        preview = d.text if len(d.text) <= 280 else d.text[:277] + "..."
        try:
            vote = execute_labeling_function(lf.type, dict(lf.config or {}), d.text)
        except LfConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rows.append({"document_id": d.id, "vote": vote, "text_preview": preview})
    return {"rows": rows}


def _serialize(lf: LabelingFunction) -> dict:
    return {
        "id": lf.id,
        "tag_id": lf.tag_id,
        "name": lf.name,
        "type": lf.type,
        "config": dict(lf.config or {}),
        "enabled": lf.enabled,
        "created_at": lf.created_at.isoformat() + "Z",
    }
