"""Tests for GitHub App integration.

Covers:
G1  GET /github/callback — valid install stores installation record, redirects to settings
G2  GET /github/callback — delete action removes existing record
G3  GET /github/callback — missing/invalid state redirects to error page
G4  POST /webhooks/github — valid HMAC-SHA256 signature accepted
G5  POST /webhooks/github — invalid signature → 401
G6  POST /webhooks/github — installation.deleted event removes DB record
G7  GET /github/installations — returns installations scoped to workspace
G8  DELETE /github/installations/{id} — removes record (204)
G9  DELETE /github/installations/{id} — 404 for unknown installation
G10 DELETE /github/installations/{id} — 403 for wrong workspace
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.github_app import GitHubInstallation
from app.models.run import ApiKey, Workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_API_KEY = "test-key-github"
_WEBHOOK_SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _make_workspace(session: AsyncSession, name: str = "ws", slug: str = "ws") -> Workspace:
    ws = Workspace(name=name, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


async def _make_api_key(session: AsyncSession, workspace_id: int, raw: str = _API_KEY) -> ApiKey:
    from app.core.auth import _hash, _key_prefix
    key = ApiKey(
        label="test",
        key_hash=_hash(raw),
        key_prefix=_key_prefix(raw),
        workspace_id=workspace_id,
        role="admin",
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return key


async def _make_installation(
    session: AsyncSession,
    workspace_id: int,
    installation_id: int = 999,
) -> GitHubInstallation:
    inst = GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=installation_id,
        account_login="test-org",
        account_type="Organization",
    )
    session.add(inst)
    await session.commit()
    await session.refresh(inst)
    return inst


# ---------------------------------------------------------------------------
# G1 — callback: valid install stores record
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_callback_creates_installation(client: AsyncClient, session: AsyncSession, monkeypatch):
    ws = await _make_workspace(session)

    # Patch _make_app_jwt and httpx to avoid real GitHub calls
    import app.api.github_app as gh_mod
    import app.services.github_tokens as tok_mod

    monkeypatch.setattr(tok_mod, "_make_app_jwt", lambda: "fake-jwt")

    class _FakeResp:
        def __init__(self):
            self.is_success = True
        def json(self):
            return {"account": {"login": "my-org", "type": "Organization"}}

    import httpx

    class _FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient())

    resp = await client.get(
        "/github/callback",
        params={"installation_id": 42, "setup_action": "install", "state": str(ws.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert "github=connected" in resp.headers["location"]

    inst = (await session.exec(
        select(GitHubInstallation).where(GitHubInstallation.workspace_id == ws.id)
    )).first()
    assert inst is not None
    assert inst.installation_id == 42
    assert inst.account_login == "my-org"


# ---------------------------------------------------------------------------
# G2 — callback: delete action removes record
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_callback_delete_removes_installation(client: AsyncClient, session: AsyncSession):
    ws = await _make_workspace(session, name="ws2", slug="ws2")
    await _make_installation(session, ws.id, installation_id=77)

    resp = await client.get(
        "/github/callback",
        params={"installation_id": 77, "setup_action": "delete", "state": str(ws.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert "github=disconnected" in resp.headers["location"]

    remaining = (await session.exec(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == 77)
    )).first()
    assert remaining is None


# ---------------------------------------------------------------------------
# G3 — callback: missing state → error redirect
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_callback_missing_state_redirects_to_error(client: AsyncClient, session: AsyncSession):
    resp = await client.get(
        "/github/callback",
        params={"installation_id": 1, "setup_action": "install"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert "github=error" in resp.headers["location"]


# ---------------------------------------------------------------------------
# G4 — webhook: valid signature accepted
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_webhook_valid_signature(client: AsyncClient, session: AsyncSession, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    body = json.dumps({"action": "ping"}).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"received": True}


# ---------------------------------------------------------------------------
# G5 — webhook: invalid signature → 401
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_webhook_invalid_signature(client: AsyncClient, session: AsyncSession, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    body = json.dumps({"action": "ping"}).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=badhex",
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# G6 — webhook: installation.deleted removes DB record
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_webhook_installation_deleted(client: AsyncClient, session: AsyncSession, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    ws = await _make_workspace(session, name="ws3", slug="ws3")
    await _make_installation(session, ws.id, installation_id=55)

    payload = {
        "action": "deleted",
        "installation": {"id": 55, "account": {"login": "org"}},
    }
    body = json.dumps(payload).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 200

    remaining = (await session.exec(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == 55)
    )).first()
    assert remaining is None


# ---------------------------------------------------------------------------
# G7 — GET /github/installations scoped to workspace
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_installations(client: AsyncClient, session: AsyncSession):
    ws = await _make_workspace(session, name="ws4", slug="ws4")
    await _make_api_key(session, ws.id)
    await _make_installation(session, ws.id, installation_id=100)

    resp = await client.get(
        "/github/installations",
        params={"workspace_id": ws.id},
        headers={"X-API-Key": _API_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["installation_id"] == 100
    assert data[0]["account_login"] == "test-org"


# ---------------------------------------------------------------------------
# G8 — DELETE /github/installations/{id} removes record
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_disconnect_installation(client: AsyncClient, session: AsyncSession):
    ws = await _make_workspace(session, name="ws5", slug="ws5")
    await _make_api_key(session, ws.id)
    inst = await _make_installation(session, ws.id, installation_id=200)

    resp = await client.delete(
        f"/github/installations/{inst.installation_id}",
        headers={"X-API-Key": _API_KEY},
    )
    assert resp.status_code == 204

    remaining = (await session.exec(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == 200)
    )).first()
    assert remaining is None


# ---------------------------------------------------------------------------
# G9 — DELETE /github/installations/{id} → 404 for unknown
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_disconnect_installation_not_found(client: AsyncClient, session: AsyncSession):
    ws = await _make_workspace(session, name="ws6", slug="ws6")
    await _make_api_key(session, ws.id)

    resp = await client.delete(
        "/github/installations/9999",
        headers={"X-API-Key": _API_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# G10 — DELETE /github/installations/{id} → 403 for wrong workspace
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_disconnect_installation_wrong_workspace(client: AsyncClient, session: AsyncSession):
    ws_owner = await _make_workspace(session, name="ws7", slug="ws7")
    ws_other = await _make_workspace(session, name="ws8", slug="ws8")
    # API key is scoped to ws_other
    await _make_api_key(session, ws_other.id)
    inst = await _make_installation(session, ws_owner.id, installation_id=300)

    resp = await client.delete(
        f"/github/installations/{inst.installation_id}",
        headers={"X-API-Key": _API_KEY},
    )
    assert resp.status_code == 403
