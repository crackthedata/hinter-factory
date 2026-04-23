# See docs/notes-ml.md#servicesmlappproject_scopepy for the mandatory-project_id contract.

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Project


def resolve_project_id(db: Session, project_id: str | None) -> str:
    if not project_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "project_id is required. Pass it as a query parameter (e.g. "
                "?project_id=...) or, for multipart uploads, as a form field. "
                "Use GET /v1/projects to list available projects or POST "
                "/v1/projects to create one."
            ),
        )
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project.id
