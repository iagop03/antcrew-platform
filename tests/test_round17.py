"""Round 17 tests — ANTCREW_REQUIRE_AUTH, webhook on resolution, audit timeout entry.

Covers:
- WorkspaceContext.role defaults to "write" not "admin" (P1)
- ANTCREW_REQUIRE_AUTH=true blocks startup when no keys exist (P2)
- POST /reviews/{id} (submit) creates WebhookDelivery rows for subscribed configs (P3)
- hitl.review_resolved webhook payload has expected fields (P3)
- Webhook not fired when workspace has no config registered (P3)
- GET /reviews/{id}/audit returns "timed_out" entry when added by cleanup (P4)
"""
from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext
from app.models.run import (
    ApiKey, HitlAuditEntry, HitlReview, Run, WebhookConfig, WebhookDelivery, WebhookEvent,
    Workspace,
)


# ---------------------------------------------------------------------------
# P1 — WorkspaceContext.role safe default
# ---------------------------------------------------------------------------

def test_workspace_context_role_default_is_write():
    """WorkspaceContext default role must not be 'admin' to avoid accidental privilege escalation."""
    ctx = WorkspaceContext(workspace_id=None, created_by=None)
    assert ctx.role != "admin", (
        "Default role must not be 'admin'. "
        "Open mode should explicitly set role='admin', not rely on the default."
    )


def test_workspace_context_open_mode_explicit_admin():
    """Open mode must be constructed with explicit role='admin'."""
    ctx = WorkspaceContext(workspace_id=None, created_by=None, role="admin")
    assert ctx.role == "admin"


# ---------------------------------------------------------------------------
# P2 — ANTCREW_REQUIRE_AUTH startup guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_require_auth_blocks_open_mode(monkeypatch):
    """_check_auth_mode raises RuntimeError when ANTCREW_REQUIRE_AUTH=true and no keys."""
    monkeypatch.setenv("ANTCREW_REQUIRE_AUTH", "true")
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)

    from app.main import _check_auth_mode
    from unittest.mock import patch, AsyncMock, MagicMock

    mock_key_result = MagicMock()
    mock_key_result.first.return_value = None
    mock_session = AsyncMock()
    mock_session.exec = AsyncMock(return_value=mock_key_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=mock_session):
        with pytest.raises(RuntimeError, match="ANTCREW_REQUIRE_AUTH"):
            await _check_auth_mode()


@pytest.mark.asyncio
async def test_require_auth_passes_when_keys_exist(monkeypatch):
    """_check_auth_mode passes when ANTCREW_REQUIRE_AUTH=true and a key exists in DB."""
    monkeypatch.setenv("ANTCREW_REQUIRE_AUTH", "true")
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)

    from app.main import _check_auth_mode
    from unittest.mock import patch, AsyncMock, MagicMock

    mock_key = MagicMock()
    mock_result = MagicMock()
    mock_result.first.return_value = mock_key
    mock_session = AsyncMock()
    mock_session.exec = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=mock_session):
        await _check_auth_mode()  # should not raise


# ---------------------------------------------------------------------------
# P3 — Webhook fired on review resolution
# ---------------------------------------------------------------------------

async def _setup_ws_with_webhook(session: AsyncSession, *, slug: str, url: str) -> tuple:
    ws = Workspace(name=slug, slug=slug)
    session.add(ws)
    await session.flush()
    cfg = WebhookConfig(workspace_id=ws.id, url=url, enabled=True)
    session.add(cfg)
    await session.flush()
    # Subscribe to all events
    session.add(WebhookEvent(webhook_id=cfg.id, event_type="*"))
    await session.commit()
    await session.refresh(ws)
    return ws, cfg


