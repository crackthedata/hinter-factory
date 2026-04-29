from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import uuid
from collections.abc import Iterator
from typing import Any

import polars as pl


class IngestError(Exception):
    pass


PER_BATCH_ERROR_CAP = 100


def _decode_csv_text(data: bytes) -> str:
    # See docs/notes-ml.md#servicesmlappingestpy for encoding-detection rationale.
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


def _peek_csv_meta(path: str, sample_bytes: int = 64 * 1024) -> tuple[str, str, str | None]:
    # See docs/notes-ml.md#servicesmlappingestpy for the UTF-16 transcoding strategy.
    with open(path, "rb") as fh:
        head = fh.read(sample_bytes)

    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        transcoded = tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".utf8.csv"
        )
        try:
            with open(path, "rb") as src:
                import codecs

                decoder = codecs.getincrementaldecoder("utf-16")()
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    text = decoder.decode(chunk)
                    if text:
                        transcoded.write(text.encode("utf-8"))
                tail = decoder.decode(b"", final=True)
                if tail:
                    transcoded.write(tail.encode("utf-8"))
        finally:
            transcoded.close()
        with open(transcoded.name, "rb") as fh:
            new_head = fh.read(sample_bytes)
        sample_text = new_head.decode("utf-8", errors="replace")
        first_line = sample_text.splitlines()[0] if sample_text else ""
        return "utf8", _sniff_delimiter(first_line), transcoded.name

    sample_text = _decode_csv_text(head)
    first_line = sample_text.splitlines()[0] if sample_text else ""
    delimiter = _sniff_delimiter(first_line)

    try:
        head.decode("utf-8-sig")
        encoding = "utf8"
    except UnicodeDecodeError:
        encoding = "utf8-lossy"
    return encoding, delimiter, None


def _resolve_field_ci(columns: list[str], requested: str) -> str | None:
    want = _strip_header(requested).lower()
    if not want:
        return None
    for c in columns:
        if _strip_header(c).lower() == want:
            return c
    return None


def iter_csv_batches(
    path: str,
    *,
    text_column: str = "text",
    id_column: str | None = None,
    batch_size: int = 10_000,
) -> Iterator[tuple[list[dict[str, Any]], list[str], int]]:
    # See docs/notes-ml.md#servicesmlappingestpy for streaming + per-batch error-cap rationale.
    encoding, delimiter, transcoded_path = _peek_csv_meta(path)
    read_path = transcoded_path or path

    try:
        lf = pl.scan_csv(
            read_path,
            separator=delimiter,
            encoding=encoding,
            infer_schema=False,
            has_header=True,
            rechunk=False,
            low_memory=True,
        )

        try:
            columns = list(lf.collect_schema().keys())
        except Exception as exc:  # noqa: BLE001 - surface any Polars failure as a clean IngestError
            raise IngestError(f"Could not read CSV header: {exc}") from exc
        if not columns:
            raise IngestError("CSV has no header row")

        text_key = _resolve_field_ci(columns, text_column)
        if not text_key:
            headers_fmt = ", ".join(repr(_strip_header(c)) for c in columns) or "(none)"
            raise IngestError(
                f"CSV missing required text column {text_column!r} (case-insensitive). "
                f"Use the exact header of the body column \u2014 one name only. "
                f"Found columns: {headers_fmt}"
            )
        id_key: str | None = None
        if id_column:
            id_key = _resolve_field_ci(columns, id_column)
            if not id_key:
                headers_fmt = (
                    ", ".join(repr(_strip_header(c)) for c in columns) or "(none)"
                )
                raise IngestError(
                    f"CSV missing id column {id_column!r} (case-insensitive). "
                    f"Use a header name from the first row, not a row number. "
                    f"Found columns: {headers_fmt}"
                )
        skip = {text_key}
        if id_key:
            skip.add(id_key)

        def process(
            batch: pl.DataFrame, start_row: int
        ) -> tuple[list[dict[str, Any]], list[str], int]:
            items: list[dict[str, Any]] = []
            errors: list[str] = []
            dropped = 0
            for offset, row in enumerate(batch.iter_rows(named=True)):
                row_no = start_row + offset
                raw_body = row.get(text_key)
                body = (raw_body or "").strip() if isinstance(raw_body, str) else ""
                if not body:
                    if len(errors) < PER_BATCH_ERROR_CAP:
                        errors.append(f"row {row_no}: empty {text_key!r}")
                    else:
                        dropped += 1
                    continue
                if id_key:
                    raw_id = row.get(id_key)
                    doc_id = (raw_id or "").strip() if isinstance(raw_id, str) else ""
                    if not doc_id:
                        if len(errors) < PER_BATCH_ERROR_CAP:
                            errors.append(f"row {row_no}: empty {id_key!r}")
                        else:
                            dropped += 1
                        continue
                else:
                    doc_id = str(uuid.uuid4())
                meta: dict[str, Any] = {}
                for k, v in row.items():
                    if k in skip:
                        continue
                    if v is None or v == "":
                        continue
                    meta[k] = v
                items.append({"id": doc_id, "text": body, "metadata": meta})
            return items, errors, dropped

        next_row_no = 2
        for batch in lf.collect_batches(chunk_size=batch_size, maintain_order=True):
            items, errors, dropped = process(batch, next_row_no)
            next_row_no += batch.height
            yield items, errors, dropped
    finally:
        if transcoded_path:
            try:
                os.unlink(transcoded_path)
            except OSError:
                pass


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
