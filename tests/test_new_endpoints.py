"""Tests for replay, compare-artifacts, and quick-audit endpoints."""
from __future__ import annotations

import hashlib

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


_RAW_KEY = "newep-default-apikey"


async def _make_ws_and_key(session: AsyncSession, slug: str, raw_key: str) -> tuple:
    ws = Workspace(name=slug, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    key_hash = _sha256(raw_key)
    key = ApiKey(
        label=f"key-{slug}",
        key_hash=key_hash,
        key_prefix=key_hash[:16],
        workspace_id=ws.id,
        role="admin",
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return ws, key


def _h(raw_key: str = _RAW_KEY) -> dict:
    return {"X-Api-Key": raw_key}


# ---------------------------------------------------------------------------
# POST /runs/{run_id}/replay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_nonexistent_run(client, session: AsyncSession):
    """Replaying a non-existent run → 404."""
    await _make_ws_and_key(session, "replay-ws", _RAW_KEY)
    r = await client.post("/runs/nonexistent-run-id/replay", json={}, headers=_h())
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_replay_unknown_run_returns_404(client, session: AsyncSession):
    """Replaying an unknown-format run ID → 404."""
    await _make_ws_and_key(session, "replay-ws2", "replay-ws2-key")
    r = await client.post("/runs/totally-nonexistent-xyz/replay", json={}, headers=_h("replay-ws2-key"))
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# GET /runs/compare-artifacts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compare_artifacts_missing_params(client, session: AsyncSession):
    """Missing run_a or run_b → 422."""
    await _make_ws_and_key(session, "cmp-ws", _RAW_KEY)
    r = await client.get("/runs/compare-artifacts?run_a=abc", headers=_h())
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_compare_artifacts_nonexistent_runs(client, session: AsyncSession):
    """Both run IDs given but neither exists → 404."""
    await _make_ws_and_key(session, "cmp-ws2", _RAW_KEY)
    r = await client.get("/runs/compare-artifacts?run_a=aaa&run_b=bbb", headers=_h())
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_compare_artifacts_both_params_required(client, session: AsyncSession):
    """run_b missing → 422."""
    await _make_ws_and_key(session, "cmp-ws3", "cmp-ws3-key")
    r = await client.get("/runs/compare-artifacts?run_b=bbb", headers=_h("cmp-ws3-key"))
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# POST /security/quick-audit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quick_audit_missing_required_fields(client, session: AsyncSession):
    """Missing github_repo → 422."""
    await _make_ws_and_key(session, "audit-ws", _RAW_KEY)
    r = await client.post(
        "/security/quick-audit",
        json={"github_token": "ghp_fake"},
        headers=_h(),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_quick_audit_missing_token(client, session: AsyncSession):
    """Missing github_token → 422."""
    await _make_ws_and_key(session, "audit-ws2", _RAW_KEY)
    r = await client.post(
        "/security/quick-audit",
        json={"github_repo": "owner/repo"},
        headers=_h(),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_quick_audit_both_fields_required(client, session: AsyncSession):
    """github_repo and github_token are both required → 422 if either missing."""
    await _make_ws_and_key(session, "audit-ws2b", "audit-ws2b-key")
    r = await client.post(
        "/security/quick-audit",
        json={"github_repo": "owner/repo"},  # missing token
        headers=_h("audit-ws2b-key"),
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_quick_audit_accepted(client, session: AsyncSession):
    """Valid payload → 202 with run_id and cycle_id."""
    from unittest.mock import AsyncMock, patch

    await _make_ws_and_key(session, "audit-ws3", _RAW_KEY)
    with patch("app.api.security_audit._run_audit_task", new_callable=AsyncMock):
        r = await client.post(
            "/security/quick-audit",
            json={
                "github_repo": "owner/repo",
                "github_token": "ghp_fake_token_for_test",
                "github_branch": "main",
                "max_cost_usd": 1.0,
            },
            headers=_h(),
        )
    assert r.status_code == 202, r.text
    d = r.json()
    assert "run_id" in d
    assert "cycle_id" in d
    assert d["status"] == "pending"
    assert d["github_repo"] == "owner/repo"
