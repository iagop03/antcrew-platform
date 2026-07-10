"""Stripe billing webhook receiver and workspace billing admin endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, require_role
from app.core.database import get_session
from app.models.run import Workspace
from app.services import billing as _billing

log = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Stripe webhook — no API key required (signature-verified)
# ---------------------------------------------------------------------------

@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Receive Stripe webhook events.

    Verifies the ``stripe-signature`` header against ``STRIPE_WEBHOOK_SECRET``.
    In development (secret not set), verification is skipped with a warning.
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    s = _billing._stripe()

    if s and webhook_secret:
        try:
            event = s.Webhook.construct_event(payload, sig, webhook_secret)
        except Exception as exc:
            log.warning("billing: invalid Stripe webhook signature: %s", exc)
            raise HTTPException(400, "Invalid webhook signature")
        event_type: str = event["type"]
        event_data: dict = event["data"]
    elif s and not webhook_secret:
        # Stripe is configured but no webhook secret — reject rather than accept unverified events.
        # An attacker who knows this endpoint can forge any subscription event without a secret.
        log.error(
            "billing: STRIPE_WEBHOOK_SECRET not set — rejecting webhook to prevent "
            "unverified event processing. Set STRIPE_WEBHOOK_SECRET to enable webhooks."
        )
        raise HTTPException(
            403,
            "Webhook signature verification is not configured. "
            "Set STRIPE_WEBHOOK_SECRET on the server to enable Stripe webhook processing.",
        )
    else:
        # Stripe not configured at all (no STRIPE_SECRET_KEY): dev/test mode.
        # Accept raw JSON so local integration tests can drive the billing state machine.
        try:
            body = json.loads(payload)
        except Exception:
            raise HTTPException(400, "Invalid JSON payload")
        event_type = body.get("type", "")
        event_data = body.get("data", {})

    result = _billing.handle_subscription_event(event_type, event_data)
    if result is not None:
        customer_id, new_status = result
        await _apply_subscription_status(session, customer_id, new_status)
        log.info(
            "billing: webhook %s → customer %s status=%s",
            event_type, customer_id, new_status,
        )

    return {"received": True}


async def _apply_subscription_status(
    session: AsyncSession,
    stripe_customer_id: str,
    new_status: str,
) -> None:
    """Update workspace.stripe_subscription_status for the matching customer."""
    ws = (await session.exec(
        select(Workspace).where(Workspace.stripe_customer_id == stripe_customer_id)
    )).first()
    if ws is None:
        log.debug(
            "billing: no workspace found for customer %s — ignoring event",
            stripe_customer_id,
        )
        return
    ws.stripe_subscription_status = new_status
    session.add(ws)
    await session.commit()
    log.info(
        "billing: workspace %d (%s) subscription status → %s",
        ws.id, ws.slug, new_status,
    )


# ---------------------------------------------------------------------------
# Admin: link / view workspace billing
# ---------------------------------------------------------------------------

class AttachBilling(BaseModel):
    stripe_customer_id: str
    stripe_subscription_id: Optional[str] = None
    stripe_subscription_status: Optional[str] = "active"


class BillingOut(BaseModel):
    workspace_id: int
    slug: str
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    stripe_subscription_status: Optional[str]
    total_cost_usd: float
    max_cost_usd: Optional[float]


@router.get(
    "/workspaces/{workspace_id}",
    response_model=BillingOut,
    dependencies=[Depends(require_api_key)],
)
async def get_workspace_billing(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
) -> BillingOut:
    """Return billing details for a workspace."""
    ws = (await session.exec(
        select(Workspace).where(Workspace.id == workspace_id)
    )).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    return BillingOut(
        workspace_id=ws.id,  # type: ignore[arg-type]
        slug=ws.slug,
        stripe_customer_id=ws.stripe_customer_id,
        stripe_subscription_id=ws.stripe_subscription_id,
        stripe_subscription_status=ws.stripe_subscription_status,
        total_cost_usd=ws.total_cost_usd,
        max_cost_usd=ws.max_cost_usd,
    )


@router.post(
    "/workspaces/{workspace_id}/attach",
    response_model=BillingOut,
    dependencies=[Depends(require_api_key), Depends(require_role("admin"))],
)
async def attach_stripe(
    workspace_id: int,
    body: AttachBilling,
    session: AsyncSession = Depends(get_session),
) -> BillingOut:
    """Link a workspace to an existing Stripe Customer and (optionally) subscription.

    Call this after creating the Customer in Stripe (via dashboard or API).
    To auto-create a Customer, omit the body and use POST /billing/workspaces/{id}/create-customer.
    """
    ws = (await session.exec(
        select(Workspace).where(Workspace.id == workspace_id)
    )).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    ws.stripe_customer_id = body.stripe_customer_id
    if body.stripe_subscription_id is not None:
        ws.stripe_subscription_id = body.stripe_subscription_id
    if body.stripe_subscription_status is not None:
        ws.stripe_subscription_status = body.stripe_subscription_status
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    log.info(
        "billing: workspace %d (%s) linked to Stripe customer %s",
        ws.id, ws.slug, ws.stripe_customer_id,
    )
    return BillingOut(
        workspace_id=ws.id,  # type: ignore[arg-type]
        slug=ws.slug,
        stripe_customer_id=ws.stripe_customer_id,
        stripe_subscription_id=ws.stripe_subscription_id,
        stripe_subscription_status=ws.stripe_subscription_status,
        total_cost_usd=ws.total_cost_usd,
        max_cost_usd=ws.max_cost_usd,
    )


@router.post(
    "/workspaces/{workspace_id}/create-customer",
    response_model=BillingOut,
    dependencies=[Depends(require_api_key), Depends(require_role("admin"))],
)
async def create_stripe_customer(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
) -> BillingOut:
    """Auto-create a Stripe Customer for this workspace and link it.

    Requires STRIPE_SECRET_KEY to be set.  Returns 422 if Stripe is not configured
    or if the workspace already has a customer ID.
    """
    ws = (await session.exec(
        select(Workspace).where(Workspace.id == workspace_id)
    )).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    if ws.stripe_customer_id:
        raise HTTPException(
            422,
            f"Workspace already linked to Stripe customer {ws.stripe_customer_id}. "
            "Use PATCH /billing/workspaces/{id}/attach to update.",
        )
    customer_id = _billing.get_or_create_customer(ws.name, ws.slug)
    if customer_id is None:
        raise HTTPException(
            503,
            "Stripe is not configured (STRIPE_SECRET_KEY missing or stripe package not installed). "
            "Install stripe and set STRIPE_SECRET_KEY, or use /attach to manually link a customer.",
        )
    ws.stripe_customer_id = customer_id
    ws.stripe_subscription_status = None  # no subscription yet
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    log.info(
        "billing: created Stripe customer %s for workspace %d (%s)",
        customer_id, ws.id, ws.slug,
    )
    return BillingOut(
        workspace_id=ws.id,  # type: ignore[arg-type]
        slug=ws.slug,
        stripe_customer_id=ws.stripe_customer_id,
        stripe_subscription_id=ws.stripe_subscription_id,
        stripe_subscription_status=ws.stripe_subscription_status,
        total_cost_usd=ws.total_cost_usd,
        max_cost_usd=ws.max_cost_usd,
    )


@router.delete(
    "/workspaces/{workspace_id}/detach",
    status_code=204,
    dependencies=[Depends(require_api_key), Depends(require_role("admin"))],
)
async def detach_stripe(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove Stripe billing linkage from a workspace (does NOT cancel in Stripe)."""
    ws = (await session.exec(
        select(Workspace).where(Workspace.id == workspace_id)
    )).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    ws.stripe_customer_id = None
    ws.stripe_subscription_id = None
    ws.stripe_subscription_status = None
    session.add(ws)
    await session.commit()


