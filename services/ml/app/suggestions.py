"""Heuristic candidate-keyword miner for the "Suggested hinters" panel.

Given a tag, propose new keyword labeling functions by mining:

- Gold-labeled documents for the tag (positive vs negative class).
- A sample of corpus documents (cold-start fallback when there are no gold labels).
- The tag name itself (always seeded so the panel is useful before any labeling).

Existing keyword/regex labeling functions for the tag are subtracted from the
candidate set so we never re-suggest a token the user has already covered.

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

from app.models import Document, GoldLabel, LabelingFunction, Tag


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
_COLD_START_DOC_SAMPLE = 500
_EXAMPLE_DOC_LIMIT = 3
_MAX_DOCS_PER_CLASS = 1000


@dataclass
class HinterSuggestion:
    keyword: str
    score: float
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


def _covered_tokens(lfs: Iterable[LabelingFunction]) -> set[str]:
    """Tokens already represented by a keyword/regex LF for the tag."""
    covered: set[str] = set()
    for lf in lfs:
        config = dict(lf.config or {})
        if lf.type == "keywords":
            for kw in config.get("keywords") or []:
                if isinstance(kw, str):
                    covered.add(kw.strip().lower())
        elif lf.type == "regex":
            pattern = config.get("pattern")
            if isinstance(pattern, str):
                for tok in _REGEX_TOKEN_RE.findall(pattern):
                    covered.add(tok.lower())
    covered.discard("")
    return covered


def suggest_keywords_for_tag(
    db: Session,
    *,
    project_id: str,
    tag_id: str,
    limit: int = 10,
    exclude: Iterable[str] | None = None,
) -> SuggestionResult:
    """Compute keyword suggestions for a tag.

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
    covered = _covered_tokens(existing_lfs)
    if exclude:
        for token in exclude:
            if isinstance(token, str):
                tok = token.strip().lower()
                if tok:
                    covered.add(tok)

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
    examples: dict[str, list[str]] = defaultdict(list)

    for doc in pos_docs:
        toks = _tokenize(doc.text)
        for tok in toks:
            pos_df[tok] += 1
            if len(examples[tok]) < _EXAMPLE_DOC_LIMIT:
                examples[tok].append(doc.id)
    for doc in neg_docs:
        toks = _tokenize(doc.text)
        for tok in toks:
            neg_df[tok] += 1

    has_gold = bool(pos_docs or neg_docs)
    min_pos = 2 if len(pos_docs) >= 5 else 1

    scored: dict[str, float] = {}

    for tok, p in pos_df.items():
        if tok in covered:
            continue
        n = neg_df.get(tok, 0)
        if p < min_pos:
            continue
        if p <= n:
            continue
        # Smoothed positive log-odds, weighted by support so a token in 5 pos
        # docs beats one in 2 even at the same purity.
        score = math.log((p + 0.5) / (n + 0.5)) * math.log(1 + p)
        scored[tok] = score

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
                if len(examples[tok]) < _EXAMPLE_DOC_LIMIT:
                    examples[tok].append(doc.id)
        # Without supervision we have no signal; surface only tag-name tokens.
        # The corpus sample is still useful because it lets us attach example
        # documents that contain the tag-name token.
        for tok in name_tokens:
            if tok in covered or tok in scored:
                continue
            if corpus_df.get(tok, 0) == 0:
                # Still suggest the bare tag-name token even if it doesn't
                # appear in the sample - the user named the tag this for a
                # reason.
                scored[tok] = _TAG_NAME_BOOST
            else:
                scored[tok] = _TAG_NAME_BOOST + math.log(1 + corpus_df[tok])

    # Tag-name boost (always): nudge tag-name tokens toward the top whether or
    # not gold labels exist.
    for tok in name_tokens:
        if tok in covered:
            continue
        scored[tok] = scored.get(tok, 0.0) + _TAG_NAME_BOOST

    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:limit]

    suggestions = [
        HinterSuggestion(
            keyword=tok,
            score=round(score, 4),
            positive_hits=pos_df.get(tok, 0),
            negative_hits=neg_df.get(tok, 0),
            example_document_ids=list(examples.get(tok, []))[:_EXAMPLE_DOC_LIMIT],
        )
        for tok, score in ranked
    ]

    if has_gold and any(s.positive_hits > 0 for s in suggestions):
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
