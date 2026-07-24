"""Tests for workspace contract schema CRUD (Phase 1 custom_fields extension)."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FONT_JARDINERIA_SCHEMA = {
    "type": "object",
    "properties": {
        "cms_platform": {"type": "string", "enum": ["wordpress", "webflow", "squarespace"]},
        "seo_keywords": {"type": "array", "items": {"type": "string"}},
        "brand_voice": {"type": "string"},
    },
}


async def _make_workspace(session: AsyncSession) -> int:
    ws = Workspace(name="Font Jardineria", slug=f"font-{uuid.uuid4().hex[:8]}")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws.id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PUT — upsert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_creates_schema(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    r = await client.put(
        f"/workspaces/{ws_id}/contract-schemas/PRD",
        json={"json_schema": _FONT_JARDINERIA_SCHEMA, "description": "Font Jardineria PRD fields"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["contract_name"] == "PRD"
    assert data["workspace_id"] == ws_id
    assert data["json_schema"] == _FONT_JARDINERIA_SCHEMA
    assert data["description"] == "Font Jardineria PRD fields"
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_upsert_updates_existing_schema(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    await client.put(f"/workspaces/{ws_id}/contract-schemas/PRD", json={"json_schema": {"type": "object"}})
    r = await client.put(
        f"/workspaces/{ws_id}/contract-schemas/PRD",
        json={"json_schema": _FONT_JARDINERIA_SCHEMA},
    )
    assert r.status_code == 200
    assert r.json()["json_schema"] == _FONT_JARDINERIA_SCHEMA


@pytest.mark.asyncio
async def test_upsert_unknown_contract_returns_422(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    r = await client.put(
        f"/workspaces/{ws_id}/contract-schemas/CodeReview",
        json={"json_schema": {"type": "object"}},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upsert_nonexistent_workspace_returns_404(client: AsyncClient, session: AsyncSession):
    r = await client.put(
        "/workspaces/99999/contract-schemas/PRD",
        json={"json_schema": _FONT_JARDINERIA_SCHEMA},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET — retrieve
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_schema(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    await client.put(f"/workspaces/{ws_id}/contract-schemas/PRD", json={"json_schema": _FONT_JARDINERIA_SCHEMA})
    r = await client.get(f"/workspaces/{ws_id}/contract-schemas/PRD")
    assert r.status_code == 200
    assert r.json()["json_schema"] == _FONT_JARDINERIA_SCHEMA


@pytest.mark.asyncio
async def test_get_missing_schema_returns_404(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    r = await client.get(f"/workspaces/{ws_id}/contract-schemas/PRD")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_schemas_empty(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    r = await client.get(f"/workspaces/{ws_id}/contract-schemas")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_schemas_returns_all(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    await client.put(f"/workspaces/{ws_id}/contract-schemas/PRD", json={"json_schema": _FONT_JARDINERIA_SCHEMA})
    r = await client.get(f"/workspaces/{ws_id}/contract-schemas")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["contract_name"] == "PRD"


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_schema(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    await client.put(f"/workspaces/{ws_id}/contract-schemas/PRD", json={"json_schema": _FONT_JARDINERIA_SCHEMA})
    r = await client.delete(f"/workspaces/{ws_id}/contract-schemas/PRD")
    assert r.status_code == 204
    # verify gone
    r2 = await client.get(f"/workspaces/{ws_id}/contract-schemas/PRD")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_schema_returns_404(client: AsyncClient, session: AsyncSession):
    ws_id = await _make_workspace(session)
    r = await client.delete(f"/workspaces/{ws_id}/contract-schemas/PRD")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Discovery endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extendable_contracts_endpoint(client: AsyncClient):
    r = await client.get("/workspaces/contract-schemas/extendable")
    assert r.status_code == 200
    data = r.json()
    assert "PRD" in data["extendable_contracts"]
    assert data["phase"] == 1
