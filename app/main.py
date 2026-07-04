"""antcrew-platform FastAPI application."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import init_db
from app.core.listener import start_listening, stop_listening
from app.api import runs, tickets, stream, pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_listening()
    yield
    stop_listening()


app = FastAPI(
    title="antcrew-platform",
    version="0.1.0",
    description="Dashboard and API layer for antcrew pipelines",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pipeline.router)
app.include_router(runs.router)
app.include_router(tickets.router)
app.include_router(stream.router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
