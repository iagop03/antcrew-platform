"""Evaluation run, schedule, and comparison models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, JSON, Column

from app.models._utils import _utcnow


class EvalRun(SQLModel, table=True):
    """A platform-dispatched eval run — result of POST /evals/."""

    __tablename__ = "eval_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    eval_id: str = Field(index=True, unique=True)   # UUID
    run_id: Optional[str] = Field(default=None, index=True)  # FK → run.run_id (stub Run)
    team: str
    request: str
    name: str = Field(default="")
    model: str = Field(default="")                  # informational only
    judge_model: str = Field(default="")            # LLM used as eval judge
    status: str = Field(default="running")          # running | done | error
    report: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    error: Optional[str] = Field(default=None)
    cost_usd: float = Field(default=0.0)
    elapsed_ms: float = Field(default=0.0)
    workspace_id: Optional[int] = Field(default=None)
    regression_id: Optional[str] = Field(default=None, index=True)  # batch ID when created via POST /evals/regression
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = Field(default=None)


class EvalSchedule(SQLModel, table=True):
    """A recurring eval schedule that dispatches EvalRun entries on a cron-like basis."""

    __tablename__ = "eval_schedule"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    team: str
    request: str
    interval_hours: float = Field(default=24.0)
    next_run_at: datetime = Field(default_factory=_utcnow)
    enabled: bool = Field(default=True)
    model: str = Field(default="")
    judge_model: str = Field(default="")
    expect_min_tickets: int = Field(default=0)
    expect_min_code_files: int = Field(default=0)
    expect_review_verdict: str = Field(default="")
    workspace_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    last_eval_id: Optional[str] = Field(default=None)


class CompareRun(SQLModel, table=True):
    """Side-by-side comparison of the same request run against two LLM backends."""

    __tablename__ = "compare_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    compare_id: str = Field(index=True, unique=True)
    run_id_a: str = Field(index=True)
    run_id_b: str = Field(index=True)
    model_a: str
    model_b: str
    team: str
    request: str
    status: str = Field(default="running")  # running | done | error
    workspace_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = Field(default=None)
