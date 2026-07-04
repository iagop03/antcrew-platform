"""Database models for pipeline runs and their artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, JSON, Column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Run(SQLModel, table=True):
    """One pipeline execution."""

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, unique=True)       # antcrew new_run_id() hex
    thread_id: str = Field(default="default")
    team: str                                           # "DevTeam", "MinimalPipeline", …
    request: str
    status: str = Field(default="running")             # running | success | error
    cost_usd: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = Field(default=None)
    state: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class Ticket(SQLModel, table=True):
    """A PM ticket produced by a pipeline run — stable by deterministic ID."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: str = Field(index=True)                 # TICKET-<sha256[:8]>
    run_id: str = Field(index=True)                    # antcrew run_id
    title: str
    description: str = Field(default="")
    priority: str = Field(default="medium")
    status: str = Field(default="open")
    prd_title: str = Field(default="")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Event(SQLModel, table=True):
    """Raw event emitted by the antcrew event bus."""

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[str] = Field(default=None, index=True)
    thread_id: Optional[str] = Field(default=None)
    event_type: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    timestamp: float
    recorded_at: datetime = Field(default_factory=_utcnow)
