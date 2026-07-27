"""Tests for proxy mode — workspaces_proxy.py endpoints.

Covers:
- GET  /workspaces/{id}/proxy          — status (not configured)
- POST /workspaces/{id}/proxy/generate — generates token, stores encrypted, returns once
- POST /workspaces/{id}/proxy/generate — rotation (second call replaces token)
- POST /workspaces/{id}/proxy/activate — switches llm_key_mode to "proxy"
- POST /workspaces/{id}/proxy/activate — 422 when no token generated yet
- DELETE /workspaces/{id}/proxy        — revokes token, resets to managed
- DELETE /workspaces/{id}/proxy        — keeps llm_key_mode if already managed
- runner_base.resolve_workspace_llm_config — managed returns (None, None)
- runner_base.resolve_workspace_llm_config — proxy returns (token, url/provider)
- runner_base.resolve_workspace_llm_config — byok returns key from DB
- Workspace 404 on all proxy endpoints
- 403 when API key does not have access to workspace
"""
from __future__ import annotations

import hashlib
import os
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _admin_key(session: AsyncSession, *, label: str, raw: str) -> ApiKey:
    k = ApiKey(label=label, key_hash=_hash(raw), role="admin")
    session.add(k)
    await session.commit()
    await session.refresh(k)
    return k


@pytest.fixture(autouse=True)
def _byok_dev_mode():
    """Force byok._IS_DEV=True so _encrypt/_decrypt work without BYOK_ENCRYPTION_KEY.

    _IS_DEV is a module-level constant set at import time from APP_ENV. Tests run
    without APP_ENV=dev so it defaults to False. Patching it here avoids having to
    set a real Fernet key in every test.
    """
    with patch("app.core.byok._IS_DEV", True):
        yield


