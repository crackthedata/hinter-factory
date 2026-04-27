from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.evaluation import find_latest_completed_run
from app.models import Document, LfRunVote, ProbabilisticLabel, Tag
from app.probabilistic_aggregator import predicted_label_from_probability
from app.project_scope import resolve_project_id

router = APIRouter(prefix="/v1/probabilistic-labels", tags=["probabilisticLabels"])

SortField = Literal["probability_desc", "probability_asc", "entropy_desc", "updated_at"]
PredictedFilter = Literal["positive", "negative", "abstain"]
TEXT_PREVIEW_CHARS = 200


@router.get("")
def list_probabilistic_labels(
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
    tag_id: str | None = None,
    predicted: PredictedFilter | None = None,
    q: str | None = None,
    sort: SortField = "probability_desc",
    limit: int = 100,
    offset: int = 0,
):
    project_id = resolve_project_id(db, project_id)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    stmt = select(ProbabilisticLabel).where(ProbabilisticLabel.project_id == project_id)
    if tag_id:
        stmt = stmt.where(ProbabilisticLabel.tag_id == tag_id)
    if predicted == "positive":
        stmt = stmt.where(ProbabilisticLabel.probability > 0.5)
    elif predicted == "negative":
        stmt = stmt.where(ProbabilisticLabel.probability < 0.5)
    elif predicted == "abstain":
        stmt = stmt.where(ProbabilisticLabel.probability == 0.5)

    if q:
        # Join on Document for text search. We keep the join optional so the
        # default "no text filter" path stays a single-table scan.
        stmt = stmt.join(Document, Document.id == ProbabilisticLabel.document_id).where(
            Document.text.ilike(f"%{q}%")
        )

    total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(db.scalar(total_stmt) or 0)

    if sort == "probability_asc":
        stmt = stmt.order_by(ProbabilisticLabel.probability.asc(), ProbabilisticLabel.document_id)
    elif sort == "entropy_desc":
        stmt = stmt.order_by(
            ProbabilisticLabel.entropy.desc().nulls_last(),
            ProbabilisticLabel.document_id,
        )
    elif sort == "updated_at":
        stmt = stmt.order_by(ProbabilisticLabel.updated_at.desc())
    else:  # probability_desc
        stmt = stmt.order_by(ProbabilisticLabel.probability.desc(), ProbabilisticLabel.document_id)

    rows = list(db.scalars(stmt.offset(offset).limit(limit)).all())
    if not rows:
        return {"items": [], "total": total, "run_id": _latest_run_id(db, tag_id)}

    doc_ids = [r.document_id for r in rows]
    docs = {
        d.id: d
        for d in db.scalars(select(Document).where(Document.id.in_(doc_ids))).all()
    }

    # Vote counts per (doc, tag) come from the latest completed run for the tag.
    # When listing across multiple tags we batch by tag.
    pos_neg_by_doc: dict[tuple[str, str], tuple[int, int]] = {}
    tag_ids_in_page = {r.tag_id for r in rows}
    for tid in tag_ids_in_page:
        run = find_latest_completed_run(db, tid)
        if run is None:
            continue
        page_doc_ids = [r.document_id for r in rows if r.tag_id == tid]
        if not page_doc_ids:
            continue
        for vote_row in db.execute(
            select(
                LfRunVote.document_id,
                func.sum(case((LfRunVote.vote > 0, 1), else_=0)).label("pos"),
                func.sum(case((LfRunVote.vote < 0, 1), else_=0)).label("neg"),
            )
            .where(
                LfRunVote.run_id == run.id,
                LfRunVote.document_id.in_(page_doc_ids),
            )
            .group_by(LfRunVote.document_id)
        ).all():
            pos_neg_by_doc[(vote_row.document_id, tid)] = (
                int(vote_row.pos or 0),
                int(vote_row.neg or 0),
            )

    items = []
    for r in rows:
        doc = docs.get(r.document_id)
        text = doc.text if doc else ""
        pos, neg = pos_neg_by_doc.get((r.document_id, r.tag_id), (0, 0))
        items.append(
            {
                "document_id": r.document_id,
                "tag_id": r.tag_id,
                "probability": r.probability,
                "conflict_score": r.conflict_score,
                "entropy": r.entropy,
                "updated_at": r.updated_at.isoformat() + "Z",
                "predicted": predicted_label_from_probability(r.probability),
                "positive_votes": pos,
                "negative_votes": neg,
                "text_preview": (text or "")[:TEXT_PREVIEW_CHARS],
                "char_length": doc.char_length if doc else 0,
            }
        )

    return {
        "items": items,
        "total": total,
        "run_id": _latest_run_id(db, tag_id),
    }


