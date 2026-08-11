from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from models.db import get_session, init_db
from routers import documents, extract, metrics, review
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="logistics-doc-automation",
    description="Intelligent document processing with deterministic-first extraction, "
    "GPT-4o Vision fallback, and confidence-based human-in-the-loop routing.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(extract.router)
app.include_router(documents.router)
app.include_router(review.router)
app.include_router(metrics.router)


@app.get("/live")
def live():
    """Liveness: the process is up. Never touches a dependency."""
    return {"status": "ok"}


@app.get("/health")
def health(db: Session = Depends(get_session)):
    """Readiness: the API can actually serve requests.

    Every real endpoint needs the database, so a health check that does not
    reach it reports "ok" while /documents and /metrics return 500 — and a load
    balancer keeps routing traffic to an API that cannot answer. 503 here takes
    the instance out of rotation instead.
    """
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": type(exc).__name__},
        )
    return {"status": "ok", "database": "ok"}
