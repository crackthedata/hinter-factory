"""Heuristic candidate-keyword miner for the "Suggested hinters" panel.

Given a tag, propose new keyword labeling functions by mining three signal
sources for *positive* (+1) hinters and two for *negative* (-1) hinters:

Positive hinter signal sources
--------------------------------
1. Gold-labeled documents (positive class) — tokens more frequent in gold-+1
   docs than gold-−1 docs are the primary candidates.
2. Missed-positive documents from the latest completed LF run — gold-+1 docs
   where the run produced a false-negative (vote_sum < 0) or abstained
   (vote_sum == 0) receive a score boost so suggestions target the actual
   recall gaps rather than already-covered ground.
3. Cold-start corpus sample — when no gold labels exist, tag-name tokens from
   a recent corpus sample are used as a fallback.

Negative hinter signal sources
--------------------------------
1. Gold-labeled documents (negative class) — tokens more frequent in gold-−1
   docs than gold-+1 docs.
2. Missed-negative documents from the latest completed LF run — gold-−1 docs
   where the run abstained (vote_sum == 0) or produced a false-positive
   (vote_sum > 0) receive a symmetric score boost so suggestions also target
   the precision gaps, not just the recall gaps.

Other invariants
-----------------
- The tag name always gets a small boost in the positive direction.
- Tokens already in an existing keyword/regex LF (per direction) are excluded.
- ``exclude`` lets the UI thread dismissed suggestions back as covered tokens.

The miner is a pure function over the database session; it does not write
anything. It is invoked on-demand by the
``GET /v1/labeling-functions/suggestions`` endpoint.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, GoldLabel, LabelingFunction, LfRun, LfRunVote, Tag


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9']{2,}")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[_\-\s]+")
_REGEX_TOKEN_RE = re.compile(r"[A-Za-z]{3,}")

# Smaller than nltk's list, but enough to drop the obvious noise.
_STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are aren as at
    be because been before being below between both but by can cannot could
    couldn did didn do does doesn doing don down during each few for from
    further had hadn has hasn have haven having he her here hers herself him
    himself his how however i if in into is isn it its itself just like ll
    may maybe me might more most mustn my myself no nor not now of off on
    once one only or other our ours ourselves out over own re really s same
    shan she should shouldn so some such t than that the their theirs them
    themselves then there these they this those through to too under until
    up ve very was wasn we were weren what when where which while who whom
    why will with won would wouldn y you your yours yourself yourselves
    """.split()
)

_TAG_NAME_BOOST = 0.5
_MISS_BOOST = 0.5        # additive score uplift per unit of log(1 + missed_df)
_COLD_START_DOC_SAMPLE = 500
_EXAMPLE_DOC_LIMIT = 3
_MAX_DOCS_PER_CLASS = 1000
_MAX_MISSED_DOCS = 500   # cap so large runs stay cheap


@dataclass
class HinterSuggestion:
    keyword: str
    score: float
    return_value: int  # +1 for positive hinter, -1 for negative hinter
    positive_hits: int
    negative_hits: int
    example_document_ids: list[str] = field(default_factory=list)


@dataclass
class SuggestionResult:
    tag_id: str
    generated_at: datetime
    basis: Literal["gold", "tag_name", "mixed"]
    suggestions: list[HinterSuggestion]


def _tokenize(text: str) -> set[str]:
    """Return the set of distinct, non-stopword tokens of length >= 3."""
    if not text:
        return set()
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        tok = raw.lower()
        if len(tok) < 3:
            continue
        if tok in _STOPWORDS:
            continue
        out.add(tok)
    return out


def _tag_name_tokens(name: str) -> list[str]:
    """Split a tag name like 'is_invoice' or 'requiresLegalReview' into tokens."""
    if not name:
        return []
    parts = _CAMEL_SPLIT_RE.split(name)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tok = part.strip().lower()
        if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _covered_tokens(lfs: Iterable[LabelingFunction]) -> tuple[set[str], set[str]]:
    """Tokens already represented by keyword/regex LFs, split by return_value direction.

    Returns ``(covered_positive, covered_negative)`` so that a token can still be
    suggested as a negative hinter even if a positive LF already uses it, and
    vice-versa.
    """
    covered_pos: set[str] = set()
    covered_neg: set[str] = set()
    for lf in lfs:
        config = dict(lf.config or {})
        rv = config.get("return_value", 1)
        target = covered_neg if rv == -1 else covered_pos
        if lf.type == "keywords":
            for kw in config.get("keywords") or []:
                if isinstance(kw, str):
                    target.add(kw.strip().lower())
        elif lf.type == "regex":
            pattern = config.get("pattern")
            if isinstance(pattern, str):
                for tok in _REGEX_TOKEN_RE.findall(pattern):
                    target.add(tok.lower())
    covered_pos.discard("")
    covered_neg.discard("")
    return covered_pos, covered_neg


