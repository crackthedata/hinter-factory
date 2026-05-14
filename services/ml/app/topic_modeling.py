"""Topic modeling engine for the Hinter Factory.

Fits LDA or NMF on a project's corpus, stores topics and per-document dominant
topic assignments, then provides keyword suggestions aligned to a specific tag
via gold-label signals.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Document, GoldLabel, TopicModel


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

def _fit_model(
    texts: list[str],
    n_topics: int,
    algorithm: str,
    max_features: int,
) -> tuple[Any, Any, list[str]]:
    """Return (fitted_model, doc_topic_matrix, feature_names).

    Imports scikit-learn lazily so the API server starts without requiring it
    for unrelated endpoints.
    """
    if algorithm == "nmf":
        from sklearn.decomposition import NMF
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(
            max_features=max_features, stop_words="english", min_df=2, max_df=0.95
        )
        X = vectorizer.fit_transform(texts)
        model = NMF(n_components=n_topics, random_state=42, max_iter=400)
    else:
        from sklearn.decomposition import LatentDirichletAllocation
        from sklearn.feature_extraction.text import CountVectorizer

        vectorizer = CountVectorizer(
            max_features=max_features, stop_words="english", min_df=2, max_df=0.95
        )
        X = vectorizer.fit_transform(texts)
        model = LatentDirichletAllocation(
            n_components=n_topics, random_state=42, n_jobs=1, max_iter=20
        )

    doc_topic_matrix = model.fit_transform(X)
    feature_names: list[str] = vectorizer.get_feature_names_out().tolist()
    return model, doc_topic_matrix, feature_names


def run_topic_model(
    db: Session,
    *,
    project_id: str,
    model_id: str,
) -> None:
    """Execute topic modeling in a background thread.

    Reads configuration from the existing ``TopicModel`` row, fits the model,
    stores results, and marks the row ``completed`` (or ``failed`` on error).
    The caller must flush/commit before handing off to a thread because this
    function opens its own transaction lifecycle via the passed ``db`` session.
    """
    tm = db.get(TopicModel, model_id)
    if tm is None:
        return

    tm.status = "running"
    db.commit()

    try:
        import numpy as np  # lazy import — keep inside try so missing dep → "failed"
        docs = (
            db.query(Document)
            .filter(Document.project_id == project_id)
            .order_by(Document.created_at)
            .all()
        )
        if len(docs) < tm.n_topics:
            raise ValueError(
                f"Only {len(docs)} documents available; need at least {tm.n_topics} "
                f"(one per requested topic)."
            )

        texts = [d.text for d in docs]
        doc_ids = [d.id for d in docs]

        fitted_model, doc_topic_matrix, feature_names = _fit_model(
            texts, tm.n_topics, tm.algorithm, tm.max_features
        )

        # Top-20 words per topic
        n_top_words = 20
        topics = []
        for topic_idx, component in enumerate(fitted_model.components_):
            top_indices = component.argsort()[::-1][:n_top_words]
            top_words = [
                {"word": feature_names[i], "weight": round(float(component[i]), 6)}
                for i in top_indices
            ]
            topics.append({"id": topic_idx, "top_words": top_words})

        # Dominant topic per document (argmax of per-doc topic distribution)
        dominant = np.argmax(doc_topic_matrix, axis=1).tolist()
        doc_topics: dict[str, int] = {doc_ids[i]: int(dominant[i]) for i in range(len(doc_ids))}

        tm.topics_json = topics
        tm.doc_topics_json = doc_topics
        tm.documents_processed = len(docs)
        tm.status = "completed"
        tm.completed_at = datetime.utcnow()
        db.commit()

    except Exception as exc:  # noqa: BLE001
        db.rollback()
        tm = db.get(TopicModel, model_id)
        if tm:
            tm.status = "failed"
            tm.error = str(exc)
            db.commit()
        raise


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------

_N_TOP_TOPICS = 3   # max topics to surface as "relevant" for a tag
_N_SUGGESTIONS = 15  # max keyword suggestions returned


def get_topic_suggestions(
    db: Session,
    *,
    model_id: str,
    tag_id: str,
    limit: int = 10,
    exclude: set[str] | None = None,
) -> dict:
    """Return keyword suggestions derived from topic–gold-label alignment.

    Algorithm:
    1. For the given tag, find gold-positive (value=+1) and gold-negative
       (value=-1) document ids.
    2. Using the stored doc_topics map, count how often each topic appears in
       positive vs negative documents (normalised by class size).
    3. Rank topics by  (pos_freq − neg_freq)  to find the ones most
       discriminative for the positive class.
    4. Collect top-weighted words from the most relevant topics, merging their
       scores weighted by topic relevance.  Return up to ``limit`` suggestions.

    Returns a dict ready for the API response schema.
    """
    tm = db.get(TopicModel, model_id)
    if tm is None or tm.status != "completed" or not tm.topics_json:
        return {
            "relevant_topics": [],
            "suggestions": [],
            "basis": "no_model",
        }

    topics: list[dict] = tm.topics_json
    doc_topics: dict[str, int] = tm.doc_topics_json or {}
    exclude = exclude or set()

    # ---- gold labels for this tag ----------------------------------------
    gold_rows = db.query(GoldLabel).filter(GoldLabel.tag_id == tag_id).all()
    pos_ids = {g.document_id for g in gold_rows if g.value == 1}
    neg_ids = {g.document_id for g in gold_rows if g.value == -1}

    basis = "gold" if pos_ids else "corpus"

    if not pos_ids:
        # cold start: return top words from the largest topics (by total weight)
        top_topics = topics[:_N_TOP_TOPICS]
        suggestions = _collect_suggestions(top_topics, [], 1.0, exclude, limit)
        return {
            "relevant_topics": [
                {"topic_id": t["id"], "relevance_score": 1.0, "top_words": t["top_words"][:10]}
                for t in top_topics
            ],
            "suggestions": suggestions,
            "basis": basis,
        }

    # ---- topic–class alignment ------------------------------------------
    pos_counts: Counter[int] = Counter()
    neg_counts: Counter[int] = Counter()

    for doc_id, topic_idx in doc_topics.items():
        if doc_id in pos_ids:
            pos_counts[topic_idx] += 1
        elif doc_id in neg_ids:
            neg_counts[topic_idx] += 1

    total_pos = max(len(pos_ids), 1)
    total_neg = max(len(neg_ids), 1)

    topic_scores: dict[int, float] = {}
    for t in topics:
        tid: int = t["id"]
        pos_freq = pos_counts.get(tid, 0) / total_pos
        neg_freq = neg_counts.get(tid, 0) / total_neg
        topic_scores[tid] = pos_freq - neg_freq

    sorted_pairs = sorted(topic_scores.items(), key=lambda kv: kv[1], reverse=True)
    top_pairs = [(tid, score) for tid, score in sorted_pairs[:_N_TOP_TOPICS] if score > 0]

    relevant_topics = []
    for tid, score in top_pairs:
        t = next((x for x in topics if x["id"] == tid), None)
        if t:
            relevant_topics.append(
                {
                    "topic_id": tid,
                    "relevance_score": round(score, 4),
                    "top_words": t["top_words"][:10],
                }
            )

    suggestions = _collect_suggestions(
        [next(x for x in topics if x["id"] == tid) for tid, _ in top_pairs],
        [score for _, score in top_pairs],
        total_pos,
        exclude,
        limit,
    )

    return {
        "relevant_topics": relevant_topics,
        "suggestions": suggestions,
        "basis": basis,
    }


def _collect_suggestions(
    relevant_topics: list[dict],
    topic_scores: list[float],
    scale: float,
    exclude: set[str],
    limit: int,
) -> list[dict]:
    """Merge word weights across topics, apply exclusion, return top-``limit`` words."""
    word_scores: dict[str, float] = {}
    for i, t in enumerate(relevant_topics):
        rel_score = topic_scores[i] if topic_scores else scale
        for tw in t.get("top_words", []):
            word = tw["word"]
            if word in exclude:
                continue
            word_scores[word] = word_scores.get(word, 0.0) + float(tw["weight"]) * rel_score

    ranked = sorted(word_scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"word": w, "score": round(s, 6)} for w, s in ranked]
