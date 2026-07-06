"""Tests for the event bus listener — cancel guard, HITL webhook push."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from sqlmodel import select

from app.models.run import Run, HitlReview, WebhookDelivery


class _FakeEvent:
    """Minimal stand-in for antcrew.core.events.Event."""
    def __init__(self, type, run_id, payload=None, thread_id="default", timestamp=1_700_000_000.0):
        self.type = type
        self.run_id = run_id
        self.thread_id = thread_id
        self.payload = payload or {}
        self.timestamp = timestamp


def _session_cm(session):
    """Return an async context manager that yields the given session."""
    class _CM:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return session
        async def __aexit__(self, *a): pass
    return _CM


# ---------------------------------------------------------------------------
# Fix 1: pipeline.end must not overwrite a cancelled run's status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_end_does_not_overwrite_cancelled(session):
    """When pipeline.end fires after cancel_run, the run stays 'cancelled'."""
    import app.core.listener as _lis

    run = Run(run_id="r-canc-guard", team="DevTeam", request="x", status="cancelled", cost_usd=0.0)
    session.add(run)
    await session.commit()

    evt = _FakeEvent("pipeline.end", "r-canc-guard", {"success": True, "cost_usd": 0.15})

    with patch("app.core.listener.AsyncSession", _session_cm(session)):
        await _lis._persist_event(evt)

    await session.refresh(run)
    assert run.status == "cancelled"   # not overwritten to "success"
    assert run.cost_usd == 0.0         # cost not updated either


@pytest.mark.asyncio
async def test_pipeline_end_updates_non_cancelled_run(session):
    """pipeline.end normally transitions a running run to success."""
    import app.core.listener as _lis

    run = Run(run_id="r-normal-end", team="DevTeam", request="x", status="running", cost_usd=0.0)
    session.add(run)
    await session.commit()

    evt = _FakeEvent("pipeline.end", "r-normal-end", {"success": True, "cost_usd": 0.20})

    with patch("app.core.listener.AsyncSession", _session_cm(session)):
        await _lis._persist_event(evt)

    await session.refresh(run)
    assert run.status == "success"
    assert abs(run.cost_usd - 0.20) < 0.001


# ---------------------------------------------------------------------------
# Fix 2: hitl.review_required creates a WebhookDelivery when HITL_WEBHOOK_URL set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hitl_webhook_delivery_created(session, monkeypatch):
    """hitl.review_required enqueues a WebhookDelivery when HITL_WEBHOOK_URL is set."""
    import app.core.listener as _lis

    monkeypatch.setenv("HITL_WEBHOOK_URL", "https://hooks.example.com/hitl")

    run = Run(run_id="r-hitl-wh", team="DevTeam", request="x", status="running")
    session.add(run)
    await session.commit()

    evt = _FakeEvent("hitl.review_required", "r-hitl-wh", {
        "review_id": "rev-wh-001",
        "agent_name": "pm_agent",
        "artifact": {"title": "PRD v1"},
        "options": ["approve", "reject", "feedback"],
    })

    with patch("app.core.listener.AsyncSession", _session_cm(session)):
        await _lis._persist_event(evt)

    result = await session.exec(
        select(WebhookDelivery).where(WebhookDelivery.run_id == "r-hitl-wh")
    )
    deliveries = result.all()
    assert len(deliveries) == 1
    payload = json.loads(deliveries[0].payload_json)
    assert payload["event"] == "hitl.review_required"
    assert payload["review_id"] == "rev-wh-001"
    assert payload["agent_name"] == "pm_agent"
    assert deliveries[0].url == "https://hooks.example.com/hitl"


@pytest.mark.asyncio
async def test_hitl_no_webhook_when_url_not_set(session):
    """hitl.review_required does NOT enqueue a WebhookDelivery when HITL_WEBHOOK_URL is unset."""
    import app.core.listener as _lis

    os.environ.pop("HITL_WEBHOOK_URL", None)

    run = Run(run_id="r-hitl-nowh", team="DevTeam", request="x", status="running")
    session.add(run)
    await session.commit()

    evt = _FakeEvent("hitl.review_required", "r-hitl-nowh", {
        "review_id": "rev-nowh-001",
        "agent_name": "pm_agent",
        "artifact": {},
        "options": ["approve", "reject"],
    })

    with patch("app.core.listener.AsyncSession", _session_cm(session)):
        await _lis._persist_event(evt)

    result = await session.exec(
        select(WebhookDelivery).where(WebhookDelivery.run_id == "r-hitl-nowh")
    )
    assert result.all() == []


@pytest.mark.asyncio
async def test_pipeline_end_webhook_created(session, monkeypatch):
    """pipeline.end enqueues a WebhookDelivery when WEBHOOK_URL is set."""
    import app.core.listener as _lis

    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example.com/pipeline")

    run = Run(run_id="r-wh-end", team="DevTeam", request="x", status="running")
    session.add(run)
    await session.commit()

    evt = _FakeEvent("pipeline.end", "r-wh-end", {"success": True, "cost_usd": 0.05})

    with patch("app.core.listener.AsyncSession", _session_cm(session)):
        await _lis._persist_event(evt)

    result = await session.exec(
        select(WebhookDelivery).where(WebhookDelivery.run_id == "r-wh-end")
    )
    deliveries = result.all()
    assert len(deliveries) == 1
    payload = json.loads(deliveries[0].payload_json)
    assert payload["status"] == "success"


# ---------------------------------------------------------------------------
# Webhook wakeup: notify_new_delivery() triggers immediate processing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notify_new_delivery_sets_event():
    """notify_new_delivery() sets the _wakeup asyncio.Event on the running loop."""
    import asyncio
    import app.services.webhook as _wh

    # Simulate the retry loop having initialised _wakeup on this event loop
    _wh._main_loop = asyncio.get_running_loop()
    _wh._wakeup = asyncio.Event()

    assert not _wh._wakeup.is_set()
    _wh.notify_new_delivery()
    # call_soon_threadsafe schedules it; give the loop a tick to run
    await asyncio.sleep(0)
    assert _wh._wakeup.is_set()


@pytest.mark.asyncio
async def test_retry_loop_wakes_on_delivery(session, monkeypatch):
    """start_webhook_retry_loop processes a new delivery immediately, not after 30s."""
    import asyncio
    import app.services.webhook as _wh

    delivered: list[str] = []

    async def _fake_process():
        delivered.append("processed")

    # Patch _process_pending so we don't need real httpx calls
    monkeypatch.setattr(_wh, "_process_pending", _fake_process)

    loop_task = asyncio.create_task(_wh.start_webhook_retry_loop())
    await asyncio.sleep(0.05)  # let the loop initialise _wakeup

    _wh.notify_new_delivery()
    await asyncio.sleep(0.05)  # let it process

    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    assert "processed" in delivered