def _missed_positive_doc_ids(
    db: Session,
    *,
    tag_id: str,
    gold_positive_ids: set[str],
) -> list[str]:
    """Return gold-positive document IDs that the latest completed run missed.

    "Missed" covers two evaluation buckets:
    - *false_negative*: vote_sum < 0 (LFs actively voted against a true positive).
    - *abstain_on_positive*: vote_sum == 0 (no LF fired on a true positive).

    Returns an empty list when there is no completed run yet for the tag, or
    when ``gold_positive_ids`` is empty.
    """
    if not gold_positive_ids:
        return []

    run = db.scalar(
        select(LfRun)
        .where(LfRun.tag_id == tag_id, LfRun.status == "completed")
        .order_by(LfRun.completed_at.desc(), LfRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        return []

    doc_ids_list = list(gold_positive_ids)
    vote_rows = db.scalars(
        select(LfRunVote).where(
            LfRunVote.run_id == run.id,
            LfRunVote.document_id.in_(doc_ids_list),
        )
    ).all()

    vote_sums: dict[str, int] = defaultdict(int)
    for v in vote_rows:
        vote_sums[v.document_id] += int(v.vote)

    # vote_sum == 0 catches both "no LF fired" and "exact tie" — both are misses.
    missed = [doc_id for doc_id in doc_ids_list if vote_sums.get(doc_id, 0) <= 0]
    return missed[:_MAX_MISSED_DOCS]


def _missed_negative_doc_ids(
    db: Session,
    *,
    tag_id: str,
    gold_negative_ids: set[str],
) -> list[str]:
    """Return gold-negative document IDs that the latest completed run missed.

    "Missed" covers two evaluation buckets:
    - *false_positive*: vote_sum > 0 (LFs actively voted for a true negative).
    - *abstain_on_negative*: vote_sum == 0 (no LF fired on a true negative).

    Returns an empty list when there is no completed run yet for the tag, or
    when ``gold_negative_ids`` is empty.
    """
    if not gold_negative_ids:
        return []

    run = db.scalar(
        select(LfRun)
        .where(LfRun.tag_id == tag_id, LfRun.status == "completed")
        .order_by(LfRun.completed_at.desc(), LfRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        return []

    doc_ids_list = list(gold_negative_ids)
    vote_rows = db.scalars(
        select(LfRunVote).where(
            LfRunVote.run_id == run.id,
            LfRunVote.document_id.in_(doc_ids_list),
        )
    ).all()

    vote_sums: dict[str, int] = defaultdict(int)
    for v in vote_rows:
        vote_sums[v.document_id] += int(v.vote)

    # vote_sum == 0 catches both "no LF fired" and "exact tie"; vote_sum > 0
    # means a false positive — both are misses for the negative class.
    missed = [doc_id for doc_id in doc_ids_list if vote_sums.get(doc_id, 0) >= 0]
    return missed[:_MAX_MISSED_DOCS]


def suggest_keywords_for_tag(
    db: Session,
    *,
    project_id: str,
    tag_id: str,
    limit: int = 10,
    exclude: Iterable[str] | None = None,
) -> SuggestionResult:
    """Compute keyword suggestions for a tag, including both positive (+1) and
    negative (-1) hinters.

    The caller is responsible for verifying ``tag_id`` belongs to ``project_id``.

    ``exclude`` is an optional set of tokens to treat as already-covered (used
    by the UI to thread dismissed suggestions back so a refresh surfaces fresh
    candidates instead of the same ones).
    """
    limit = max(1, min(int(limit), 50))
    generated_at = datetime.utcnow()

    existing_lfs = list(
        db.scalars(
            select(LabelingFunction).where(
                LabelingFunction.project_id == project_id,
                LabelingFunction.tag_id == tag_id,
            )
        )
    )
    covered_pos, covered_neg = _covered_tokens(existing_lfs)

    # Dismissed tokens from the UI are excluded from both directions.
    if exclude:
        for token in exclude:
            if isinstance(token, str):
                tok = token.strip().lower()
                if tok:
                    covered_pos.add(tok)
                    covered_neg.add(tok)

    tag = db.get(Tag, tag_id)
    name_tokens = _tag_name_tokens(tag.name) if tag else []

    gold_rows = list(
        db.scalars(
            select(GoldLabel).where(
                GoldLabel.project_id == project_id,
                GoldLabel.tag_id == tag_id,
            )
        )
    )

    pos_doc_ids = [g.document_id for g in gold_rows if g.value == 1]
    neg_doc_ids = [g.document_id for g in gold_rows if g.value == -1]

    # Cap the per-class doc set so the miner stays cheap on huge validation sets.
    pos_doc_ids = pos_doc_ids[:_MAX_DOCS_PER_CLASS]
    neg_doc_ids = neg_doc_ids[:_MAX_DOCS_PER_CLASS]

    pos_docs = _fetch_documents(db, pos_doc_ids) if pos_doc_ids else []
    neg_docs = _fetch_documents(db, neg_doc_ids) if neg_doc_ids else []

    pos_df: dict[str, int] = defaultdict(int)
    neg_df: dict[str, int] = defaultdict(int)
    pos_examples: dict[str, list[str]] = defaultdict(list)
    neg_examples: dict[str, list[str]] = defaultdict(list)

    # Process missed-positive docs first so they get priority in example slots.
    missed_pos_ids = _missed_positive_doc_ids(
        db, tag_id=tag_id, gold_positive_ids=set(pos_doc_ids)
    )
    missed_docs = _fetch_documents(db, missed_pos_ids) if missed_pos_ids else []
    missed_df: dict[str, int] = defaultdict(int)
    for doc in missed_docs:
        toks = _tokenize(doc.text)
        for tok in toks:
            missed_df[tok] += 1
            if len(pos_examples[tok]) < _EXAMPLE_DOC_LIMIT:
                pos_examples[tok].append(doc.id)

    # Process missed-negative docs (abstain-on-negative + false-positive from
    # the latest run) so the negative scorer can boost tokens that appear there.
    missed_neg_ids = _missed_negative_doc_ids(
        db, tag_id=tag_id, gold_negative_ids=set(neg_doc_ids)
    )
    missed_neg_docs = _fetch_documents(db, missed_neg_ids) if missed_neg_ids else []
    missed_neg_df: dict[str, int] = defaultdict(int)
    for doc in missed_neg_docs:
        toks = _tokenize(doc.text)
        for tok in toks:
            missed_neg_df[tok] += 1
            if len(neg_examples[tok]) < _EXAMPLE_DOC_LIMIT:
                neg_examples[tok].append(doc.id)

    for doc in pos_docs:
        toks = _tokenize(doc.text)
        for tok in toks:
            pos_df[tok] += 1
            if len(pos_examples[tok]) < _EXAMPLE_DOC_LIMIT:
                pos_examples[tok].append(doc.id)
    for doc in neg_docs:
        toks = _tokenize(doc.text)
        for tok in toks:
            neg_df[tok] += 1
            if len(neg_examples[tok]) < _EXAMPLE_DOC_LIMIT:
                neg_examples[tok].append(doc.id)

    has_gold = bool(pos_docs or neg_docs)
    min_pos = 2 if len(pos_docs) >= 5 else 1
    min_neg = 2 if len(neg_docs) >= 5 else 1

    # Scored candidates: keyed by (token, return_value) to allow the same token
    # to appear as both a positive and a negative hinter when the signal differs.
    scored_pos: dict[str, float] = {}
    scored_neg: dict[str, float] = {}

    # Positive hinter candidates: tokens more frequent in gold-positive docs.
    # Tokens that also appear in missed-positive docs (FN / abstain-on-positive
    # from the latest run) receive an additive boost so suggestions target the
    # real recall gaps rather than documents already covered.
    for tok, p in pos_df.items():
        if tok in covered_pos:
            continue
        n = neg_df.get(tok, 0)
        if p < min_pos:
            continue
        if p <= n:
            continue
        # Smoothed positive log-odds, weighted by support so a token in 5 pos
        # docs beats one in 2 even at the same purity.
        base_score = math.log((p + 0.5) / (n + 0.5)) * math.log(1 + p)
        miss_boost = _MISS_BOOST * math.log(1 + missed_df.get(tok, 0))
        scored_pos[tok] = base_score + miss_boost

    # Negative hinter candidates: tokens more frequent in gold-negative docs.
    # Tokens that appear in missed-negative docs (abstain-on-negative or
    # false-positive from the latest run) receive the same additive boost as
    # missed-positive tokens do for positive hinters, targeting precision gaps.
    for tok, n in neg_df.items():
        if tok in covered_neg:
            continue
        p = pos_df.get(tok, 0)
        if n < min_neg:
            continue
        if n <= p:
            continue
        base_score = math.log((n + 0.5) / (p + 0.5)) * math.log(1 + n)
        miss_boost = _MISS_BOOST * math.log(1 + missed_neg_df.get(tok, 0))
        scored_neg[tok] = base_score + miss_boost

    # Cold-start: fall back to a corpus sample when there are zero gold labels.
    if not has_gold:
        sample_docs = list(
            db.scalars(
                select(Document)
                .where(Document.project_id == project_id)
                .order_by(Document.created_at.desc())
                .limit(_COLD_START_DOC_SAMPLE)
            )
        )
        corpus_df: dict[str, int] = defaultdict(int)
        for doc in sample_docs:
            toks = _tokenize(doc.text)
            for tok in toks:
                corpus_df[tok] += 1
                if len(pos_examples[tok]) < _EXAMPLE_DOC_LIMIT:
                    pos_examples[tok].append(doc.id)
        # Without supervision we have no signal; surface only tag-name tokens
        # as positive-hinter candidates.
        for tok in name_tokens:
            if tok in covered_pos or tok in scored_pos:
                continue
            if corpus_df.get(tok, 0) == 0:
                # Still suggest the bare tag-name token even if it doesn't
                # appear in the sample - the user named the tag this for a reason.
                scored_pos[tok] = _TAG_NAME_BOOST
            else:
                scored_pos[tok] = _TAG_NAME_BOOST + math.log(1 + corpus_df[tok])

    # Tag-name boost for positive hinters (always): nudge tag-name tokens toward
    # the top whether or not gold labels exist.
    for tok in name_tokens:
        if tok in covered_pos:
            continue
        scored_pos[tok] = scored_pos.get(tok, 0.0) + _TAG_NAME_BOOST

    # Take the top `limit` candidates from each direction independently so the
    # caller always receives up to `limit` positive suggestions AND up to `limit`
    # negative suggestions (up to 2×limit total).
    top_pos = sorted(scored_pos.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    top_neg = sorted(scored_neg.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    # Positive suggestions first, then negative — preserves a predictable order
    # that the UI can rely on for its two-section layout.
    top = [(tok, score, 1) for tok, score in top_pos] + [
        (tok, score, -1) for tok, score in top_neg
    ]

    suggestions = [
        HinterSuggestion(
            keyword=tok,
            score=round(score, 4),
            return_value=rv,
            positive_hits=pos_df.get(tok, 0),
            negative_hits=neg_df.get(tok, 0),
            example_document_ids=list(
                (neg_examples if rv == -1 else pos_examples).get(tok, [])
            )[:_EXAMPLE_DOC_LIMIT],
        )
        for tok, score, rv in top
    ]

    if has_gold and any(
        (s.positive_hits > 0 and s.return_value == 1)
        or (s.negative_hits > 0 and s.return_value == -1)
        for s in suggestions
    ):
        basis: Literal["gold", "tag_name", "mixed"] = (
            "mixed" if name_tokens else "gold"
        )
    else:
        basis = "tag_name"

    return SuggestionResult(
        tag_id=tag_id,
        generated_at=generated_at,
        basis=basis,
        suggestions=suggestions,
    )


def _fetch_documents(db: Session, ids: list[str]) -> list[Document]:
    if not ids:
        return []
    rows = list(db.scalars(select(Document).where(Document.id.in_(ids))))
    # Preserve the order of ids passed in so example_document_ids is stable.
    by_id = {d.id: d for d in rows}
    return [by_id[i] for i in ids if i in by_id]