@router.get("/distribution")
def probability_distribution(
    db: Annotated[Session, Depends(get_db)],
    tag_id: str,
    project_id: str | None = None,
    bins: int = Query(default=10, ge=2, le=50),
):
    """Per-tag histogram of probabilities for the entire corpus, plus the
    counts of predicted +1 / 0 / -1. Powers the summary header on the
    Predictions page."""
    project_id = resolve_project_id(db, project_id)
    rows = db.scalars(
        select(ProbabilisticLabel.probability).where(
            ProbabilisticLabel.project_id == project_id,
            ProbabilisticLabel.tag_id == tag_id,
        )
    ).all()
    total = len(rows)
    if total == 0:
        return {
            "tag_id": tag_id,
            "total": 0,
            "predicted_positive": 0,
            "predicted_negative": 0,
            "predicted_abstain": 0,
            "mean_probability": None,
            "bins": [],
        }

    pos = sum(1 for p in rows if p > 0.5)
    neg = sum(1 for p in rows if p < 0.5)
    ab = total - pos - neg
    mean = sum(rows) / total

    edges = [i / bins for i in range(bins + 1)]
    counts = [0] * bins
    for p in rows:
        # Last bin is closed on the right so probability == 1.0 lands in it.
        idx = min(int(p * bins), bins - 1)
        counts[idx] += 1

    return {
        "tag_id": tag_id,
        "total": total,
        "predicted_positive": pos,
        "predicted_negative": neg,
        "predicted_abstain": ab,
        "mean_probability": mean,
        "bins": [
            {"lower": edges[i], "upper": edges[i + 1], "count": counts[i]}
            for i in range(bins)
        ],
    }


@router.post("")
def upsert_probabilistic_label(payload: dict, db: Annotated[Session, Depends(get_db)]):
    document_id = str(payload.get("document_id", "")).strip()
    tag_id = str(payload.get("tag_id", "")).strip()
    probability = payload.get("probability")
    if not document_id or not tag_id or probability is None:
        raise HTTPException(status_code=400, detail="document_id, tag_id, and probability are required")
    try:
        p = float(probability)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="probability must be a number") from exc
    if p < 0 or p > 1:
        raise HTTPException(status_code=400, detail="probability must be between 0 and 1")

    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="tag not found")

    row = db.scalar(
        select(ProbabilisticLabel).where(
            ProbabilisticLabel.document_id == document_id,
            ProbabilisticLabel.tag_id == tag_id,
        )
    )
    conflict = payload.get("conflict_score")
    entropy = payload.get("entropy")
    if row:
        row.probability = p
        row.conflict_score = float(conflict) if conflict is not None else None
        row.entropy = float(entropy) if entropy is not None else None
        row.updated_at = datetime.utcnow()
    else:
        row = ProbabilisticLabel(
            project_id=tag.project_id,
            document_id=document_id,
            tag_id=tag_id,
            probability=p,
            conflict_score=float(conflict) if conflict is not None else None,
            entropy=float(entropy) if entropy is not None else None,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "document_id": row.document_id,
        "tag_id": row.tag_id,
        "probability": row.probability,
        "conflict_score": row.conflict_score,
        "entropy": row.entropy,
        "updated_at": row.updated_at.isoformat() + "Z",
    }


def _latest_run_id(db: Session, tag_id: str | None) -> str | None:
    if not tag_id:
        return None
    run = find_latest_completed_run(db, tag_id)
    return run.id if run else None
