# See docs/notes-ml.md#servicesmlapproutersprojectspy for the export bundle shape and import re-mint policy.

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Document,
    GoldLabel,
    LabelingFunction,
    LfRun,
    LfRunLabelingFunction,
    LfRunVote,
    ProbabilisticLabel,
    Project,
    Tag,
)

router = APIRouter(prefix="/v1/projects", tags=["projects"])

EXPORT_FORMAT = "hinter-factory.project"
EXPORT_VERSION = 1


@router.get("")
def list_projects(db: Annotated[Session, Depends(get_db)]):
    rows = db.scalars(select(Project).order_by(Project.created_at.asc())).all()
    return [_serialize(p) for p in rows]


@router.post("", status_code=201)
def create_project(payload: dict, db: Annotated[Session, Depends(get_db)]):
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    description = payload.get("description")
    if db.scalar(select(Project).where(Project.name == name)):
        raise HTTPException(status_code=409, detail="project name already exists")
    project = Project(name=name, description=str(description) if description else None)
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize(project)


@router.get("/{project_id}")
def get_project(project_id: str, db: Annotated[Session, Depends(get_db)]):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    counts = _project_counts(db, project_id)
    return {**_serialize(project), "counts": counts}


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, db: Annotated[Session, Depends(get_db)]):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    for model in (
        ProbabilisticLabel,
        GoldLabel,
        LfRunVote,
        LfRunLabelingFunction,
        LfRun,
        LabelingFunction,
        Tag,
        Document,
    ):
        if model is LfRunVote or model is LfRunLabelingFunction:
            run_ids = [
                r.id
                for r in db.scalars(select(LfRun).where(LfRun.project_id == project_id)).all()
            ]
            if run_ids:
                for child in db.scalars(
                    select(model).where(model.run_id.in_(run_ids))
                ).all():
                    db.delete(child)
            continue
        for child in db.scalars(
            select(model).where(model.project_id == project_id)
        ).all():
            db.delete(child)
    db.delete(project)
    db.commit()
    return None


@router.get("/{project_id}/export")
def export_project(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    include_runs: bool = Query(
        default=True,
        description="Include the latest completed LF run per tag (with votes).",
    ),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    documents = db.scalars(
        select(Document).where(Document.project_id == project_id).order_by(Document.created_at.asc())
    ).all()
    tags = db.scalars(
        select(Tag).where(Tag.project_id == project_id).order_by(Tag.created_at.asc())
    ).all()
    lfs = db.scalars(
        select(LabelingFunction)
        .where(LabelingFunction.project_id == project_id)
        .order_by(LabelingFunction.created_at.asc())
    ).all()
    gold = db.scalars(
        select(GoldLabel).where(GoldLabel.project_id == project_id).order_by(GoldLabel.created_at.asc())
    ).all()
    prob = db.scalars(
        select(ProbabilisticLabel).where(ProbabilisticLabel.project_id == project_id)
    ).all()

    runs_export: list[dict] = []
    if include_runs:
        for tag in tags:
            run = db.scalar(
                select(LfRun)
                .where(
                    LfRun.tag_id == tag.id,
                    LfRun.status == "completed",
                )
                .order_by(LfRun.completed_at.desc(), LfRun.created_at.desc())
                .limit(1)
            )
            if run is None:
                continue
            run_lfs = db.scalars(
                select(LfRunLabelingFunction)
                .where(LfRunLabelingFunction.run_id == run.id)
                .order_by(LfRunLabelingFunction.position.asc())
            ).all()
            votes = db.scalars(select(LfRunVote).where(LfRunVote.run_id == run.id)).all()
            runs_export.append(
                {
                    "id": run.id,
                    "tag_id": run.tag_id,
                    "status": run.status,
                    "documents_scanned": run.documents_scanned,
                    "votes_written": run.votes_written,
                    "created_at": _iso(run.created_at),
                    "completed_at": _iso(run.completed_at),
                    "labeling_function_ids": [r.labeling_function_id for r in run_lfs],
                    "votes": [
                        {
                            "document_id": v.document_id,
                            "labeling_function_id": v.labeling_function_id,
                            "vote": int(v.vote),
                        }
                        for v in votes
                    ],
                }
            )

    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_VERSION,
        "exported_at": _iso(datetime.utcnow()),
        "project": {"name": project.name, "description": project.description},
        "documents": [
            {
                "id": d.id,
                "text": d.text,
                "metadata": dict(d.metadata_json or {}),
                "char_length": d.char_length,
                "created_at": _iso(d.created_at),
            }
            for d in documents
        ],
        "tags": [
            {
                "id": t.id,
                "name": t.name,
                "taxonomy_version": t.taxonomy_version,
                "created_at": _iso(t.created_at),
            }
            for t in tags
        ],
        "labeling_functions": [
            {
                "id": lf.id,
                "tag_id": lf.tag_id,
                "name": lf.name,
                "type": lf.type,
                "config": dict(lf.config or {}),
                "enabled": bool(lf.enabled),
                "created_at": _iso(lf.created_at),
            }
            for lf in lfs
        ],
        "gold_labels": [
            {
                "id": g.id,
                "document_id": g.document_id,
                "tag_id": g.tag_id,
                "value": int(g.value),
                "note": g.note,
                "created_at": _iso(g.created_at),
            }
            for g in gold
        ],
        "lf_runs": runs_export,
        "probabilistic_labels": [
            {
                "document_id": p.document_id,
                "tag_id": p.tag_id,
                "probability": p.probability,
                "conflict_score": p.conflict_score,
                "entropy": p.entropy,
                "updated_at": _iso(p.updated_at),
            }
            for p in prob
        ],
    }


