from contextlib import asynccontextmanager

from fastapi import FastAPI
from models.db import init_db
from routers import documents, extract, metrics, review


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


@app.get("/health")
def health():
    return {"status": "ok"}
