"""Label-priority queries that drive Explore's "Smart pick" mode and the
coverage-gap banner.

Both endpoints pivot off the latest *completed* ``LfRun`` for a tag (matching
what Evaluation already does via ``app.evaluation.find_latest_completed_run``).
The module is deliberately a pair of pure functions over a SQLAlchemy session
so the router stays a thin wrapper.

The supported priority modes are:

- ``uncertain`` -- unlabeled docs with at least one LF vote in the run, sorted
  by ``|vote_sum|`` ascending (the doc whose prediction would be most easily
  flipped by one extra label first), then by ``vote_count`` descending (so a
  3-vs-3 split beats a 1-vs-1).
- ``no_lf_fires`` -- unlabeled docs the run produced zero votes for. These are
  the coverage holes; gold-labeling them measures real recall.
- ``weak_positive`` -- unlabeled docs predicted ``+1`` but only because a
  single LF fired alone. The most likely false positives.

Coverage stats are intentionally cheap (no live LF execution): they sample
``ORDER BY id LIMIT N`` from the project's documents and count how many
appear in ``lf_run_votes`` for the run.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import (
    String,
    cast,
    func,
    not_,
    or_,
    select,
)
from sqlalchemy.orm import Session

from app.evaluation import find_latest_completed_run
from app.models import (
    Document,
    GoldLabel,
    LabelingFunction,
    LfRun,
    LfRunVote,
    Tag,
)


PriorityMode = Literal["uncertain", "no_lf_fires", "weak_positive"]
PRIORITY_MODES: tuple[PriorityMode, ...] = (
    "uncertain",
    "no_lf_fires",
    "weak_positive",
)


@dataclass
class PriorityVote:
    labeling_function_id: str
    labeling_function_name: str
    vote: int


@dataclass
class PriorityRow:
    id: str
    text: str
    metadata: dict
    char_length: int
    created_at: str
    vote_sum: int
    vote_count: int
    votes: list[PriorityVote] = field(default_factory=list)


@dataclass
class LabelPriorityResult:
    run_id: str | None
    mode: PriorityMode
    total: int
    items: list[PriorityRow]
    message: str | None = None


@dataclass
class CoverageStatsResult:
    tag_id: str
    run_id: str | None
    sample_size: int
    sample_no_lf_fires: int
    no_lf_fires_rate: float | None
    estimated_recall_ceiling: float | None
    sample_with_gold: int
    message: str | None = None


def _length_clause(bucket: str):
    # Mirrors app.routers.documents._length_clause so the priority endpoints
    # can compose the same filters Explore already uses. Kept inline (rather
    # than imported) so this module doesn't depend on a router.
    if bucket == "short":
        return Document.char_length < 100
    if bucket == "medium":
        return (Document.char_length >= 100) & (Document.char_length < 500)
    if bucket == "long":
        return Document.char_length >= 500
    raise ValueError(f"unknown length bucket: {bucket}")


def _apply_explore_filters(
    stmt,
    *,
    q: str | None,
    length_bucket: list[str] | None,
    metadata_key: str | None,
    metadata_value: str | None,
):
    if q:
        stmt = stmt.where(Document.text.ilike(f"%{q}%"))
    if length_bucket:
        stmt = stmt.where(or_(*[_length_clause(b) for b in length_bucket]))
    if metadata_key and metadata_value is not None:
        path = f"$.{metadata_key}"
        stmt = stmt.where(
            func.lower(cast(func.json_extract(Document.metadata_json, path), String))
            == metadata_value.lower()
        )
    return stmt


def _resolve_run(db: Session, *, tag_id: str, run_id: str | None) -> LfRun | None:
    if run_id:
        run = db.get(LfRun, run_id)
        if run is None or run.tag_id != tag_id or run.status != "completed":
            return None
        return run
    return find_latest_completed_run(db, tag_id)


def list_label_priority(
    db: Session,
    *,
    project_id: str,
    tag_id: str,
    mode: PriorityMode,
    run_id: str | None = None,
    q: str | None = None,
    length_bucket: list[str] | None = None,
    metadata_key: str | None = None,
    metadata_value: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> LabelPriorityResult:
    if mode not in PRIORITY_MODES:
        raise ValueError(f"unknown priority mode: {mode}")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    run = _resolve_run(db, tag_id=tag_id, run_id=run_id)
    if run is None:
        return LabelPriorityResult(
            run_id=None,
            mode=mode,
            total=0,
            items=[],
            message="No completed LF run for this tag yet. Run LFs in LF Studio first.",
        )

    # NOT EXISTS: docs already gold-labeled for this tag are off the table.
    already_labeled = (
        select(GoldLabel.id)
        .where(
            GoldLabel.document_id == Document.id,
            GoldLabel.tag_id == tag_id,
        )
        .exists()
    )

    base = (
        select(Document)
        .where(Document.project_id == project_id)
        .where(not_(already_labeled))
    )
    base = _apply_explore_filters(
        base,
        q=q,
        length_bucket=length_bucket,
        metadata_key=metadata_key,
        metadata_value=metadata_value,
    )

    # Aggregated vote stats per doc, restricted to this run. Abstains
    # (vote == 0) are stored alongside real votes in lf_run_votes, so we
    # exclude them from the count -- otherwise every doc looks "covered".
    vote_stats_subq = (
        select(
            LfRunVote.document_id.label("doc_id"),
            func.coalesce(func.sum(LfRunVote.vote), 0).label("vote_sum"),
            func.count(LfRunVote.id).label("vote_count"),
        )
        .where(LfRunVote.run_id == run.id, LfRunVote.vote != 0)
        .group_by(LfRunVote.document_id)
        .subquery()
    )

    stmt = base.add_columns(
        func.coalesce(vote_stats_subq.c.vote_sum, 0).label("vote_sum"),
        func.coalesce(vote_stats_subq.c.vote_count, 0).label("vote_count"),
    ).join(
        vote_stats_subq,
        vote_stats_subq.c.doc_id == Document.id,
        isouter=True,
    )

    if mode == "uncertain":
        stmt = stmt.where(func.coalesce(vote_stats_subq.c.vote_count, 0) > 0)
        stmt = stmt.order_by(
            func.abs(func.coalesce(vote_stats_subq.c.vote_sum, 0)).asc(),
            func.coalesce(vote_stats_subq.c.vote_count, 0).desc(),
            Document.id,
        )
    elif mode == "no_lf_fires":
        stmt = stmt.where(func.coalesce(vote_stats_subq.c.vote_count, 0) == 0)
        stmt = stmt.order_by(Document.id)
    else:  # weak_positive
        stmt = stmt.where(
            func.coalesce(vote_stats_subq.c.vote_sum, 0) >= 1,
            func.coalesce(vote_stats_subq.c.vote_count, 0) == 1,
        )
        stmt = stmt.order_by(Document.id)

    # Total under the same filters but without the OFFSET/LIMIT.
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(db.scalar(count_stmt) or 0)

    rows = list(db.execute(stmt.offset(offset).limit(limit)).all())
    if not rows:
        return LabelPriorityResult(run_id=run.id, mode=mode, total=total, items=[])

    doc_ids = [row[0].id for row in rows]
    votes_by_doc: dict[str, list[LfRunVote]] = defaultdict(list)
    for v in db.scalars(
        select(LfRunVote).where(
            LfRunVote.run_id == run.id,
            LfRunVote.vote != 0,
            LfRunVote.document_id.in_(doc_ids),
        )
    ).all():
        votes_by_doc[v.document_id].append(v)

    lf_ids = {v.labeling_function_id for vs in votes_by_doc.values() for v in vs}
    lf_names: dict[str, str] = {}
    if lf_ids:
        for lf in db.scalars(
            select(LabelingFunction).where(LabelingFunction.id.in_(lf_ids))
        ).all():
            lf_names[lf.id] = lf.name

    items: list[PriorityRow] = []
    for doc, vote_sum, vote_count in rows:
        doc_votes = votes_by_doc.get(doc.id, [])
        priority_votes = [
            PriorityVote(
                labeling_function_id=v.labeling_function_id,
                labeling_function_name=lf_names.get(v.labeling_function_id, "(deleted LF)"),
                vote=int(v.vote),
            )
            for v in doc_votes
        ]
        priority_votes.sort(key=lambda x: x.labeling_function_name.lower())
        items.append(
            PriorityRow(
                id=doc.id,
                text=doc.text,
                metadata=dict(doc.metadata_json or {}),
                char_length=doc.char_length,
                created_at=doc.created_at.isoformat() + "Z",
                vote_sum=int(vote_sum or 0),
                vote_count=int(vote_count or 0),
                votes=priority_votes,
            )
        )

    return LabelPriorityResult(run_id=run.id, mode=mode, total=total, items=items)


def coverage_stats(
    db: Session,
    *,
    project_id: str,
    tag_id: str,
    run_id: str | None = None,
    sample_size: int = 200,
) -> CoverageStatsResult:
    sample_size = max(1, min(int(sample_size), 2000))

    tag = db.get(Tag, tag_id)
    if not tag or tag.project_id != project_id:
        return CoverageStatsResult(
            tag_id=tag_id,
            run_id=None,
            sample_size=0,
            sample_no_lf_fires=0,
            no_lf_fires_rate=None,
            estimated_recall_ceiling=None,
            sample_with_gold=0,
            message="Tag does not belong to this project.",
        )

    run = _resolve_run(db, tag_id=tag_id, run_id=run_id)
    if run is None:
        return CoverageStatsResult(
            tag_id=tag_id,
            run_id=None,
            sample_size=0,
            sample_no_lf_fires=0,
            no_lf_fires_rate=None,
            estimated_recall_ceiling=None,
            sample_with_gold=0,
            message="No completed LF run for this tag yet. Run LFs in LF Studio first.",
        )

    # Deterministic ORDER BY id sample so refreshes are repeatable.
    sample_doc_ids = list(
        db.scalars(
            select(Document.id)
            .where(Document.project_id == project_id)
            .order_by(Document.id)
            .limit(sample_size)
        )
    )
    actual_size = len(sample_doc_ids)
    if actual_size == 0:
        return CoverageStatsResult(
            tag_id=tag_id,
            run_id=run.id,
            sample_size=0,
            sample_no_lf_fires=0,
            no_lf_fires_rate=None,
            estimated_recall_ceiling=None,
            sample_with_gold=0,
            message="No documents in this project.",
        )

    docs_with_votes = set(
        db.scalars(
            select(LfRunVote.document_id)
            .where(
                LfRunVote.run_id == run.id,
                LfRunVote.vote != 0,
                LfRunVote.document_id.in_(sample_doc_ids),
            )
            .distinct()
        )
    )
    no_fire_count = sum(1 for d in sample_doc_ids if d not in docs_with_votes)
    no_fire_rate = no_fire_count / actual_size

    sample_with_gold = int(
        db.scalar(
            select(func.count())
            .select_from(GoldLabel)
            .where(
                GoldLabel.tag_id == tag_id,
                GoldLabel.document_id.in_(sample_doc_ids),
            )
        )
        or 0
    )

    return CoverageStatsResult(
        tag_id=tag_id,
        run_id=run.id,
        sample_size=actual_size,
        sample_no_lf_fires=no_fire_count,
        no_lf_fires_rate=no_fire_rate,
        estimated_recall_ceiling=1.0 - no_fire_rate,
        sample_with_gold=sample_with_gold,
        message=None,
    )