class CheckoutOut(BaseModel):
    checkout_url: str


@router.post(
    "/workspaces/{workspace_id}/checkout",
    response_model=CheckoutOut,
    dependencies=[Depends(require_api_key), Depends(require_role("admin"))],
)
async def create_checkout_session(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
) -> CheckoutOut:
    """Create a Stripe Checkout Session and return the hosted payment URL.

    Required env vars: STRIPE_SECRET_KEY, STRIPE_PRICE_ID, STRIPE_SUCCESS_URL, STRIPE_CANCEL_URL.
    """
    s = _billing._stripe()
    if not s:
        raise HTTPException(
            503,
            "Stripe is not configured. Set STRIPE_SECRET_KEY and install the stripe package.",
        )

    price_id = os.environ.get("STRIPE_PRICE_ID")
    success_url = os.environ.get("STRIPE_SUCCESS_URL")
    cancel_url = os.environ.get("STRIPE_CANCEL_URL")
    if not price_id or not success_url or not cancel_url:
        raise HTTPException(
            503,
            "Checkout requires STRIPE_PRICE_ID, STRIPE_SUCCESS_URL, and STRIPE_CANCEL_URL env vars.",
        )

    ws = (await session.exec(
        select(Workspace).where(Workspace.id == workspace_id)
    )).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")

    # Ensure there's a Stripe Customer to attach the subscription to
    if not ws.stripe_customer_id:
        customer_id = _billing.get_or_create_customer(ws.name, ws.slug)
        if customer_id:
            ws.stripe_customer_id = customer_id
            session.add(ws)
            await session.commit()

    try:
        loop = asyncio.get_running_loop()
        checkout = await loop.run_in_executor(
            None,
            lambda: s.checkout.Session.create(
                mode="subscription",
                customer=ws.stripe_customer_id or None,
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={"workspace_id": str(workspace_id), "slug": ws.slug},
            ),
        )
    except Exception as exc:
        log.error("billing: checkout session creation failed for ws %d: %s", workspace_id, exc)
        raise HTTPException(502, f"Stripe error: {exc}")

    return CheckoutOut(checkout_url=checkout.url)
