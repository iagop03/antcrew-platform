"""Shared helper for querying the active free-trial campaign."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.admin import Campaign


async def get_active_free_promo(session) -> "Optional[Campaign]":
    """Return the current active campaign with multiplier=0, or None.

    A free-trial promo is defined as a Campaign where:
    - active=True
    - multiplier=0
    - starts_at <= now <= ends_at
    """
    from sqlmodel import select
    from app.models.admin import Campaign

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (await session.exec(
        select(Campaign)
        .where(Campaign.active.is_(True))
        .where(Campaign.multiplier == 0.0)
        .where(Campaign.starts_at <= now)
        .where(Campaign.ends_at >= now)
        .order_by(Campaign.id.desc())
        .limit(1)
    )).first()