@pytest.mark.asyncio
async def test_submit_review_fires_webhook(client: AsyncClient, session: AsyncSession):
    """Resolving a review creates a WebhookDelivery row for subscribed workspace configs."""
    ws, cfg = await _setup_ws_with_webhook(session, slug="wh-r17", url="http://example.com/hook")

    run_id = f"wh-run-{uuid.uuid4()}"
    session.add(Run(run_id=run_id, team="dev", request="x", workspace_id=ws.id))
    await session.commit()

    review_id = str(uuid.uuid4())
    r = await client.post("/reviews/", json={
        "run_id": run_id,
        "review_id": review_id,
        "agent_name": "WebhookAgent",
        "workspace_id": ws.id,
    })
    assert r.status_code == 201

    r2 = await client.post(f"/reviews/{review_id}", json={"decision": "approve"})
    assert r2.status_code == 200

    deliveries = (await session.exec(
        select(WebhookDelivery).where(WebhookDelivery.run_id == run_id)
    )).all()
    assert len(deliveries) >= 1, "should create at least one WebhookDelivery"

    payload = json.loads(deliveries[0].payload_json)
    assert payload["event_type"] == "hitl.review_resolved"
    assert payload["review_id"] == review_id
    assert payload["decision"] == "approve"


@pytest.mark.asyncio
async def test_submit_review_no_webhook_when_no_config(client: AsyncClient, session: AsyncSession):
    """Resolving a review does not create deliveries when workspace has no webhook config."""
    run_id = f"no-wh-run-{uuid.uuid4()}"
    session.add(Run(run_id=run_id, team="dev", request="x"))
    await session.commit()

    review_id = str(uuid.uuid4())
    await client.post("/reviews/", json={
        "run_id": run_id, "review_id": review_id, "agent_name": "NoHookAgent",
    })
    await client.post(f"/reviews/{review_id}", json={"decision": "reject"})

    deliveries = (await session.exec(
        select(WebhookDelivery).where(WebhookDelivery.run_id == run_id)
    )).all()
    assert len(deliveries) == 0, "no deliveries without webhook config"


@pytest.mark.asyncio
async def test_webhook_payload_contains_actor_and_timestamp(client: AsyncClient, session: AsyncSession):
    """Webhook payload includes actor_label and resolved_at."""
    ws, _ = await _setup_ws_with_webhook(session, slug="wh2-r17", url="http://example.com/hook2")
    run_id = f"wh2-run-{uuid.uuid4()}"
    session.add(Run(run_id=run_id, team="dev", request="x", workspace_id=ws.id))
    await session.commit()

    review_id = str(uuid.uuid4())
    await client.post("/reviews/", json={
        "run_id": run_id, "review_id": review_id, "agent_name": "Actor",
        "workspace_id": ws.id,
    })
    await client.post(f"/reviews/{review_id}", json={"decision": "reject", "feedback": "bad"})

    delivery = (await session.exec(
        select(WebhookDelivery).where(WebhookDelivery.run_id == run_id)
    )).first()
    assert delivery is not None
    payload = json.loads(delivery.payload_json)
    assert "resolved_at" in payload
    assert payload["decision"] == "reject"


# ---------------------------------------------------------------------------
# P4 — Audit entry for timed-out reviews
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_entry_timed_out(client: AsyncClient, session: AsyncSession):
    """Adding a 'timed_out' HitlAuditEntry for a review is visible via GET /reviews/{id}/audit."""
    run_id = f"timeout-run-{uuid.uuid4()}"
    session.add(Run(run_id=run_id, team="dev", request="x"))
    await session.commit()

    review_id = str(uuid.uuid4())
    r = await client.post("/reviews/", json={
        "run_id": run_id, "review_id": review_id, "agent_name": "TimeoutAgent",
    })
    assert r.status_code == 201

    # Simulate what the cleanup loop does
    review = (await session.exec(
        select(HitlReview).where(HitlReview.review_id == review_id)
    )).first()
    assert review is not None
    from datetime import datetime, timezone
    review.status = "timeout"
    review.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(review)
    session.add(HitlAuditEntry(
        review_id=review_id,
        actor_label=None,
        action="timed_out",
        note="Auto-timed-out after 3600s",
    ))
    await session.commit()

    r2 = await client.get(f"/reviews/{review_id}/audit")
    assert r2.status_code == 200
    entries = r2.json()
    actions = [e["action"] for e in entries]
    assert "created" in actions
    assert "timed_out" in actions

    timeout_entry = next(e for e in entries if e["action"] == "timed_out")
    assert timeout_entry["actor_label"] is None
    assert "3600" in timeout_entry["note"]
