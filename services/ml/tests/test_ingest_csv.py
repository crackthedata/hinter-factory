from __future__ import annotations

from app.ingest import parse_csv_bytes


def test_csv_case_insensitive_header() -> None:
    raw = "Text,sector\nhello world,alpha\n".encode("utf-8")
    items, errors = parse_csv_bytes(raw, text_column="text")
    assert not errors
    assert len(items) == 1
    assert items[0]["text"] == "hello world"
    assert items[0]["metadata"] == {"sector": "alpha"}


def test_csv_semicolon_delimiter() -> None:
    raw = "text;sector\nhello;beta\n".encode("utf-8")
    items, errors = parse_csv_bytes(raw, text_column="text")
    assert not errors
    assert len(items) == 1
    assert items[0]["metadata"] == {"sector": "beta"}
