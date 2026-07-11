"""Lemon Squeezy (MoR) webhook receiver.

Verifies the X-Signature header (HMAC-SHA256 of the raw body with
LEMON_SQUEEZY_WEBHOOK_SECRET) and maps Lemon Squeezy subscription events
to the neutral subscription_status field on Workspace.

Lemon Squeezy status values and their mapping:
  on_trial / trialing → trialing
  active              → active
  paused              → paused   (not in BLOCKED_STATUSES — access continues)
  past_due / unpaid   → unpaid   (BLOCKED)
  cancelled / expired → canceled (BLOCKED)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.models.run import Workspace

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mor", tags=["billing-mor"])

# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

_LS_STATUS_MAP: dict[str, str] = {
    "on_trial":  "trialing",
    "trialing":  "trialing",
    "active":    "active",
    "paused":    "paused",
    "past_due":  "unpaid",
    "unpaid":    "unpaid",
    "cancelled": "canceled",
    "canceled":  "canceled",
    "expired":   "canceled",
}

# Events we care about; others are acknowledged but ignored
_HANDLED_EVENTS = frozenset({
    "subscription_created",
    "subscription_updated",
    "subscription_cancelled",
    "subscription_payment_failed",
    "subscription_expired",
})


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_signature(body: bytes, signature: str) -> bool:
    secret = os.environ.get("LEMON_SQUEEZY_WEBHOOK_SECRET", "")
    if not secret:
        return True  # dev mode: skip verification (warn logged in startup guard)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@router.post("/webhook", include_in_schema=False)
async def mor_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Receive and process Lemon Squeezy subscription webhook events."""
    body = await request.body()
    signature = request.headers.get("X-Signature", "")

    if not _verify_signature(body, signature):
        log.warning("mor: invalid webhook signature")
        raise HTTPException(400, "Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    meta = payload.get("meta", {})
    event_name: str = meta.get("event_name", "")

    if event_name not in _HANDLED_EVENTS:
        log.debug("mor: ignoring event %r", event_name)
        return {"received": True}

    data = payload.get("data", {})
    attrs = data.get("attributes", {})
    ls_subscription_id: str = str(data.get("id", ""))
    ls_customer_id: str = str(attrs.get("customer_id", ""))
    ls_status: str = attrs.get("status", "")
    new_status = _LS_STATUS_MAP.get(ls_status, ls_status)

    # custom_data.workspace_id is set when we create the LS checkout link
    custom_data: dict = meta.get("custom_data") or {}
    workspace_id_hint: str = str(custom_data.get("workspace_id", ""))

    ws = await _resolve_workspace(session, ls_subscription_id, ls_customer_id, workspace_id_hint)
    if ws is None:
        log.warning(
            "mor: no workspace found for subscription %s / customer %s / hint %s",
            ls_subscription_id, ls_customer_id, workspace_id_hint,
        )
        # Return 200 so LS doesn't retry; we can't do anything without a matching workspace
        return {"received": True}

    # Persist provider identifiers on first contact
    if not ws.mor_subscription_id and ls_subscription_id:
        ws.mor_subscription_id = ls_subscription_id
    if not ws.mor_customer_id and ls_customer_id:
        ws.mor_customer_id = ls_customer_id

    ws.billing_provider = "mor"
    ws.subscription_status = new_status
    session.add(ws)
    await session.commit()

    log.info(
        "mor: workspace %d (%s) subscription event=%r ls_status=%r → status=%r",
        ws.id, ws.slug, event_name, ls_status, new_status,
    )
    return {"received": True}


async def _resolve_workspace(
    session: AsyncSession,
    ls_subscription_id: str,
    ls_customer_id: str,
    workspace_id_hint: str,
) -> Workspace | None:
    """Resolve the workspace for a Lemon Squeezy event.

    Lookup order:
    1. By mor_subscription_id (most specific — set after first webhook)
    2. By mor_customer_id (set after first webhook)
    3. By custom_data.workspace_id (set during checkout, used for subscription_created)
    """
    if ls_subscription_id:
        ws = (await session.exec(
            select(Workspace).where(Workspace.mor_subscription_id == ls_subscription_id)
        )).first()
        if ws:
            return ws

    if ls_customer_id:
        ws = (await session.exec(
            select(Workspace).where(Workspace.mor_customer_id == ls_customer_id)
        )).first()
        if ws:
            return ws

    if workspace_id_hint and workspace_id_hint.isdigit():
        ws = (await session.exec(
            select(Workspace).where(Workspace.id == int(workspace_id_hint))
        )).first()
        return ws

    return None
