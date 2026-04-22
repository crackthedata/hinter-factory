"""Helpers to resolve and validate the active project for a request.

All scoped routes accept `project_id` as a query (or form) parameter. It is
**required** — there is no implicit fallback project anymore. If `project_id`
is missing, callers get HTTP 400 with a clear message; if it doesn't match an
existing project they get 404.

The web client injects `project_id` automatically via
`apps/web/lib/api.ts:projectScopeMiddleware` and `apps/web/lib/ml-fetch.ts`,
sourced from the active project header. CLI/curl callers must pass it
explicitly.
"""

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
