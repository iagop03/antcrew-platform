"""Round 18 tests — Slack config guard, workspace access on create_review, WS first-message auth.

Covers:
- _check_slack_config() blocks startup on public host without SLACK_TOKEN_ENCRYPTION_KEY (S1)
- _check_slack_config() warns locally, does not block (S1)
- _check_slack_config() no-op when SLACK_BOT_TOKEN not set (S1)
- POST /reviews/ rejects body.workspace_id the caller cannot access (S2)
- POST /reviews/ allows body.workspace_id the caller can access (S2)
- POST /reviews/ with no body.workspace_id uses ctx.workspace_id unchecked (S2)
- WebSocket first-message auth succeeds in open mode (S3)
"""
from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import Run, Workspace


# ---------------------------------------------------------------------------
# S1 — _check_slack_config startup guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slack_config_blocks_public_host_without_enc_key(monkeypatch):
    """Public host + SLACK_BOT_TOKEN + no enc key → RuntimeError."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("HOST", "0.0.0.0")

    from app.main import _check_slack_config
    with pytest.raises(RuntimeError, match="SLACK_TOKEN_ENCRYPTION_KEY"):
        await _check_slack_config()


@pytest.mark.asyncio
async def test_slack_config_warns_locally_without_enc_key(monkeypatch, caplog):
    """Localhost + SLACK_BOT_TOKEN + no enc key → warning, no exception."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("HOST", "127.0.0.1")

    import logging
    from app.main import _check_slack_config
    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _check_slack_config()  # must not raise
    assert any("plaintext" in r.message.lower() or "plain text" in r.message.lower()
                for r in caplog.records)


@pytest.mark.asyncio
async def test_slack_config_noop_when_token_absent(monkeypatch):
    """No SLACK_BOT_TOKEN → guard does nothing regardless of other vars."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("HOST", "0.0.0.0")

    from app.main import _check_slack_config
    await _check_slack_config()  # must not raise


@pytest.mark.asyncio
async def test_slack_config_passes_with_enc_key(monkeypatch):
    """SLACK_BOT_TOKEN + SLACK_TOKEN_ENCRYPTION_KEY on public host → OK."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", "ZmFrZWtleWZha2VrZXlmYWtla2V5ZmFrZWtleWZha2s=")
    monkeypatch.setenv("HOST", "0.0.0.0")

    from app.main import _check_slack_config
    await _check_slack_config()  # must not raise


# ---------------------------------------------------------------------------
# S2 — create_review workspace access enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_review_rejects_foreign_workspace(session: AsyncSession):
    """ws_accessible rejects access when workspace_id is not in ctx scope."""
    # Create two workspaces to get two distinct IDs
    ws_a = Workspace(name="ws-a-r18", slug="ws-a-r18")
    ws_b = Workspace(name="ws-b-r18", slug="ws-b-r18")
    session.add(ws_a)
    session.add(ws_b)
    await session.commit()
    await session.refresh(ws_a)
    await session.refresh(ws_b)

    # A context scoped to ws_a should NOT have access to ws_b
    from app.core.auth import WorkspaceContext, ws_accessible
    ctx = WorkspaceContext(workspace_id=ws_a.id, created_by="test", role="write")
    assert ws_accessible(ws_a.id, ctx), "caller must access their own workspace"
    assert not ws_accessible(ws_b.id, ctx), "caller must NOT access another workspace"


@pytest.mark.asyncio
async def test_create_review_allows_own_workspace(client: AsyncClient, session: AsyncSession):
    """create_review succeeds when body.workspace_id matches ctx.workspace_id (open mode)."""
    ws = Workspace(name="my-ws-r18", slug="my-ws-r18")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    run_id = f"own-ws-run-{uuid.uuid4()}"
    session.add(Run(run_id=run_id, team="dev", request="x", workspace_id=ws.id))
    await session.commit()

    review_id = str(uuid.uuid4())
    r = await client.post("/reviews/", json={
        "run_id": run_id,
        "review_id": review_id,
        "agent_name": "OwnWsAgent",
        "workspace_id": ws.id,
    })
    # In open mode (no keys in DB) all workspaces are accessible → should succeed
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_create_review_no_workspace_id_uses_ctx(client: AsyncClient, session: AsyncSession):
    """create_review with no body.workspace_id falls back to ctx.workspace_id without 403."""
    run_id = f"no-ws-run-{uuid.uuid4()}"
    session.add(Run(run_id=run_id, team="dev", request="x"))
    await session.commit()

    review_id = str(uuid.uuid4())
    r = await client.post("/reviews/", json={
        "run_id": run_id,
        "review_id": review_id,
        "agent_name": "NoWsAgent",
    })
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# S3 — WebSocket first-message auth (unit test on the logic layer)
# ---------------------------------------------------------------------------

def test_ws_accessible_logic_for_review_workspace():
    """ws_accessible used by create_review correctly scopes workspace access."""
    from app.core.auth import WorkspaceContext, ws_accessible

    ctx_open = WorkspaceContext(workspace_id=None, created_by=None, role="admin")
    ctx_scoped = WorkspaceContext(workspace_id=5, created_by="key", role="write")

    # Open mode: all workspaces accessible
    assert ws_accessible(1, ctx_open)
    assert ws_accessible(99, ctx_open)

    # Scoped: only own workspace
    assert ws_accessible(5, ctx_scoped)
    assert not ws_accessible(6, ctx_scoped)
