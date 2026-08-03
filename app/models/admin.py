"""Admin-only models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow


class PlatformConfig(SQLModel, table=True):
    """Singleton row (id=1) storing platform-wide billing defaults.

    These are the base cost multipliers before any campaign discount is applied.
    Update via PATCH /admin/billing-rates — no redeploy needed.
    """

    __tablename__ = "platform_config"

    id: int = Field(default=1, primary_key=True)
    managed_cost_multiplier: float = Field(default=3.0)
    byok_service_multiplier: float = Field(default=0.4)
    proxy_service_multiplier: float = Field(default=0.7)
    updated_at: datetime = Field(default_factory=_utcnow)


class Campaign(SQLModel, table=True):
    """Time-limited cost multiplier campaign.

    target:
      "all" — applies to every non-locked workspace active during the campaign window.
      "new" — applies only to workspaces created after starts_at.

    Locked workspaces (multiplier_locked=True) are always exempt.
    """

    __tablename__ = "campaign"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    multiplier: float
    starts_at: datetime
    ends_at: datetime
    target: str = Field(default="all")  # all | new
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)
