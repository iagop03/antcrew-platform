"""Round 12 P3 tests — per-workspace Slack token encryption.

Covers:
- _encrypt / _decrypt roundtrip with a valid Fernet key
- _encrypt / _decrypt dev-mode (no key): plain-text passthrough
- _encrypt raises cleanly on invalid key
- _decrypt falls back gracefully on bad ciphertext
- PATCH /workspaces/{id}/slack-tokens stores encrypted tokens
- DELETE /workspaces/{id}/slack-tokens clears tokens
- Validation: bot_token must start with xoxb-, app_token with xapp-
- start_slack_socket_mode deduplication via _handlers dict
- listener resolves effective tokens (per-workspace > env fallback)
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.models.run import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_workspace(session, *, name, slug):
    ws = Workspace(name=name, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


def _fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# 1. _encrypt / _decrypt
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    key = _fernet_key()
    with patch.dict(os.environ, {"SLACK_TOKEN_ENCRYPTION_KEY": key}):
        from app.core.slack_hitl import _encrypt, _decrypt
        token = "xoxb-test-token-abc123"
        enc = _encrypt(token)
        assert enc != token
        assert _decrypt(enc) == token


def test_encrypt_dev_mode_no_key(monkeypatch):
    """Without encryption key, _encrypt returns the token unchanged (with warning)."""
    monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
    from app.core.slack_hitl import _encrypt
    token = "xoxb-plain-text"
    assert _encrypt(token) == token


def test_decrypt_dev_mode_no_key(monkeypatch):
    """Without encryption key, _decrypt returns the value unchanged."""
    monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
    from app.core.slack_hitl import _decrypt
    assert _decrypt("xoxb-plain") == "xoxb-plain"


def test_encrypt_invalid_key():
    with patch.dict(os.environ, {"SLACK_TOKEN_ENCRYPTION_KEY": "not-a-valid-fernet-key"}):
        from app.core.slack_hitl import _encrypt
        with pytest.raises(RuntimeError, match="encryption failed"):
            _encrypt("xoxb-something")


def test_decrypt_bad_ciphertext_falls_back(monkeypatch):
    """_decrypt on garbage ciphertext returns the input (plain-text fallback)."""
    key = _fernet_key()
    monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", key)
    from app.core.slack_hitl import _decrypt
    assert _decrypt("not-encrypted-at-all") == "not-encrypted-at-all"


def test_encrypt_decrypt_different_keys():
    """Decrypting with a different key returns the ciphertext (graceful fallback)."""
    key1 = _fernet_key()
    key2 = _fernet_key()
    token = "xoxb-secret"
    with patch.dict(os.environ, {"SLACK_TOKEN_ENCRYPTION_KEY": key1}):
        from app.core.slack_hitl import _encrypt
        enc = _encrypt(token)
    with patch.dict(os.environ, {"SLACK_TOKEN_ENCRYPTION_KEY": key2}):
        from app.core.slack_hitl import _decrypt
        result = _decrypt(enc)
    assert result == enc  # fallback: returns ciphertext unchanged


# ---------------------------------------------------------------------------
# 2. PATCH /workspaces/{id}/slack-tokens
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_slack_tokens_stores_encrypted(client: AsyncClient, session, monkeypatch):
    """PATCH /slack-tokens stores an encrypted value, not the raw token."""
    monkeypatch.setenv("SLACK_TOKEN_ENCRYPTION_KEY", _fernet_key())
    ws = await _make_workspace(session, name="Token WS", slug="token-ws-r12")

    r = await client.patch(f"/workspaces/{ws.id}/slack-tokens", json={
        "bot_token": "xoxb-abc-123",
        "app_token": "xapp-def-456",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["slack_bot_configured"] is True
    assert data["slack_app_configured"] is True
    assert data["workspace_id"] == ws.id

    # Verify stored value is not the raw token
    await session.refresh(ws)
    assert ws.slack_bot_token_enc is not None
    assert ws.slack_bot_token_enc != "xoxb-abc-123"
    assert ws.slack_app_token_enc is not None
    assert ws.slack_app_token_enc != "xapp-def-456"


@pytest.mark.asyncio
async def test_set_slack_tokens_no_app_token(client: AsyncClient, session, monkeypatch):
    """app_token is optional."""
    monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
    ws = await _make_workspace(session, name="Bot Only WS", slug="bot-only-ws-r12")

    r = await client.patch(f"/workspaces/{ws.id}/slack-tokens", json={
        "bot_token": "xoxb-bot-only",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["slack_bot_configured"] is True
    assert data["slack_app_configured"] is False


@pytest.mark.asyncio
async def test_set_slack_tokens_invalid_bot_prefix(client: AsyncClient, session):
    ws = await _make_workspace(session, name="Bad Bot WS", slug="bad-bot-ws-r12")
    r = await client.patch(f"/workspaces/{ws.id}/slack-tokens", json={
        "bot_token": "not-a-bot-token",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_slack_tokens_invalid_app_prefix(client: AsyncClient, session):
    ws = await _make_workspace(session, name="Bad App WS", slug="bad-app-ws-r12")
    r = await client.patch(f"/workspaces/{ws.id}/slack-tokens", json={
        "bot_token": "xoxb-valid",
        "app_token": "not-an-app-token",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_set_slack_tokens_not_found(client: AsyncClient, session):
    r = await client.patch("/workspaces/99999/slack-tokens", json={"bot_token": "xoxb-x"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 3. DELETE /workspaces/{id}/slack-tokens
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clear_slack_tokens(client: AsyncClient, session, monkeypatch):
    monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
    ws = Workspace(name="Clear Token WS", slug="clear-token-ws-r12",
                   slack_bot_token_enc="xoxb-old", slack_app_token_enc="xapp-old")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.delete(f"/workspaces/{ws.id}/slack-tokens")
    assert r.status_code == 204

    await session.refresh(ws)
    assert ws.slack_bot_token_enc is None
    assert ws.slack_app_token_enc is None


@pytest.mark.asyncio
async def test_clear_slack_tokens_not_found(client: AsyncClient, session):
    r = await client.delete("/workspaces/99999/slack-tokens")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 4. start_slack_socket_mode deduplication
# ---------------------------------------------------------------------------

def test_socket_mode_deduplication(monkeypatch):
    """start_slack_socket_mode only starts one handler per unique bot token."""
    import app.core.slack_hitl as sh

    # Reset handlers for this test
    original = dict(sh._handlers)
    sh._handlers.clear()

    started = []

    def fake_thread(target, daemon, name):
        t = MagicMock()
        t.start = lambda: started.append(name)
        return t

    mock_bolt_app = MagicMock()
    mock_bolt_instance = MagicMock()
    mock_bolt_app.return_value = mock_bolt_instance

    with patch("threading.Thread", side_effect=fake_thread), \
         patch.dict("sys.modules", {
             "slack_bolt": MagicMock(App=mock_bolt_app),
             "slack_bolt.adapter.socket_mode": MagicMock(SocketModeHandler=MagicMock()),
         }):
        sh.start_slack_socket_mode("xoxb-tok1-abc", "xapp-tok1-xyz")
        sh.start_slack_socket_mode("xoxb-tok1-abc", "xapp-tok1-xyz")  # duplicate — ignored

    assert len(started) == 1

    # Restore
    sh._handlers.clear()
    sh._handlers.update(original)


def test_socket_mode_two_orgs_start_two_handlers(monkeypatch):
    """Different bot tokens get independent handlers."""
    import app.core.slack_hitl as sh

    original = dict(sh._handlers)
    sh._handlers.clear()

    started = []

    def fake_thread(target, daemon, name):
        t = MagicMock()
        t.start = lambda: started.append(name)
        return t

    mock_bolt_app = MagicMock()
    mock_bolt_instance = MagicMock()
    mock_bolt_app.return_value = mock_bolt_instance

    with patch("threading.Thread", side_effect=fake_thread), \
         patch.dict("sys.modules", {
             "slack_bolt": MagicMock(App=mock_bolt_app),
             "slack_bolt.adapter.socket_mode": MagicMock(SocketModeHandler=MagicMock()),
         }):
        sh.start_slack_socket_mode("xoxb-org1-aaa", "xapp-org1-zzz")
        sh.start_slack_socket_mode("xoxb-org2-bbb", "xapp-org2-yyy")

    assert len(started) == 2

    sh._handlers.clear()
    sh._handlers.update(original)


# ---------------------------------------------------------------------------
# 5. listener token resolution order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listener_uses_workspace_token_over_env(monkeypatch):
    """When workspace has slack_bot_token_enc, listener uses it instead of SLACK_BOT_TOKEN."""
    import asyncio
    from app.core import listener as _listener
    from app.models.run import Run, HitlReview

    monkeypatch.delenv("SLACK_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-global")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C-global")

    sent_with: list[dict] = []

    async def fake_send(*, bot_token, channel_id, **kw):
        sent_with.append({"bot_token": bot_token, "channel_id": channel_id})

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel
    from sqlmodel.ext.asyncio.session import AsyncSession

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as sess:
        ws = Workspace(name="WS", slug="ws-listener-r12",
                       slack_bot_token_enc="xoxb-workspace",  # plain text (no key)
                       slack_channel_id="C-workspace")
        sess.add(ws)
        run = Run(run_id="r12-run-1", team="T", request="x",
                  status="running", workspace_id=None)
        sess.add(run)
        await sess.commit()
        await sess.refresh(ws)
        run.workspace_id = ws.id
        sess.add(run)
        await sess.commit()

    monkeypatch.setattr(_listener, "engine", engine)

    with patch("app.core.slack_hitl.send_hitl_to_slack", new=fake_send), \
         patch("app.core.slack_hitl.start_slack_socket_mode"):
        from antcrew.core.events import Event as AcEvent
        fake_event = MagicMock()
        fake_event.type = "hitl.review_required"
        fake_event.run_id = "r12-run-1"
        fake_event.thread_id = "t1"
        fake_event.payload = {
            "review_id": "rev-r12-001",
            "agent_name": "PM",
            "artifact": {"title": "Test PRD"},
            "options": ["approve", "reject"],
        }
        fake_event.timestamp = 0.0

        await _listener._persist_event(fake_event)

    # Workspace token should have been used, not the global env one
    assert any(s["bot_token"] == "xoxb-workspace" for s in sent_with), \
        f"Expected workspace token, got: {sent_with}"
    assert any(s["channel_id"] == "C-workspace" for s in sent_with)

    await engine.dispose()
