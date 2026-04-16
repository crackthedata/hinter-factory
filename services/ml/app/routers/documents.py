from __future__ import annotations

import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingest import IngestError, parse_csv_bytes, parse_json_bytes
from app.models import Document

router = APIRouter(prefix="/v1/documents", tags=["documents"])


def _should_parse_as_csv(filename: str | None, content_type: str | None) -> bool:
    """Detect CSV uploads; Windows/Excel often omits 'csv' from Content-Type."""
    name = (filename or "").lower()
    ct = (content_type or "").lower()
    if name.endswith(".json"):
        return False
    if name.endswith(".csv"):
        return True
    if "csv" in ct or ct == "text/csv":
        return True
    # Excel on Windows frequently labels comma-separated exports as:
    if ct in ("application/vnd.ms-excel", "application/vnd.ms-excel.sheet.macroenabled.12"):
        return True
    if ct in ("text/plain", "application/octet-stream") and name.endswith(".csv"):
        return True
    return False


def _should_parse_as_json(filename: str | None, content_type: str | None) -> bool:
    name = (filename or "").lower()
    ct = (content_type or "").lower()
    return name.endswith(".json") or "json" in ct


def _length_clause(bucket: str):
    if bucket == "short":
        return Document.char_length < 100
    if bucket == "medium":
        return (Document.char_length >= 100) & (Document.char_length < 500)
    if bucket == "long":
        return Document.char_length >= 500
    raise HTTPException(status_code=400, detail=f"unknown length bucket: {bucket}")


def _validate_metadata_key(key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", key or ""):
        raise HTTPException(status_code=400, detail="metadata_key must be alphanumeric/underscore")
    return key


@router.post("/upload")
def upload_documents(
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    text_column: Annotated[str, Form()] = "text",
    id_column: Annotated[str | None, Form()] = None,
):
    raw = file.file.read()
    name = file.filename
    ct = file.content_type
    try:
        if _should_parse_as_json(name, ct):
            items, errors = parse_json_bytes(raw)
        elif _should_parse_as_csv(name, ct):
            items, errors = parse_csv_bytes(raw, text_column=text_column, id_column=id_column or None)
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not detect CSV or JSON from filename or Content-Type. "
                    "Use a .csv or .json extension, or export CSV as UTF-8 from Excel."
                ),
            )
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"could not parse file: {exc}") from exc

    inserted = 0
    skipped = 0
    for row in items:
        existing = db.get(Document, row["id"])
        if existing:
            existing.text = row["text"]
            existing.metadata_json = row["metadata"]
            existing.char_length = len(row["text"])
            skipped += 1
        else:
            db.add(
                Document(
                    id=row["id"],
                    text=row["text"],
                    metadata_json=row["metadata"],
                    char_length=len(row["text"]),
                )
            )
            inserted += 1
    db.commit()
    return {"inserted": inserted, "skipped": skipped, "errors": errors}


@router.get("/facets/metadata-keys")
def metadata_keys(db: Annotated[Session, Depends(get_db)]):
    docs = db.scalars(select(Document).limit(5000)).all()
    keys: set[str] = set()
    for d in docs:
        if isinstance(d.metadata_json, dict):
            keys.update(d.metadata_json.keys())
    return sorted(keys)


@router.get("/facets/metadata-values")
def metadata_values(
    db: Annotated[Session, Depends(get_db)],
    key: str,
    limit: int = 100,
):
    key = _validate_metadata_key(key)
    limit = max(1, min(limit, 500))
    stmt = text(
        """
        SELECT DISTINCT CAST(je.value AS TEXT) AS v
        FROM documents d, json_each(d.metadata) AS je
        WHERE je.key = :k AND json_type(je.value) IN ('text','integer','real','true','false')
        ORDER BY v
        LIMIT :lim
        """
    )
    rows = db.execute(stmt, {"k": key, "lim": limit}).all()
    return [r[0] for r in rows if r[0] is not None]


@router.get("")
def list_documents(
    db: Annotated[Session, Depends(get_db)],
    q: str | None = None,
    length_bucket: list[str] | None = Query(None),
    metadata_key: str | None = None,
    metadata_value: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    stmt = select(Document)
    count_stmt = select(func.count()).select_from(Document)

    if q:
        like = f"%{q}%"
        stmt = stmt.where(Document.text.ilike(like))
        count_stmt = count_stmt.where(Document.text.ilike(like))

    if length_bucket:
        stmt = stmt.where(or_(*[_length_clause(b) for b in length_bucket]))
        count_stmt = count_stmt.where(or_(*[_length_clause(b) for b in length_bucket]))

    if metadata_key and metadata_value is not None:
        key = _validate_metadata_key(metadata_key)
        path = f"$.{key}"
        stmt = stmt.where(
            func.lower(cast(func.json_extract(Document.metadata_json, path), String))
            == metadata_value.lower()
        )
        count_stmt = count_stmt.where(
            func.lower(cast(func.json_extract(Document.metadata_json, path), String))
            == metadata_value.lower()
        )
    elif metadata_key and metadata_value is None:
        raise HTTPException(status_code=400, detail="metadata_value is required when metadata_key is set")

    total = int(db.scalar(count_stmt) or 0)
    stmt = stmt.order_by(Document.created_at.desc()).offset(offset).limit(limit)
    rows = list(db.scalars(stmt))

    return {
        "total": total,
        "items": [
            {
                "id": d.id,
                "text": d.text,
                "metadata": dict(d.metadata_json or {}),
                "char_length": d.char_length,
                "created_at": d.created_at.isoformat() + "Z",
            }
            for d in rows
        ],
    }


@router.get("/{document_id}")
def get_document(document_id: str, db: Annotated[Session, Depends(get_db)]):
    d = db.get(Document, document_id)
    if not d:
        raise HTTPException(status_code=404, detail="document not found")
    return {
        "id": d.id,
        "text": d.text,
        "metadata": dict(d.metadata_json or {}),
        "char_length": d.char_length,
        "created_at": d.created_at.isoformat() + "Z",
    }
