import csv
from pathlib import Path

import polars as pl
import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Project, Tag, LabelingFunction
from headless import process_csv


@pytest.fixture
def db() -> Session:
    with SessionLocal() as session:
        yield session


def test_process_csv_headless(db: Session, tmp_path: Path):
    # Setup database with a project, tag, and an LF
    project = Project(name="Headless Test Project")
    db.add(project)
    db.commit()

    tag1 = Tag(project_id=project.id, name="is_invoice")
    tag2 = Tag(project_id=project.id, name="is_receipt")
    db.add_all([tag1, tag2])
    db.commit()

    # Regex LF for is_invoice
    lf1 = LabelingFunction(
        project_id=project.id,
        tag_id=tag1.id,
        name="Invoice Regex",
        type="regex",
        config={"pattern": "invoice", "flags": "i"},
        enabled=True,
    )
    # Keyword LF for is_receipt
    lf2 = LabelingFunction(
        project_id=project.id,
        tag_id=tag2.id,
        name="Receipt Keyword",
        type="keywords",
        config={"keywords": ["receipt"], "mode": "any"},
        enabled=True,
    )
    db.add_all([lf1, lf2])
    db.commit()

    # Create a temporary input CSV
    input_csv = tmp_path / "input.csv"
    output_csv = tmp_path / "output.csv"

    data = [
        {"id": "1", "text": "This is an Invoice for your purchase."},
        {"id": "2", "text": "Here is your receipt."},
        {"id": "3", "text": "Just some random text with nothing relevant."},
    ]
    
    # Write input CSV
    with open(input_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "text"])
        writer.writeheader()
        writer.writerows(data)

    # Run the headless processor
    process_csv(
        project_name="Headless Test Project",
        input_csv=str(input_csv),
        output_csv=str(output_csv),
        text_column="text",
    )

    # Read output and verify
    assert output_csv.exists()
    df = pl.read_csv(str(output_csv))
    
    assert "is_invoice_probability" in df.columns
    assert "is_receipt_probability" in df.columns

    probs_invoice = df["is_invoice_probability"].to_list()
    probs_receipt = df["is_receipt_probability"].to_list()

    # Document 1: "This is an Invoice..."
    # should trigger lf1 (is_invoice) -> pos_votes=1, neg_votes=0
    # probability = (1 + 1) / (2 + 1 + 0) = 2/3 = 0.666...
    assert abs(probs_invoice[0] - (2 / 3)) < 1e-5
    # should NOT trigger lf2 (is_receipt) -> pos_votes=0, neg_votes=0
    # probability = (1 + 0) / (2 + 0 + 0) = 1/2 = 0.5
    assert abs(probs_receipt[0] - 0.5) < 1e-5

    # Document 2: "Here is your receipt."
    assert abs(probs_invoice[1] - 0.5) < 1e-5
    assert abs(probs_receipt[1] - (2 / 3)) < 1e-5

    # Document 3: "Just some random text..."
    assert abs(probs_invoice[2] - 0.5) < 1e-5
    assert abs(probs_receipt[2] - 0.5) < 1e-5


def test_process_csv_missing_project(tmp_path: Path):
    input_csv = tmp_path / "input.csv"
    output_csv = tmp_path / "output.csv"
    
    with open(input_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["text"])
        writer.writeheader()
        writer.writerow({"text": "test"})

    # Should exit because project doesn't exist
    with pytest.raises(SystemExit) as exc:
        process_csv(
            project_name="Nonexistent Project",
            input_csv=str(input_csv),
            output_csv=str(output_csv),
            text_column="text",
        )
    assert exc.value.code == 1
