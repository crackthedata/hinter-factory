from __future__ import annotations

import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import Base, engine
from app.projects_migration import migrate as migrate_projects
from app.routers import (
    documents,
    evaluation,
    gold_labels,
    labeling_functions,
    lf_runs,
    predictions,
    probabilistic,
    projects,
    tags,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_projects(engine)
    yield


settings = get_settings()
app = FastAPI(title="Hinter Factory ML", version="0.1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = traceback.format_exc()
    print(f"UNHANDLED EXCEPTION on {request.method} {request.url}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(documents.router)
app.include_router(predictions.router)
app.include_router(tags.router)
app.include_router(labeling_functions.router)
app.include_router(lf_runs.router)
app.include_router(probabilistic.router)
app.include_router(gold_labels.router)
app.include_router(evaluation.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
