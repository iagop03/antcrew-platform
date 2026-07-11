"""Webhook delivery retry service.

Delivers WebhookDelivery rows immediately when created (event-driven) and retries
failed attempts with exponential backoff (up to 5 attempts, then marks as failed).

Usage:
    # Trigger immediate delivery after inserting a row (from listener.py):
    notify_new_delivery()

    # Started at app startup in lifespan:
    asyncio.create_task(start_webhook_retry_loop())

Alerts:
    Set ALERT_WEBHOOK_URL to receive a POST notification whenever a delivery
    permanently fails (all 5 attempts exhausted).  Works with Slack incoming
    webhooks, Discord webhooks, or any HTTP endpoint that accepts JSON.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
from app.models.run import WebhookConfig, WebhookDelivery, WebhookEvent

_SIGNING_SECRET: Optional[str] = os.environ.get("WEBHOOK_SIGNING_SECRET") or None


def _sign_payload(body: str) -> Optional[str]:
    """Return ``sha256=<hex>`` HMAC of *body* using WEBHOOK_SIGNING_SECRET, or None."""
    if not _SIGNING_SECRET:
        return None
    sig = hmac.new(_SIGNING_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"sha256={sig}"

log = logging.getLogger(__name__)

_ALERT_URL: Optional[str] = os.environ.get("ALERT_WEBHOOK_URL") or None


async def _send_alert(delivery: WebhookDelivery) -> None:
    """POST a failure summary to ALERT_WEBHOOK_URL (fire-and-forget, never raises)."""
    if not _ALERT_URL:
        return
    payload = {
        "text": (
            f":x: Webhook delivery #{delivery.id} permanently failed after "
            f"{delivery.attempts} attempts.\n"
            f"URL: {delivery.url}\n"
            f"Last error: {delivery.last_error or 'unknown'}"
        ),
        "delivery_id": delivery.id,
        "url": delivery.url,
        "attempts": delivery.attempts,
        "last_error": delivery.last_error,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(_ALERT_URL, json=payload)
        log.debug("webhook: sent failure alert for delivery %d", delivery.id)
    except Exception as exc:
        log.warning("webhook: failed to send alert for delivery %d: %s", delivery.id, exc)

_POLL_INTERVAL = 30   # fallback poll for retries even when no new deliveries arrive
_MAX_ATTEMPTS = 5

# Set by the running event loop at startup; used for thread-safe wakeups from listener.
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_wakeup: Optional[asyncio.Event] = None


def notify_new_delivery() -> None:
    """Wake the retry loop immediately to process a newly created WebhookDelivery.

    Safe to call from any thread (listener uses the antcrew sync handler).
    """
    if _main_loop is not None and _wakeup is not None:
        try:
            _main_loop.call_soon_threadsafe(_wakeup.set)
        except RuntimeError:
            pass  # loop already closed (test teardown or shutdown race)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _process_pending() -> None:
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            now = _utcnow()
            result = await session.exec(
                select(WebhookDelivery).where(
                    WebhookDelivery.status.in_(["pending", "retrying"]),
                    WebhookDelivery.next_retry_at <= now,
                )
            )
            deliveries = list(result.all())

            for delivery in deliveries:
                try:
                    headers = {"Content-Type": "application/json"}
                    sig = _sign_payload(delivery.payload_json)
                    if sig:
                        headers["X-Antcrew-Signature"] = sig
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        r = await client.post(
                            delivery.url,
                            content=delivery.payload_json,
                            headers=headers,
                        )
                        r.raise_for_status()
                    delivery.status = "delivered"
                    delivery.attempts += 1
                    log.info("webhook: delivered %d to %s", delivery.id, delivery.url)
                except Exception as exc:
                    delivery.attempts += 1
                    delivery.last_error = str(exc)[:500]
                    if delivery.attempts >= _MAX_ATTEMPTS:
                        delivery.status = "failed"
                        log.warning("webhook: permanently failed delivery %d: %s", delivery.id, exc)
                        await _send_alert(delivery)
                    else:
                        delivery.status = "retrying"
                        delay = 2 ** delivery.attempts  # 2, 4, 8, 16 seconds
                        delivery.next_retry_at = _utcnow() + timedelta(seconds=delay)

                session.add(delivery)

            if deliveries:
                await session.commit()
    except Exception as exc:
        log.warning("webhook: retry loop error: %s", exc)


async def fire_event_webhooks(
    session,
    *,
    workspace_id: Optional[int],
    event_type: str,
    run_id: str,
    payload: dict,
) -> int:
    """Create WebhookDelivery rows for all enabled configs subscribed to *event_type*.

    Returns the number of deliveries queued.  Call notify_new_delivery() after committing
    to wake the retry loop immediately.

    WebhookEvent rows with event_type matching *event_type* or ``"*"`` are eligible.
    """
    if workspace_id is None:
        return 0
    configs = (await session.exec(
        select(WebhookConfig).where(
            WebhookConfig.workspace_id == workspace_id,
            WebhookConfig.enabled == True,  # noqa: E712
        )
    )).all()
    if not configs:
        return 0

    subscribed_ids = (await session.exec(
        select(WebhookEvent.webhook_id).where(
            WebhookEvent.event_type.in_([event_type, "*"])
        )
    )).all()
    subscribed_set = set(subscribed_ids)

    payload_json = json.dumps({"event_type": event_type, **payload})
    count = 0
    for cfg in configs:
        # If no subscription rows exist for this config, treat as "all events" (wildcard)
        if subscribed_set and cfg.id not in subscribed_set:
            continue
        session.add(WebhookDelivery(
            run_id=run_id,
            url=cfg.url,
            payload_json=payload_json,
            status="pending",
        ))
        count += 1

    return count


async def start_webhook_retry_loop() -> None:
    """Long-running background task — deliver and retry pending webhook deliveries.

    Wakes immediately when notify_new_delivery() is called (new row created).
    Falls back to a 30-second poll for retry scheduling even without notifications.
    """
    global _main_loop, _wakeup
    _main_loop = asyncio.get_running_loop()
    _wakeup = asyncio.Event()

    log.info("webhook: retry loop started (interval=%ds, max_attempts=%d)", _POLL_INTERVAL, _MAX_ATTEMPTS)
    while True:
        try:
            await asyncio.wait_for(_wakeup.wait(), timeout=_POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
        _wakeup.clear()
        await _process_pending()
