"""Webhook delivery retry service.

Delivers WebhookDelivery rows immediately when created (event-driven) and retries
failed attempts with exponential backoff (up to 5 attempts, then marks as failed).

Usage:
    # Trigger immediate delivery after inserting a row (from listener.py):
    notify_new_delivery()

    # Started at app startup in lifespan:
    asyncio.create_task(start_webhook_retry_loop())
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
from app.models.run import WebhookDelivery

log = logging.getLogger(__name__)

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
        _main_loop.call_soon_threadsafe(_wakeup.set)


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
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        r = await client.post(
                            delivery.url,
                            json=json.loads(delivery.payload_json),
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
                    else:
                        delivery.status = "retrying"
                        delay = 2 ** delivery.attempts  # 2, 4, 8, 16 seconds
                        delivery.next_retry_at = _utcnow() + timedelta(seconds=delay)

                session.add(delivery)

            if deliveries:
                await session.commit()
    except Exception as exc:
        log.warning("webhook: retry loop error: %s", exc)


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
