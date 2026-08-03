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
    managed_enabled: bool = Field(default=True)
    byok_enabled: bool = Field(default=True)
    proxy_enabled: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=_utcnow)


class Campaign(SQLModel, table=True):
    """Time-limited cost multiplier campaign.

    target:
      "all" — applies to every non-locked workspace active during the campaign window.
      "new" — applies only to workspaces created after starts_at.

    Locked workspaces (multiplier_locked=True) are always exempt.

    discount_days:
      When set, each eligible workspace gets a personal N-day discount window
      starting from their first run during the campaign.  Without this field
      the discount applies for the full campaign date range.

    max_participants:
      Cap on how many workspaces can enroll.  Only meaningful when discount_days
      is also set (enrollment-based campaigns).
    """

    __tablename__ = "campaign"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    multiplier: float
    starts_at: datetime
    ends_at: datetime
    target: str = Field(default="all")  # all | new
    active: bool = Field(default=True)
    discount_days: Optional[int] = Field(default=None)
    max_participants: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class CampaignEnrollment(SQLModel, table=True):
    """Per-workspace enrollment record for discount_days campaigns.

    Created on the first run a workspace makes during an active campaign
    that has discount_days set.  The discount is active while
    discount_ends_at > utcnow.
    """

    __tablename__ = "campaign_enrollment"

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    enrolled_at: datetime = Field(default_factory=_utcnow)
    discount_ends_at: datetime
