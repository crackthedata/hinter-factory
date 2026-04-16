from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any


class IngestError(Exception):
    pass


def _decode_csv_text(data: bytes) -> str:
    """Decode CSV bytes; Windows/Excel often emits UTF-16-LE or legacy Windows encodings."""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        pass
    return data.decode("latin-1")


def _strip_header(name: str | None) -> str:
    if name is None:
        return ""
    return name.replace("\ufeff", "").strip()


def _sniff_delimiter(sample_line: str) -> str:
    """Pick a delimiter when Excel uses semicolons (common in EU locales)."""
    if not sample_line.strip():
        return ","
    commas = sample_line.count(",")
    semis = sample_line.count(";")
    tabs = sample_line.count("\t")
    if tabs and tabs >= commas and tabs >= semis:
        return "\t"
    if semis > commas:
        return ";"
    return ","


def _resolve_field(fieldnames: list[str] | None, requested: str) -> str | None:
    """Match requested column name case-insensitively and ignoring BOM/whitespace on headers."""
    if not fieldnames:
        return None
    want = _strip_header(requested).lower()
    if not want:
        return None
    for fn in fieldnames:
        if _strip_header(fn).lower() == want:
            return fn
    return None


def _normalize_metadata(row: dict[str, Any], skip: set[str]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for k, v in row.items():
        if k in skip:
            continue
        if v is None or v == "":
            continue
        meta[k] = v
    return meta


def parse_csv_bytes(
    data: bytes, *, text_column: str = "text", id_column: str | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    text = _decode_csv_text(data)
    first_line = text.splitlines()[0] if text else ""
    delimiter = _sniff_delimiter(first_line)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise IngestError("CSV has no header row")

    text_key = _resolve_field(list(reader.fieldnames), text_column)
    if not text_key:
        headers_list = [_strip_header(h) for h in reader.fieldnames if h is not None]
        headers_fmt = ", ".join(repr(h) for h in headers_list) or "(none)"
        raise IngestError(
            f"CSV missing required text column {text_column!r} (case-insensitive). "
            f"Use the exact header of the body column — one name only. Found columns: {headers_fmt}"
        )

    id_key: str | None = None
    if id_column:
        id_key = _resolve_field(list(reader.fieldnames), id_column)
        if not id_key:
            headers_list = [_strip_header(h) for h in reader.fieldnames if h is not None]
            headers_fmt = ", ".join(repr(h) for h in headers_list) or "(none)"
            raise IngestError(
                f"CSV missing id column {id_column!r} (case-insensitive). "
                f"Use a header name from the first row, not a row number. Found columns: {headers_fmt}"
            )

    skip = {text_key}
    if id_key:
        skip.add(id_key)

    for i, row in enumerate(reader, start=2):
        body = (row.get(text_key) or "").strip()
        if not body:
            errors.append(f"row {i}: empty '{text_key}'")
            continue
        doc_id = (row.get(id_key) or "").strip() if id_key else str(uuid.uuid4())
        if id_key and not doc_id:
            errors.append(f"row {i}: empty '{id_key}'")
            continue
        if not id_key:
            doc_id = str(uuid.uuid4())
        items.append(
            {
                "id": doc_id,
                "text": body,
                "metadata": _normalize_metadata(row, skip),
            }
        )
    return items, errors


def parse_json_bytes(data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    payload = json.loads(data.decode("utf-8-sig"))
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("documents"), list):
        rows = payload["documents"]
    else:
        raise IngestError("JSON must be an array of objects or {documents: [...] }")

    items: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            errors.append(f"item {i}: expected object")
            continue
        body = str(row.get("text", "")).strip()
        if not body:
            errors.append(f"item {i}: missing text")
            continue
        doc_id = str(row.get("id") or uuid.uuid4())
        meta = row.get("metadata")
        if meta is None:
            meta = {k: v for k, v in row.items() if k not in ("id", "text")}
        elif not isinstance(meta, dict):
            errors.append(f"item {i}: metadata must be an object when provided")
            continue
        items.append({"id": doc_id, "text": body, "metadata": dict(meta)})
    return items, errors
