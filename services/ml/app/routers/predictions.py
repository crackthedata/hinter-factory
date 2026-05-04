from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.database import get_db
from app.ingest import IngestError, parse_csv_bytes, parse_json_bytes
from app.lf_executor import execute_labeling_function
from app.models import Document, LabelingFunction, Tag
from app.probabilistic_aggregator import aggregate_one, predicted_label_from_probability
from app.project_scope import resolve_project_id
from app.routers.documents import (
    _apply_bulk_pragmas,
    _should_parse_as_csv,
    _should_parse_as_json,
    _spool_upload_to_disk,
)

router = APIRouter(prefix="/v1/predictions", tags=["predictions"])

MAX_UPLOAD_PART_SIZE = 1024 * 1024 * 1024 * 64


@router.post("")
async def batch_predict(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Upload a CSV and get predictions for all tags in the project.

    Documents are saved to the project database. Returns predictions
    (label + probability) for each tag.

    Query parameters:
    - format: "json" (default) or "csv"
    """
    try:
        form = await request.form(max_part_size=MAX_UPLOAD_PART_SIZE)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"could not parse multipart upload: {exc}"
        ) from exc

    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="missing 'file' field in upload")

    text_column_raw = form.get("text_column")
    text_column = text_column_raw if isinstance(text_column_raw, str) and text_column_raw else "text"
    id_column_raw = form.get("id_column")
    id_column = id_column_raw if isinstance(id_column_raw, str) and id_column_raw else None
    project_id_raw = form.get("project_id")
    project_id_form = project_id_raw if isinstance(project_id_raw, str) else None
    project_id = project_id_form or request.query_params.get("project_id")

    project_id = resolve_project_id(db, project_id)

    name = upload.filename
    ct = upload.content_type
    is_json = _should_parse_as_json(name, ct)
    is_csv = _should_parse_as_csv(name, ct)
    if not is_json and not is_csv:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not detect CSV or JSON from filename or Content-Type. "
                "Use a .csv or .json extension, or export CSV as UTF-8 from Excel."
            ),
        )

    return await asyncio.to_thread(
        _ingest_and_predict_sync,
        db,
        project_id,
        is_json=is_json,
        upload=upload,
        text_column=text_column,
        id_column=id_column,
        format=format,
    )


def _ingest_and_predict_sync(
    db: Session,
    project_id: str,
    *,
    is_json: bool,
    upload: UploadFile,
    text_column: str,
    id_column: str | None,
    format: str,
) -> StreamingResponse | dict[str, Any]:
    """Ingest CSV/JSON and generate predictions for all tags."""
    db.commit()

    # Parse the file
    raw = upload.file.read()
    try:
        if is_json:
            items, errors = parse_csv_bytes(raw, text_column=text_column, id_column=id_column)
        else:
            items, errors = parse_csv_bytes(
                raw, text_column=text_column, id_column=id_column
            )
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not items:
        raise HTTPException(status_code=400, detail="no valid documents found")

    # Save documents to database
    sa_conn = db.connection()
    raw_conn = sa_conn.connection
    cur = raw_conn.cursor()
    _apply_bulk_pragmas(cur)

    try:
        # Write documents in batch
        now_iso = datetime.utcnow().isoformat(sep=" ", timespec="microseconds")
        inserts = []
        doc_id_map = {}  # Original ID -> inserted ID

        for it in items:
            doc_id = it["id"]
            body = it["text"]
            meta_json = json.dumps(it["metadata"], default=str)
            char_len = len(body)
            new_id = str(uuid.uuid4())

            inserts.append((new_id, project_id, body, meta_json, char_len, now_iso))
            doc_id_map[doc_id] = new_id

        if inserts:
            cur.executemany(
                "INSERT INTO documents (id, project_id, text, metadata, char_length, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                inserts,
            )

        raw_conn.commit()

        # Fetch tags and LFs
        tags = db.scalars(select(Tag).where(Tag.project_id == project_id)).all()
        lfs_by_tag = {}
        for tag in tags:
            lfs = db.scalars(
                select(LabelingFunction).where(
                    LabelingFunction.tag_id == tag.id,
                    LabelingFunction.enabled == True,
                )
            ).all()
            lfs_by_tag[tag.id] = (tag.name, lfs)

        # Fetch documents by their new IDs
        new_doc_ids = list(doc_id_map.values())
        docs = {
            d.id: d
            for d in db.scalars(
                select(Document).where(Document.id.in_(new_doc_ids))
            ).all()
        }

        # Generate predictions for each document and tag
        results = []
        for orig_id, new_id in doc_id_map.items():
            doc = docs.get(new_id)
            if not doc:
                continue

            doc_result = {
                "id": new_id,
                "original_id": orig_id,
                "text": doc.text,
                "metadata": doc.metadata_json,
                "predictions": [],
            }

            for tag_id, (tag_name, lfs) in lfs_by_tag.items():
                # Execute all LFs for this tag on this document
                pos_votes = 0
                neg_votes = 0
                for lf in lfs:
                    try:
                        vote = execute_labeling_function(lf.type, lf.config, doc.text)
                        if vote > 0:
                            pos_votes += 1
                        elif vote < 0:
                            neg_votes += 1
                    except Exception:
                        # Skip LFs that error during execution
                        pass

                # Aggregate votes
                prob, conflict, entropy = aggregate_one(pos_votes, neg_votes)
                predicted_label = predicted_label_from_probability(prob)

                doc_result["predictions"].append(
                    {
                        "tag_name": tag_name,
                        "predicted_label": predicted_label,
                        "probability": prob,
                        "positive_votes": pos_votes,
                        "negative_votes": neg_votes,
                        "conflict_score": conflict,
                        "entropy": entropy,
                    }
                )

            results.append(doc_result)

        # Return in requested format
        if format == "csv":
            return _format_as_csv(results)
        else:
            return {"documents": results, "ingest_errors": errors}

    finally:
        cur.close()


def _format_as_csv(results: list[dict[str, Any]]) -> StreamingResponse:
    """Format results as CSV with dynamic tag columns."""
    if not results:
        raise HTTPException(status_code=400, detail="no documents to export")

    # Collect all tag names
    all_tags = set()
    for doc in results:
        for pred in doc["predictions"]:
            all_tags.add(pred["tag_name"])

    all_tags = sorted(all_tags)

    # Build CSV header
    header = ["id", "original_id", "text", "metadata"]
    for tag in all_tags:
        header.append(f"{tag}_label")
        header.append(f"{tag}_probability")

    # Build CSV rows
    rows = []
    for doc in results:
        tag_preds = {p["tag_name"]: p for p in doc["predictions"]}

        row = [
            doc["id"],
            doc["original_id"],
            doc["text"],
            json.dumps(doc["metadata"]),
        ]

        for tag in all_tags:
            pred = tag_preds.get(tag)
            if pred:
                row.append(str(pred["predicted_label"]))
                row.append(f"{pred['probability']:.4f}")
            else:
                row.append("")
                row.append("")

        rows.append(row)

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=predictions.csv"},
    )
