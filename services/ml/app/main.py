from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import Base, engine
from app.routers import documents, gold_labels, labeling_functions, lf_runs, probabilistic, tags


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


settings = get_settings()
app = FastAPI(title="Hinter Factory ML", version="0.1.0", lifespan=lifespan)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(tags.router)
app.include_router(labeling_functions.router)
app.include_router(lf_runs.router)
app.include_router(probabilistic.router)
app.include_router(gold_labels.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
