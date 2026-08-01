"""Webhook delivery and configuration models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow


class WebhookDelivery(SQLModel, table=True):
    """A webhook delivery attempt with retry tracking."""

    __tablename__ = "webhook_delivery"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    url: str
    payload_json: str
    status: str = Field(default="pending")  # pending | delivered | retrying | failed
    attempts: int = Field(default=0)
    next_retry_at: datetime = Field(default_factory=_utcnow)
    last_error: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class WebhookConfig(SQLModel, table=True):
    """Per-workspace webhook registration — fires on subscribed event types."""

    __tablename__ = "webhook_config"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    url: str
    label: Optional[str] = Field(default=None)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)


class WebhookEvent(SQLModel, table=True):
    """Event-type subscription for a WebhookConfig (one row per event type per webhook)."""

    __tablename__ = "webhook_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    webhook_id: int = Field(index=True)   # FK → webhook_config.id
    event_type: str = Field(index=True)   # e.g. "pipeline.end", "hitl.review_required", "*"
