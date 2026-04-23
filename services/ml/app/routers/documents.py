from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session
# Important: use Starlette's UploadFile (not fastapi.UploadFile) for the
# isinstance check. fastapi.UploadFile is a subclass; instances returned by
# request.form() are the Starlette base class, so checking against the FastAPI
# subclass would always fail.
from starlette.datastructures import UploadFile

from app.database import get_db
from app.ingest import IngestError, iter_csv_batches, parse_csv_bytes, parse_json_bytes
from app.models import Document
from app.project_scope import resolve_project_id

# Cap how many per-row warnings we return to the client. A malformed multi-GB
# CSV can otherwise produce a multi-megabyte JSON response that no UI can show.
MAX_RETURNED_ERRORS = 100

# How many CSV rows we batch into a single executemany call. Big enough to
# amortize SQLite overhead, small enough that one batch fits comfortably in RAM
# even for very wide rows.
INGEST_BATCH_SIZE = 10_000

# When we look up which incoming IDs already exist, we chunk the IN(...) query
# below SQLite's default 999-parameter limit.
ID_LOOKUP_CHUNK = 500

# Starlette's MultiPartParser defaults max_part_size to 1 MiB and rejects any
# file part larger than that with MultiPartException, which uvicorn surfaces as
# a dropped connection (ECONNRESET on the proxy side). For our streaming
# upload we want effectively no limit; file parts spool to disk via
# SpooledTemporaryFile regardless of this number, so memory stays bounded.
MAX_UPLOAD_PART_SIZE = 1024 * 1024 * 1024 * 64  # 64 GiB ceiling

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


def _truncate_errors(errors: list[str], total_so_far: int) -> tuple[list[str], int]:
    """Keep only the first MAX_RETURNED_ERRORS messages; report the dropped count."""
    if total_so_far + len(errors) <= MAX_RETURNED_ERRORS:
        return errors, 0
    keep = max(0, MAX_RETURNED_ERRORS - total_so_far)
    dropped = len(errors) - keep
    return errors[:keep], dropped


def _apply_bulk_pragmas(cur) -> None:
    """Tune SQLite for a long bulk write. WAL is persistent on the DB file;
    the others are per-connection and may leak back to the pool, which is
    acceptable for this dev tool (slightly faster, slightly less durable)."""
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute("PRAGMA cache_size=-200000")  # negative = KiB; ~200 MiB page cache


