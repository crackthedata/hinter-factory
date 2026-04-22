"""Evaluate an LF run against gold labels for a tag.

The "validation set" is implicitly defined as every document the user has
gold-labeled for the chosen tag, with gold value != 0. Gold value 0 means the
labeler explicitly abstained, so it is excluded from precision/recall but still
reported in the totals.

Aggregation: per (document, tag) we sum the LF votes from the run. Sum > 0
predicts +1 (positive for tag), sum < 0 predicts -1 (negative), sum == 0
abstains. This matches the matrix already exposed by /v1/lf-runs/{id}/matrix
without introducing a probabilistic label model.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Document,
    GoldLabel,
    LabelingFunction,
    LfRun,
    LfRunLabelingFunction,
    LfRunVote,
)

Category = Literal[
    "true_positive",
    "true_negative",
    "false_positive",
    "false_negative",
    "abstain_on_positive",
    "abstain_on_negative",
    "gold_abstain",
]


def aggregate_vote(votes: Iterable[int]) -> int:
    """Majority by sum. Returns 1, -1, or 0 (abstain)."""
    s = 0
    for v in votes:
        s += int(v)
    if s > 0:
        return 1
    if s < 0:
        return -1
    return 0


def categorize(gold: int, predicted: int) -> Category:
    if gold == 0:
        return "gold_abstain"
    if gold == 1:
        if predicted == 1:
            return "true_positive"
        if predicted == -1:
            return "false_negative"
        return "abstain_on_positive"
    # gold == -1
    if predicted == 1:
        return "false_positive"
    if predicted == -1:
        return "true_negative"
    return "abstain_on_negative"


@dataclass
class EvaluationVote:
    labeling_function_id: str
    labeling_function_name: str
    vote: int


@dataclass
class EvaluationRow:
    document_id: str
    text_preview: str
    text: str
    gold: int
    predicted: int
    vote_sum: int
    category: Category
    votes: list[EvaluationVote]


@dataclass
class EvaluationSummary:
    total_gold: int
    considered: int  # gold != 0
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int
    abstain_on_positive: int
    abstain_on_negative: int
    gold_abstain: int
    precision: float | None
    recall: float | None
    f1: float | None
    coverage: float | None  # fraction of considered docs where prediction != 0


def _safe_div(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return num / den


def find_latest_completed_run(db: Session, tag_id: str) -> LfRun | None:
    return db.scalar(
        select(LfRun)
        .where(LfRun.tag_id == tag_id, LfRun.status == "completed")
        .order_by(LfRun.completed_at.desc(), LfRun.created_at.desc())
        .limit(1)
    )


def evaluate_run(
    db: Session,
    *,
    tag_id: str,
    run: LfRun,
    text_preview_chars: int = 200,
) -> tuple[EvaluationSummary, list[EvaluationRow]]:
    gold_rows = db.scalars(select(GoldLabel).where(GoldLabel.tag_id == tag_id)).all()
    if not gold_rows:
        return _empty_summary(0), []

    doc_ids = [g.document_id for g in gold_rows]
    docs = {
        d.id: d
        for d in db.scalars(select(Document).where(Document.id.in_(doc_ids))).all()
    }

    lf_run_lfs = db.scalars(
        select(LfRunLabelingFunction)
        .where(LfRunLabelingFunction.run_id == run.id)
        .order_by(LfRunLabelingFunction.position.asc())
    ).all()
    lf_ids = [r.labeling_function_id for r in lf_run_lfs]
    lfs = {
        lf.id: lf
        for lf in db.scalars(
            select(LabelingFunction).where(LabelingFunction.id.in_(lf_ids))
        ).all()
    }

    votes_by_doc: dict[str, list[LfRunVote]] = defaultdict(list)
    if doc_ids:
        for v in db.scalars(
            select(LfRunVote).where(
                LfRunVote.run_id == run.id,
                LfRunVote.document_id.in_(doc_ids),
            )
        ).all():
            votes_by_doc[v.document_id].append(v)

    rows: list[EvaluationRow] = []
    counts: dict[Category, int] = defaultdict(int)

    for g in gold_rows:
        doc = docs.get(g.document_id)
        if doc is None:
            continue  # gold for a deleted doc; ignore
        doc_votes = votes_by_doc.get(g.document_id, [])
        vote_sum = sum(int(v.vote) for v in doc_votes)
        if vote_sum > 0:
            predicted = 1
        elif vote_sum < 0:
            predicted = -1
        else:
            predicted = 0
        category = categorize(int(g.value), predicted)
        counts[category] += 1
        evaluation_votes = [
            EvaluationVote(
                labeling_function_id=v.labeling_function_id,
                labeling_function_name=(
                    lfs[v.labeling_function_id].name
                    if v.labeling_function_id in lfs
                    else "(deleted LF)"
                ),
                vote=int(v.vote),
            )
            for v in doc_votes
        ]
        evaluation_votes.sort(key=lambda x: x.labeling_function_name.lower())
        full_text = doc.text or ""
        rows.append(
            EvaluationRow(
                document_id=g.document_id,
                text_preview=full_text[:text_preview_chars],
                text=full_text,
                gold=int(g.value),
                predicted=predicted,
                vote_sum=vote_sum,
                category=category,
                votes=evaluation_votes,
            )
        )

    tp = counts["true_positive"]
    tn = counts["true_negative"]
    fp = counts["false_positive"]
    fn = counts["false_negative"]
    abstain_pos = counts["abstain_on_positive"]
    abstain_neg = counts["abstain_on_negative"]
    gold_abstain = counts["gold_abstain"]
    considered = tp + tn + fp + fn + abstain_pos + abstain_neg
    fn_total = fn + abstain_pos  # missed positives, including abstains
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn_total)
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    coverage = _safe_div(tp + tn + fp + fn, considered)

    summary = EvaluationSummary(
        total_gold=len(gold_rows),
        considered=considered,
        true_positive=tp,
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        abstain_on_positive=abstain_pos,
        abstain_on_negative=abstain_neg,
        gold_abstain=gold_abstain,
        precision=precision,
        recall=recall,
        f1=f1,
        coverage=coverage,
    )

    # Order: errors first (FP, FN, abstain_on_positive), then the rest.
    priority = {
        "false_positive": 0,
        "false_negative": 1,
        "abstain_on_positive": 2,
        "abstain_on_negative": 3,
        "true_positive": 4,
        "true_negative": 5,
        "gold_abstain": 6,
    }
    rows.sort(key=lambda r: (priority[r.category], -abs(r.vote_sum), r.document_id))
    return summary, rows


def _empty_summary(total_gold: int) -> EvaluationSummary:
    return EvaluationSummary(
        total_gold=total_gold,
        considered=0,
        true_positive=0,
        true_negative=0,
        false_positive=0,
        false_negative=0,
        abstain_on_positive=0,
        abstain_on_negative=0,
        gold_abstain=0,
        precision=None,
        recall=None,
        f1=None,
        coverage=None,
    )