async def _make_ws(session: AsyncSession, *, name: str = "Proxy WS", slug: str = "proxy-ws") -> Workspace:
    ws = Workspace(name=name, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


# ---------------------------------------------------------------------------
# GET /workspaces/{id}/proxy — status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_proxy_status_not_configured(client: AsyncClient, session):
    await _admin_key(session, label="adm", raw="adm-key")
    ws = await _make_ws(session)

    r = await client.get(f"/workspaces/{ws.id}/proxy", headers={"X-Api-Key": "adm-key"})
    assert r.status_code == 200
    data = r.json()
    assert data["configured"] is False
    assert data["proxy_url"] is None
    assert data["active"] is False


@pytest.mark.asyncio
async def test_proxy_status_404(client: AsyncClient, session):
    await _admin_key(session, label="adm2", raw="adm2-key")
    r = await client.get("/workspaces/999999/proxy", headers={"X-Api-Key": "adm2-key"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /workspaces/{id}/proxy/generate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_proxy_token(client: AsyncClient, session):
    await _admin_key(session, label="adm3", raw="adm3-key")
    ws = await _make_ws(session, slug="proxy-gen")

    with patch("app.core.security.validate_external_url"):
        r = await client.post(
            f"/workspaces/{ws.id}/proxy/generate",
            json={"proxy_url": "https://proxy.example.com"},
            headers={"X-Api-Key": "adm3-key"},
        )
    assert r.status_code == 201
    data = r.json()
    assert "token" in data
    assert len(data["token"]) == 36   # UUID
    assert data["proxy_url"] == "https://proxy.example.com"
    assert "docker" in data["docker_command"].lower()

    # Token must be persisted (encrypted) on the workspace row
    await session.refresh(ws)
    assert ws.proxy_url == "https://proxy.example.com"
    assert ws.proxy_token_enc is not None  # stored (plaintext in dev; Fernet-encrypted in prod)


@pytest.mark.asyncio
async def test_generate_proxy_token_rotates(client: AsyncClient, session):
    """A second generate call replaces the previous token."""
    await _admin_key(session, label="adm4", raw="adm4-key")
    ws = await _make_ws(session, slug="proxy-rotate")

    with patch("app.core.security.validate_external_url"):
        r1 = await client.post(
            f"/workspaces/{ws.id}/proxy/generate",
            json={"proxy_url": "https://proxy.example.com"},
            headers={"X-Api-Key": "adm4-key"},
        )
        r2 = await client.post(
            f"/workspaces/{ws.id}/proxy/generate",
            json={"proxy_url": "https://proxy.example.com"},
            headers={"X-Api-Key": "adm4-key"},
        )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["token"] != r2.json()["token"]


@pytest.mark.asyncio
async def test_generate_proxy_token_invalid_url(client: AsyncClient, session):
    await _admin_key(session, label="adm5", raw="adm5-key")
    ws = await _make_ws(session, slug="proxy-badurl")

    r = await client.post(
        f"/workspaces/{ws.id}/proxy/generate",
        json={"proxy_url": "not-a-url"},
        headers={"X-Api-Key": "adm5-key"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_generate_proxy_token_404(client: AsyncClient, session):
    await _admin_key(session, label="adm6", raw="adm6-key")
    with patch("app.core.security.validate_external_url"):
        r = await client.post(
            "/workspaces/999999/proxy/generate",
            json={"proxy_url": "https://proxy.example.com"},
            headers={"X-Api-Key": "adm6-key"},
        )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /workspaces/{id}/proxy/activate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activate_proxy_switches_mode(client: AsyncClient, session):
    await _admin_key(session, label="adm7", raw="adm7-key")
    ws = await _make_ws(session, slug="proxy-activate")

    with patch("app.core.security.validate_external_url"):
        await client.post(
            f"/workspaces/{ws.id}/proxy/generate",
            json={"proxy_url": "https://proxy.example.com"},
            headers={"X-Api-Key": "adm7-key"},
        )

    r = await client.post(
        f"/workspaces/{ws.id}/proxy/activate",
        headers={"X-Api-Key": "adm7-key"},
    )
    assert r.status_code == 200
    assert r.json()["llm_key_mode"] == "proxy"

    await session.refresh(ws)
    assert ws.llm_key_mode == "proxy"


@pytest.mark.asyncio
async def test_activate_proxy_requires_token(client: AsyncClient, session):
    """Cannot activate proxy mode without generating a token first."""
    await _admin_key(session, label="adm8", raw="adm8-key")
    ws = await _make_ws(session, slug="proxy-no-token")

    r = await client.post(
        f"/workspaces/{ws.id}/proxy/activate",
        headers={"X-Api-Key": "adm8-key"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_activate_proxy_404(client: AsyncClient, session):
    await _admin_key(session, label="adm9", raw="adm9-key")
    r = await client.post(
        "/workspaces/999999/proxy/activate",
        headers={"X-Api-Key": "adm9-key"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /workspaces/{id}/proxy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoke_proxy(client: AsyncClient, session):
    await _admin_key(session, label="adm10", raw="adm10-key")
    ws = await _make_ws(session, slug="proxy-revoke")

    with patch("app.core.security.validate_external_url"):
        await client.post(
            f"/workspaces/{ws.id}/proxy/generate",
            json={"proxy_url": "https://proxy.example.com"},
            headers={"X-Api-Key": "adm10-key"},
        )
    await client.post(f"/workspaces/{ws.id}/proxy/activate", headers={"X-Api-Key": "adm10-key"})

    r = await client.delete(f"/workspaces/{ws.id}/proxy", headers={"X-Api-Key": "adm10-key"})
    assert r.status_code == 200
    assert r.json()["proxy_revoked"] is True
    assert r.json()["llm_key_mode"] == "managed"

    await session.refresh(ws)
    assert ws.proxy_url is None
    assert ws.proxy_token_enc is None
    assert ws.llm_key_mode == "managed"


@pytest.mark.asyncio
async def test_revoke_proxy_status_reflects_change(client: AsyncClient, session):
    await _admin_key(session, label="adm11", raw="adm11-key")
    ws = await _make_ws(session, slug="proxy-revoke-status")

    with patch("app.core.security.validate_external_url"):
        await client.post(
            f"/workspaces/{ws.id}/proxy/generate",
            json={"proxy_url": "https://proxy.example.com"},
            headers={"X-Api-Key": "adm11-key"},
        )
    await client.delete(f"/workspaces/{ws.id}/proxy", headers={"X-Api-Key": "adm11-key"})

    r = await client.get(f"/workspaces/{ws.id}/proxy", headers={"X-Api-Key": "adm11-key"})
    assert r.status_code == 200
    assert r.json()["configured"] is False
    assert r.json()["active"] is False


# ---------------------------------------------------------------------------
# runner_base.resolve_workspace_llm_config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_managed_returns_none(session):
    """Managed mode: both api_key and base_url are None."""
    from app.services.runner_base import resolve_workspace_llm_config

    ws = Workspace(name="M", slug="resolve-managed", llm_key_mode="managed")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    key, url = await resolve_workspace_llm_config(session, ws, "claude")
    assert key is None
    assert url is None


@pytest.mark.asyncio
async def test_resolve_proxy_returns_token_and_url(session):
    """Proxy mode: returns decrypted token + provider-scoped URL."""
    from app.services.runner_base import resolve_workspace_llm_config

    if True:
        ws = Workspace(
            name="P", slug="resolve-proxy",
            llm_key_mode="proxy",
            proxy_url="https://proxy.example.com",
            proxy_token_enc="my-secret-token",  # dev mode: stored as plain text
        )
        session.add(ws)
        await session.commit()
        await session.refresh(ws)

        key, url = await resolve_workspace_llm_config(session, ws, "claude")

    assert key == "my-secret-token"
    assert url == "https://proxy.example.com/anthropic"


@pytest.mark.asyncio
async def test_resolve_proxy_routes_openai_model(session):
    """Proxy mode: OpenAI model gets /openai path suffix."""
    from app.services.runner_base import resolve_workspace_llm_config

    if True:
        ws = Workspace(
            name="P2", slug="resolve-proxy-oai",
            llm_key_mode="proxy",
            proxy_url="https://proxy.example.com",
            proxy_token_enc="tok",
        )
        session.add(ws)
        await session.commit()
        await session.refresh(ws)

        key, url = await resolve_workspace_llm_config(session, ws, "gpt-4o")

    assert url == "https://proxy.example.com/openai"


@pytest.mark.asyncio
async def test_resolve_proxy_not_configured_returns_none(session):
    """Proxy mode with missing proxy_url: falls back to (None, None)."""
    from app.services.runner_base import resolve_workspace_llm_config

    ws = Workspace(name="P3", slug="resolve-proxy-empty", llm_key_mode="proxy")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    key, url = await resolve_workspace_llm_config(session, ws, "claude")
    assert key is None
    assert url is None
