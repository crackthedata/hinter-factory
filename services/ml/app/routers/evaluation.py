from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.evaluation import evaluate_run, find_latest_completed_run
from app.models import LfRun, Tag

router = APIRouter(prefix="/v1/evaluation", tags=["evaluation"])


@router.get("")
def get_evaluation(
    db: Annotated[Session, Depends(get_db)],
    tag_id: str = Query(..., description="Tag whose gold labels define the validation set"),
    run_id: str | None = Query(
        default=None,
        description="LF run to evaluate; defaults to the latest completed run for the tag",
    ),
    text_preview_chars: int = Query(default=240, ge=20, le=2000),
    limit: int = Query(
        default=200,
        ge=1,
        le=2000,
        description="Cap on returned per-document rows; summary counts are not capped",
    ),
):
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="tag not found")

    if run_id:
        run = db.get(LfRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        if run.tag_id != tag_id:
            raise HTTPException(
                status_code=400,
                detail=f"run {run_id} belongs to tag {run.tag_id}, not {tag_id}",
            )
        if run.status != "completed":
            raise HTTPException(status_code=409, detail="run is not completed")
    else:
        run = find_latest_completed_run(db, tag_id)
        if run is None:
            return {
                "tag_id": tag_id,
                "run_id": None,
                "summary": {
                    "total_gold": 0,
                    "considered": 0,
                    "true_positive": 0,
                    "true_negative": 0,
                    "false_positive": 0,
                    "false_negative": 0,
                    "abstain_on_positive": 0,
                    "abstain_on_negative": 0,
                    "gold_abstain": 0,
                    "precision": None,
                    "recall": None,
                    "f1": None,
                    "coverage": None,
                },
                "rows": [],
                "message": "No completed LF run for this tag yet. Run LFs in Studio first.",
            }

    summary, rows = evaluate_run(
        db, tag_id=tag_id, run=run, text_preview_chars=text_preview_chars
    )

    truncated = len(rows) > limit
    rows = rows[:limit]

    return {
        "tag_id": tag_id,
        "run_id": run.id,
        "run_completed_at": run.completed_at.isoformat() + "Z" if run.completed_at else None,
        "summary": asdict(summary),
        "rows": [asdict(r) for r in rows],
        "truncated": truncated,
    }
