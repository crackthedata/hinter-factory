# See docs/notes-ml.md#servicesmlappevaluationpy for the full rationale.

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal

from sqlalchemy import func, select
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
    if predicted == 1:
        return "false_positive"
    if predicted == -1:
        return "true_negative"
    return "abstain_on_negative"


@dataclass
class LfStats:
    labeling_function_id: str
    labeling_function_name: str
    lf_type: str
    return_value: int
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    abstain_on_positive: int
    abstain_on_negative: int
    precision: float | None  # TP / (TP + FP) for +1 LFs; TN / (TN + FN) for -1 LFs


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
    considered: int
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
    coverage: float | None
    corpus_total_docs: int
    corpus_covered_docs: int
    corpus_coverage: float | None


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
) -> tuple[EvaluationSummary, list[EvaluationRow], list[LfStats]]:
    gold_rows = db.scalars(select(GoldLabel).where(GoldLabel.tag_id == tag_id)).all()
    if not gold_rows:
        return _empty_summary(0), [], []

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
            continue
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
    fn_total = fn + abstain_pos
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn_total)
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    coverage = _safe_div(tp + tn + fp + fn, considered)

    corpus_total = db.scalar(
        select(func.count(Document.id)).where(Document.project_id == run.project_id)
    ) or 0
    corpus_covered = db.scalar(
        select(func.count(func.distinct(LfRunVote.document_id))).where(
            LfRunVote.run_id == run.id,
            LfRunVote.vote != 0,
        )
    ) or 0
    corpus_coverage = _safe_div(corpus_covered, corpus_total)

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
        corpus_total_docs=corpus_total,
        corpus_covered_docs=corpus_covered,
        corpus_coverage=corpus_coverage,
    )

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

    # Per-LF stats: invert votes_by_doc into votes_by_lf then score against gold.
    gold_by_doc = {g.document_id: int(g.value) for g in gold_rows}
    votes_by_lf: dict[str, dict[str, int]] = defaultdict(dict)
    for doc_id, doc_votes in votes_by_doc.items():
        for v in doc_votes:
            votes_by_lf[v.labeling_function_id][doc_id] = int(v.vote)

    lf_stats: list[LfStats] = []
    for lf_id in lf_ids:
        lf = lfs.get(lf_id)
        lf_name = lf.name if lf else "(deleted LF)"
        lf_type = lf.type if lf else "unknown"
        rv = int((lf.config or {}).get("return_value", 1)) if lf else 1

        lf_votes = votes_by_lf.get(lf_id, {})
        tp = fp = tn = fn = abs_pos = abs_neg = 0
        for doc_id, gold in gold_by_doc.items():
            if gold == 0:
                continue
            vote = lf_votes.get(doc_id, 0)
            if gold == 1:
                if vote > 0:
                    tp += 1
                elif vote < 0:
                    fn += 1
                else:
                    abs_pos += 1
            else:  # gold == -1
                if vote < 0:
                    tn += 1
                elif vote > 0:
                    fp += 1
                else:
                    abs_neg += 1

        if rv == 1:
            lf_precision = _safe_div(tp, tp + fp)
        else:
            lf_precision = _safe_div(tn, tn + fn)

        lf_stats.append(
            LfStats(
                labeling_function_id=lf_id,
                labeling_function_name=lf_name,
                lf_type=lf_type,
                return_value=rv,
                true_positive=tp,
                false_positive=fp,
                true_negative=tn,
                false_negative=fn,
                abstain_on_positive=abs_pos,
                abstain_on_negative=abs_neg,
                precision=lf_precision,
            )
        )

    return summary, rows, lf_stats


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
        corpus_total_docs=0,
        corpus_covered_docs=0,
        corpus_coverage=None,
    )