def _existing_ids_by_project(
    cur, ids: list[str]
) -> dict[str, str]:
    """Return {document_id: project_id} for the supplied ids, chunked to stay
    under SQLite's parameter limit."""
    found: dict[str, str] = {}
    for i in range(0, len(ids), ID_LOOKUP_CHUNK):
        chunk = ids[i : i + ID_LOOKUP_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        cur.execute(
            f"SELECT id, project_id FROM documents WHERE id IN ({placeholders})",
            chunk,
        )
        for row_id, pid in cur.fetchall():
            found[row_id] = pid
    return found


def _write_batch(
    cur, project_id: str, items: list[dict[str, Any]]
) -> tuple[int, int]:
    """Upsert one batch. Returns (inserted_count, updated_count).

    Preserves the existing semantics:
      - same id, same project   -> UPDATE in place
      - same id, other project  -> mint a fresh UUID and INSERT
      - new id                  -> INSERT with the supplied id
    """
    if not items:
        return 0, 0

    existing = _existing_ids_by_project(cur, [it["id"] for it in items])

    now_iso = datetime.utcnow().isoformat(sep=" ", timespec="microseconds")
    inserts: list[tuple[str, str, str, str, int, str]] = []
    updates: list[tuple[str, str, int, str, str]] = []

    for it in items:
        body = it["text"]
        meta_json = json.dumps(it["metadata"], default=str)
        char_len = len(body)
        existing_pid = existing.get(it["id"])
        if existing_pid is None:
            inserts.append((it["id"], project_id, body, meta_json, char_len, now_iso))
        elif existing_pid == project_id:
            updates.append((body, meta_json, char_len, it["id"], project_id))
        else:
            # cross-project id collision: re-mint rather than clobber the other
            # project's row.
            inserts.append((str(uuid.uuid4()), project_id, body, meta_json, char_len, now_iso))

    if inserts:
        cur.executemany(
            "INSERT INTO documents (id, project_id, text, metadata, char_length, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            inserts,
        )
    if updates:
        cur.executemany(
            "UPDATE documents SET text = ?, metadata = ?, char_length = ? "
            "WHERE id = ? AND project_id = ?",
            updates,
        )
    return len(inserts), len(updates)


def _spool_upload_to_disk(file: UploadFile, suffix: str) -> str:
    """Copy the upload to a real on-disk temp file in 1 MiB chunks. Never
    materializes the full body in Python memory; relies on Starlette's
    UploadFile already being a SpooledTemporaryFile that spills to disk."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        # rewind in case anything has consumed the stream
        try:
            file.file.seek(0)
        except Exception:  # noqa: BLE001 - some file-likes don't support seek
            pass
        shutil.copyfileobj(file.file, tmp, length=1024 * 1024)
    finally:
        tmp.close()
    return tmp.name


def _ingest_sync(
    db: Session,
    project_id: str,
    *,
    is_json: bool,
    upload: UploadFile,
    text_column: str,
    id_column: str | None,
) -> dict[str, Any]:
    """Run the streaming ingest synchronously. This is the CPU/IO-heavy path
    that the async route offloads to a worker thread."""
    # Release any read-lock the SA session may be holding before we start a
    # long-running write transaction on the underlying connection. WAL mode
    # (set below) means readers won't block this writer either way, but this
    # keeps the session's view consistent.
    db.commit()

    sa_conn = db.connection()
    raw_conn = sa_conn.connection  # DBAPI connection (sqlite3.Connection wrapper)
    cur = raw_conn.cursor()
    _apply_bulk_pragmas(cur)

    inserted_total = 0
    updated_total = 0
    returned_errors: list[str] = []
    dropped_errors = 0
    tmp_path: str | None = None

    try:
        if is_json:
            # JSON path stays buffered: the JSON parser needs the whole document
            # anyway, so streaming wouldn't help. For very large JSON, callers
            # should convert to CSV.
            raw = upload.file.read()
            try:
                items, errors = parse_json_bytes(raw)
            except IngestError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=400, detail=f"could not parse file: {exc}"
                ) from exc

            kept, dropped = _truncate_errors(errors, len(returned_errors))
            returned_errors.extend(kept)
            dropped_errors += dropped

            for i in range(0, len(items), INGEST_BATCH_SIZE):
                chunk = items[i : i + INGEST_BATCH_SIZE]
                ins, upd = _write_batch(cur, project_id, chunk)
                inserted_total += ins
                updated_total += upd
                sa_conn.commit()
        else:
            tmp_path = _spool_upload_to_disk(upload, suffix=".csv")
            try:
                for items, errors, dropped_in_batch in iter_csv_batches(
                    tmp_path,
                    text_column=text_column,
                    id_column=id_column or None,
                    batch_size=INGEST_BATCH_SIZE,
                ):
                    if errors:
                        kept, dropped = _truncate_errors(errors, len(returned_errors))
                        returned_errors.extend(kept)
                        dropped_errors += dropped
                    dropped_errors += dropped_in_batch
                    ins, upd = _write_batch(cur, project_id, items)
                    inserted_total += ins
                    updated_total += upd
                    sa_conn.commit()
            except IngestError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return {
        "inserted": inserted_total,
        # Field name kept as `skipped` for backward compatibility with the
        # existing UI; semantically it's "rows that updated an existing doc".
        "skipped": updated_total,
        "errors": returned_errors,
        "truncated_errors_count": dropped_errors,
        "project_id": project_id,
    }


@router.post("/upload")
async def upload_documents(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    # We bypass FastAPI's `File(...)` / `Form(...)` parameters because they
    # call `request.form()` with the default `max_part_size=1 MiB`. Any file
    # upload larger than 1 MiB would otherwise raise MultiPartException and
    # uvicorn would drop the socket (the client sees ECONNRESET). Calling
    # `request.form()` ourselves lets us raise the per-part ceiling.
    try:
        form = await request.form(max_part_size=MAX_UPLOAD_PART_SIZE)
    except Exception as exc:  # noqa: BLE001 - report any parse failure cleanly
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
    # Accept project_id from either the form body or the query string; the web
    # client now passes it both ways.
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

    # The ingest is sync (Polars + SQLite executemany). Run it in a worker
    # thread so the event loop stays responsive for other requests during the
    # multi-minute write of a multi-GB file.
    return await asyncio.to_thread(
        _ingest_sync,
        db,
        project_id,
        is_json=is_json,
        upload=upload,
        text_column=text_column,
        id_column=id_column,
    )


@router.get("/facets/metadata-keys")
def metadata_keys(
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
):
    project_id = resolve_project_id(db, project_id)
    docs = db.scalars(select(Document).where(Document.project_id == project_id).limit(5000)).all()
    keys: set[str] = set()
    for d in docs:
        if isinstance(d.metadata_json, dict):
            keys.update(d.metadata_json.keys())
    return sorted(keys)


@router.get("/facets/metadata-values")
def metadata_values(
    db: Annotated[Session, Depends(get_db)],
    key: str,
    project_id: str | None = None,
    limit: int = 100,
):
    project_id = resolve_project_id(db, project_id)
    key = _validate_metadata_key(key)
    limit = max(1, min(limit, 500))
    stmt = text(
        """
        SELECT DISTINCT CAST(je.value AS TEXT) AS v
        FROM documents d, json_each(d.metadata) AS je
        WHERE d.project_id = :pid
          AND je.key = :k
          AND json_type(je.value) IN ('text','integer','real','true','false')
        ORDER BY v
        LIMIT :lim
        """
    )
    rows = db.execute(stmt, {"pid": project_id, "k": key, "lim": limit}).all()
    return [r[0] for r in rows if r[0] is not None]


@router.get("")
def list_documents(
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
    q: str | None = None,
    length_bucket: list[str] | None = Query(None),
    metadata_key: str | None = None,
    metadata_value: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    project_id = resolve_project_id(db, project_id)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    stmt = select(Document).where(Document.project_id == project_id)
    count_stmt = select(func.count()).select_from(Document).where(Document.project_id == project_id)

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