@router.post("/import", status_code=201)
def import_project(
    payload: Annotated[dict, Body(...)],
    db: Annotated[Session, Depends(get_db)],
    target_name: str | None = Query(
        default=None,
        description="Override the imported project's name; otherwise we use the bundled name "
        "(suffixed with ' (imported)' on collision).",
    ),
):
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT:
        raise HTTPException(status_code=400, detail="not a Hinter Factory project export")
    fmt_version = int(payload.get("format_version", 0) or 0)
    if fmt_version != EXPORT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported export format_version {fmt_version}; expected {EXPORT_VERSION}",
        )

    project_blob = payload.get("project") or {}
    raw_name = (target_name or project_blob.get("name") or "Imported project").strip()
    if not raw_name:
        raw_name = "Imported project"
    name = _unique_project_name(db, raw_name)
    description = project_blob.get("description")

    project = Project(name=name, description=str(description) if description else None)
    db.add(project)
    db.flush()

    doc_id_map: dict[str, str] = {}
    for d in payload.get("documents") or []:
        old_id = str(d.get("id") or "")
        text_value = str(d.get("text") or "")
        meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
        new_id = str(uuid.uuid4())
        doc_id_map[old_id] = new_id
        db.add(
            Document(
                id=new_id,
                project_id=project.id,
                text=text_value,
                metadata_json=meta,
                char_length=int(d.get("char_length") or len(text_value)),
            )
        )

    tag_id_map: dict[str, str] = {}
    for t in payload.get("tags") or []:
        old_id = str(t.get("id") or "")
        tag_name = str(t.get("name") or "").strip() or "tag"
        new_id = str(uuid.uuid4())
        tag_id_map[old_id] = new_id
        db.add(
            Tag(
                id=new_id,
                project_id=project.id,
                name=tag_name,
                taxonomy_version=str(t.get("taxonomy_version") or "v1"),
            )
        )

    lf_id_map: dict[str, str] = {}
    for lf in payload.get("labeling_functions") or []:
        old_tag_id = str(lf.get("tag_id") or "")
        if old_tag_id not in tag_id_map:
            continue
        new_id = str(uuid.uuid4())
        lf_id_map[str(lf.get("id") or "")] = new_id
        db.add(
            LabelingFunction(
                id=new_id,
                project_id=project.id,
                tag_id=tag_id_map[old_tag_id],
                name=str(lf.get("name") or "lf"),
                type=str(lf.get("type") or "regex"),
                config=lf.get("config") if isinstance(lf.get("config"), dict) else {},
                enabled=bool(lf.get("enabled", True)),
            )
        )

    for g in payload.get("gold_labels") or []:
        old_doc = str(g.get("document_id") or "")
        old_tag = str(g.get("tag_id") or "")
        if old_doc not in doc_id_map or old_tag not in tag_id_map:
            continue
        try:
            value = int(g.get("value"))
        except (TypeError, ValueError):
            continue
        if value not in (-1, 0, 1):
            continue
        db.add(
            GoldLabel(
                project_id=project.id,
                document_id=doc_id_map[old_doc],
                tag_id=tag_id_map[old_tag],
                value=value,
                note=str(g["note"]) if g.get("note") else None,
            )
        )

    for p in payload.get("probabilistic_labels") or []:
        old_doc = str(p.get("document_id") or "")
        old_tag = str(p.get("tag_id") or "")
        if old_doc not in doc_id_map or old_tag not in tag_id_map:
            continue
        try:
            prob_value = float(p.get("probability"))
        except (TypeError, ValueError):
            continue
        db.add(
            ProbabilisticLabel(
                project_id=project.id,
                document_id=doc_id_map[old_doc],
                tag_id=tag_id_map[old_tag],
                probability=prob_value,
                conflict_score=_safe_float(p.get("conflict_score")),
                entropy=_safe_float(p.get("entropy")),
            )
        )

    for run in payload.get("lf_runs") or []:
        old_tag = str(run.get("tag_id") or "")
        if old_tag not in tag_id_map:
            continue
        new_run_id = str(uuid.uuid4())
        db.add(
            LfRun(
                id=new_run_id,
                project_id=project.id,
                tag_id=tag_id_map[old_tag],
                status=str(run.get("status") or "completed"),
                documents_scanned=int(run.get("documents_scanned") or 0),
                votes_written=int(run.get("votes_written") or 0),
                completed_at=_parse_iso(run.get("completed_at")),
            )
        )
        for pos, old_lf_id in enumerate(run.get("labeling_function_ids") or []):
            new_lf_id = lf_id_map.get(str(old_lf_id))
            if not new_lf_id:
                continue
            db.add(
                LfRunLabelingFunction(
                    run_id=new_run_id,
                    labeling_function_id=new_lf_id,
                    position=pos,
                )
            )
        for v in run.get("votes") or []:
            old_doc = str(v.get("document_id") or "")
            old_lf = str(v.get("labeling_function_id") or "")
            if old_doc not in doc_id_map or old_lf not in lf_id_map:
                continue
            try:
                vote_value = int(v.get("vote"))
            except (TypeError, ValueError):
                continue
            db.add(
                LfRunVote(
                    run_id=new_run_id,
                    document_id=doc_id_map[old_doc],
                    labeling_function_id=lf_id_map[old_lf],
                    vote=vote_value,
                )
            )

    db.commit()
    db.refresh(project)
    counts = _project_counts(db, project.id)
    return {**_serialize(project), "counts": counts}


