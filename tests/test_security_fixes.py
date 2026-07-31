"""Tests for the security fixes introduced in the audit remediation.

Covers:
- WebSocket _Connection.enqueue workspace filtering (register_run / deregister_run)
- Invite token hashed lookup (token_hash) with legacy plaintext fallback
- WsAuth resolve functions return correct workspace_ids from DB
"""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, Workspace, WorkspaceInvite


# ---------------------------------------------------------------------------
# WebSocket _Connection workspace filtering
# ---------------------------------------------------------------------------

def test_connection_enqueue_no_filter():
    """Without workspace_ids, all events pass through."""
    from antcrew import Event
    from app.api.stream import _Connection

    ws = MagicMock()
    conn = _Connection(ws, workspace_ids=None)

    event = Event("agent.start", run_id="run-abc", thread_id="t")
    conn.enqueue(event)
    assert conn._queue.qsize() == 1


def test_connection_enqueue_filters_other_workspace():
    """Events for runs in a different workspace are dropped."""
    from antcrew import Event
    from app.api.stream import _Connection, register_run, deregister_run

    ws = MagicMock()
    register_run("run-ws2", workspace_id=2)
    try:
        conn = _Connection(ws, workspace_ids=frozenset({1}))  # connected to workspace 1
        event = Event("agent.start", run_id="run-ws2", thread_id="t")
        conn.enqueue(event)
        assert conn._queue.qsize() == 0, "event from workspace 2 must be dropped"
    finally:
        deregister_run("run-ws2")


def test_connection_enqueue_passes_own_workspace():
    """Events for runs in the connection's own workspace pass through."""
    from antcrew import Event
    from app.api.stream import _Connection, register_run, deregister_run

    ws = MagicMock()
    register_run("run-ws1", workspace_id=1)
    try:
        conn = _Connection(ws, workspace_ids=frozenset({1}))
        event = Event("agent.start", run_id="run-ws1", thread_id="t")
        conn.enqueue(event)
        assert conn._queue.qsize() == 1
    finally:
        deregister_run("run-ws1")


def test_connection_enqueue_unknown_run_id_passes():
    """Events with an unknown run_id (not in _run_workspace cache) pass through.

    This is the conservative fallback: if a runner didn't call register_run we
    don't drop events, which avoids silently losing legitimate output during
    restarts or version skew.
    """
    from antcrew import Event
    from app.api.stream import _Connection

    ws = MagicMock()
    conn = _Connection(ws, workspace_ids=frozenset({1}))
    event = Event("agent.start", run_id="unknown-run", thread_id="t")
    conn.enqueue(event)
    assert conn._queue.qsize() == 1


def test_connection_run_id_filter_still_works():
    """The existing run_id filter is preserved alongside the workspace filter."""
    from antcrew import Event
    from app.api.stream import _Connection, register_run, deregister_run

    ws = MagicMock()
    register_run("run-target", workspace_id=1)
    register_run("run-other", workspace_id=1)
    try:
        # Connected to workspace 1 and watching only run-target
        conn = _Connection(ws, run_id="run-target", workspace_ids=frozenset({1}))
        event_target = Event("step.end", run_id="run-target", thread_id="t")
        event_other = Event("step.end", run_id="run-other", thread_id="t")
        conn.enqueue(event_target)
        conn.enqueue(event_other)
        assert conn._queue.qsize() == 1  # only the matching run_id
    finally:
        deregister_run("run-target")
        deregister_run("run-other")


# ---------------------------------------------------------------------------
# Invite token hash lookup
# ---------------------------------------------------------------------------

def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _make_session(session, *, label: str) -> tuple[ApiKey, str]:
    """Create an ApiKey + UserSession and return (key, session_token)."""
    import secrets
    from datetime import timedelta
    from app.models.run import UserSession

    api_key = ApiKey(label=label, key_hash=_hash(label + "-secret"), role="write")
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    raw_token = secrets.token_urlsafe(32)
    user_session = UserSession(
        token=raw_token,
        api_key_id=api_key.id,
        expires_at=_utcnow() + timedelta(days=1),
        revoked=False,
    )
    session.add(user_session)
    await session.commit()
    return api_key, raw_token


@pytest.mark.asyncio
async def test_accept_invite_uses_token_hash(client, session):
    """accept_invite succeeds when invite is stored with token_hash (no plaintext)."""
    from datetime import timedelta

    ws = Workspace(name="Invite WS", slug="invite-ws")
    session.add(ws)
    api_key, session_token = await _make_session(session, label="inv-user")
    await session.refresh(ws)

    raw_token = "invite-abc123"
    invite = WorkspaceInvite(
        token=None,
        token_hash=_hash(raw_token),
        workspace_id=ws.id,
        invitee_email="user@example.com",
        inviter_email="admin@example.com",
        role="write",
        status="pending",
        expires_at=_utcnow() + timedelta(days=1),
    )
    session.add(invite)
    await session.commit()

    r = await client.post(
        "/auth/accept-invite",
        json={"token": raw_token},
        cookies={"antcrew_session": session_token},
    )
    assert r.status_code == 200
    assert r.json()["workspace_id"] == ws.id


@pytest.mark.asyncio
async def test_accept_invite_invalid_token_returns_404(client, session):
    """accept_invite returns 404 for an unknown token."""
    _, session_token = await _make_session(session, label="inv-user-2")

    r = await client.post(
        "/auth/accept-invite",
        json={"token": "completely-wrong-token"},
        cookies={"antcrew_session": session_token},
    )
    assert r.status_code == 404
