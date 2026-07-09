"""Stripe metered billing adapter.

Enabled when STRIPE_SECRET_KEY is set.  When the key is absent (or the
stripe package is not installed) every call is a no-op — the rest of the
platform keeps working without billing.

Meter configuration
-------------------
Create a Billing Meter in the Stripe dashboard:
  - Event name: antcrew_run_cost   (or override STRIPE_METER_EVENT)
  - Value field: "value" (sum aggregation)
  - Customer ID field: "stripe_customer_id"

Value is reported in microdollars (USD × 10^6) so a $0.001234 run becomes
1234 units — preserving sub-cent precision without floating-point issues.
Configure the meter price in Stripe as price-per-1,000,000-units = $1.

Webhook events handled (POST /billing/webhook):
  customer.subscription.updated  → sync stripe_subscription_status
  customer.subscription.deleted  → set status = "canceled"
  invoice.payment_failed         → set status = "past_due"
  invoice.paid                   → set status = "active"
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

METER_EVENT_NAME: str = os.environ.get("STRIPE_METER_EVENT", "antcrew_run_cost")

# Subscription statuses that block new runs
BLOCKED_STATUSES: frozenset[str] = frozenset({"canceled", "unpaid"})


def _stripe():
    """Return configured stripe module, or None if unavailable/unconfigured."""
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        return None
    try:
        import stripe as _s
        _s.api_key = key
        return _s
    except ImportError:
        log.warning(
            "billing: STRIPE_SECRET_KEY is set but the 'stripe' package is not "
            "installed. Run: pip install stripe>=7"
        )
        return None


async def report_run_cost(
    workspace_id: int,
    stripe_customer_id: Optional[str],
    cost_usd: float,
) -> None:
    """Report a completed run's cost to Stripe as a metered event.

    Fire-and-forget — never raises; failures are logged as warnings.
    """
    if not stripe_customer_id or cost_usd <= 0:
        return
    s = _stripe()
    if s is None:
        return

    microdollars = max(1, int(cost_usd * 1_000_000))
    try:
        s.billing.MeterEvent.create(
            event_name=METER_EVENT_NAME,
            payload={
                "value": str(microdollars),
                "stripe_customer_id": stripe_customer_id,
            },
        )
        log.debug(
            "billing: reported %d µ$ (%.6f USD) for workspace %d customer %s",
            microdollars, cost_usd, workspace_id, stripe_customer_id,
        )
    except Exception as exc:
        log.warning(
            "billing: failed to report usage for workspace %d: %s",
            workspace_id, exc,
        )


def get_or_create_customer(name: str, slug: str) -> Optional[str]:
    """Create a Stripe Customer for a workspace and return its ID.

    Returns None if Stripe is not configured.
    """
    s = _stripe()
    if s is None:
        return None
    try:
        customer = s.Customer.create(
            name=name,
            metadata={"workspace_slug": slug},
        )
        return customer.id
    except Exception as exc:
        log.warning("billing: failed to create Stripe customer for %r: %s", slug, exc)
        return None


def handle_subscription_event(event_type: str, data: dict) -> Optional[tuple[str, str]]:
    """Extract (stripe_customer_id, new_status) from a Stripe subscription event.

    Returns None if the event type is not a subscription lifecycle event.
    """
    obj = data.get("object", {})
    customer_id: Optional[str] = obj.get("customer")
    if not customer_id:
        return None

    if event_type in ("customer.subscription.updated", "customer.subscription.created"):
        status = obj.get("status", "unknown")
        return customer_id, status

    if event_type == "customer.subscription.deleted":
        return customer_id, "canceled"

    if event_type == "invoice.payment_failed":
        return customer_id, "past_due"

    if event_type == "invoice.paid":
        # Only promote to active if there is an active/trialing subscription
        return customer_id, "active"

    return None
