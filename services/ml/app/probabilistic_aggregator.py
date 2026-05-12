"""Aggregate raw LF votes into per-document `ProbabilisticLabel` rows.

The aggregator runs at the end of each LF run (see `routers/lf_runs.py`) and
overwrites the ``probabilistic_labels`` rows for the run's tag. Probabilities
are computed with a Laplace-smoothed majority-vote model:

    P(tag = +1 | votes) = (1 + #positive_votes) / (2 + #positive_votes + #negative_votes)

This is the lightweight Snorkel baseline (a.k.a. MV+α). It has three useful
properties for a workbench:

1. With zero votes, probability collapses to 0.5 -- "no information" rather
   than a spurious 0 or 1.
2. With one positive and zero negatives it gives 2/3 ≈ 0.67, not 1.0 -- a
   single LF firing on a doc shouldn't be treated as certain.
3. It's a pure function of the vote tallies, so it's easy to test and to
   replace later with a real label model without touching the pipeline.

We also emit two diagnostics:

- ``conflict_score`` -- ``min(pos, neg) / max(pos, neg)`` when both directions
  voted, else 0.0. 1.0 means the LFs split evenly; 0.0 means unanimous.
- ``entropy`` -- binary entropy of ``probability`` in bits (0 to 1). High
  entropy = we should not trust this prediction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, LfRunVote, ProbabilisticLabel


@dataclass
class AggregatedLabel:
    document_id: str
    tag_id: str
    probability: float
    conflict_score: float
    entropy: float
    positive_votes: int
    negative_votes: int


def aggregate_one(positive: int, negative: int) -> tuple[float, float, float]:
    """Compute (probability, conflict_score, entropy) from vote tallies."""
    p = (1 + positive) / (2 + positive + negative)
    if positive == 0 and negative == 0:
        conflict = 0.0
    else:
        bigger = max(positive, negative)
        smaller = min(positive, negative)
        conflict = smaller / bigger if bigger > 0 else 0.0
    if p <= 0.0 or p >= 1.0:
        entropy = 0.0
    else:
        entropy = -(p * math.log2(p) + (1 - p) * math.log2(1 - p))
    return p, conflict, entropy


def aggregate_votes(
    *,
    document_ids: Iterable[str],
    tag_id: str,
    votes: Iterable[LfRunVote],
) -> list[AggregatedLabel]:
    """Pure aggregator: takes a doc-id list + the votes for one run and
    returns one ``AggregatedLabel`` per document (including documents that
    received zero votes -- they get probability 0.5)."""

    pos_count: dict[str, int] = {}
    neg_count: dict[str, int] = {}
    for v in votes:
        vote = int(v.vote)
        if vote > 0:
            pos_count[v.document_id] = pos_count.get(v.document_id, 0) + 1
        elif vote < 0:
            neg_count[v.document_id] = neg_count.get(v.document_id, 0) + 1

    out: list[AggregatedLabel] = []
    for doc_id in document_ids:
        p_n = pos_count.get(doc_id, 0)
        n_n = neg_count.get(doc_id, 0)
        prob, conflict, entropy = aggregate_one(p_n, n_n)
        out.append(
            AggregatedLabel(
                document_id=doc_id,
                tag_id=tag_id,
                probability=prob,
                conflict_score=conflict,
                entropy=entropy,
                positive_votes=p_n,
                negative_votes=n_n,
            )
        )
    return out


def write_probabilistic_labels_for_run(
    db: Session,
    *,
    project_id: str,
    tag_id: str,
    run_id: str,
) -> int:
    """Recompute `ProbabilisticLabel` rows for ``tag_id`` from the votes of
    ``run_id``. Overwrites any existing rows for that tag in this project.
    Returns the number of rows written. The caller is responsible for
    committing.
    """

    document_ids = [
        d
        for d in db.scalars(
            select(Document.id)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at.asc())
        )
    ]

    votes = list(db.scalars(select(LfRunVote).where(LfRunVote.run_id == run_id)).all())

    aggregates = aggregate_votes(
        document_ids=document_ids,
        tag_id=tag_id,
        votes=votes,
    )

    existing_rows = {
        row.document_id: row
        for row in db.scalars(
            select(ProbabilisticLabel).where(
                ProbabilisticLabel.project_id == project_id,
                ProbabilisticLabel.tag_id == tag_id,
            )
        ).all()
    }

    now = datetime.utcnow()
    written = 0
    for agg in aggregates:
        existing = existing_rows.pop(agg.document_id, None)
        if existing is not None:
            existing.probability = agg.probability
            existing.conflict_score = agg.conflict_score
            existing.entropy = agg.entropy
            existing.positive_votes = agg.positive_votes
            existing.negative_votes = agg.negative_votes
            existing.updated_at = now
        else:
            db.add(
                ProbabilisticLabel(
                    project_id=project_id,
                    document_id=agg.document_id,
                    tag_id=tag_id,
                    probability=agg.probability,
                    conflict_score=agg.conflict_score,
                    entropy=agg.entropy,
                    positive_votes=agg.positive_votes,
                    negative_votes=agg.negative_votes,
                    updated_at=now,
                )
            )
        written += 1

    for stale in existing_rows.values():
        db.delete(stale)

    return written


def predicted_label_from_probability(probability: float) -> int:
    """Map a probability to the {-1, 0, +1} label space used elsewhere.

    The 0.5 band is treated as abstain so an uninformed prior (zero votes)
    is not reported as a positive prediction.
    """
    if probability > 0.5:
        return 1
    if probability < 0.5:
        return -1
    return 0