def _serialize(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "created_at": _iso(p.created_at),
    }


def _project_counts(db: Session, project_id: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    counts["documents"] = len(
        db.scalars(select(Document.id).where(Document.project_id == project_id)).all()
    )
    counts["tags"] = len(db.scalars(select(Tag.id).where(Tag.project_id == project_id)).all())
    counts["labeling_functions"] = len(
        db.scalars(
            select(LabelingFunction.id).where(LabelingFunction.project_id == project_id)
        ).all()
    )
    counts["gold_labels"] = len(
        db.scalars(select(GoldLabel.id).where(GoldLabel.project_id == project_id)).all()
    )
    counts["lf_runs"] = len(
        db.scalars(select(LfRun.id).where(LfRun.project_id == project_id)).all()
    )
    counts["probabilistic_labels"] = len(
        db.scalars(
            select(ProbabilisticLabel.id).where(ProbabilisticLabel.project_id == project_id)
        ).all()
    )
    return dict(counts)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() + "Z"


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).rstrip("Z")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique_project_name(db: Session, base: str) -> str:
    if not db.scalar(select(Project).where(Project.name == base)):
        return base
    suffix = " (imported)"
    candidate = f"{base}{suffix}"
    i = 2
    while db.scalar(select(Project).where(Project.name == candidate)):
        candidate = f"{base}{suffix} {i}"
        i += 1
    return candidate
