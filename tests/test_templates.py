"""Tests for run template CRUD."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.run import RunTemplate


@pytest.mark.asyncio
async def test_list_templates_empty(client: AsyncClient):
    r = await client.get("/templates/")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_template(client: AsyncClient):
    r = await client.post("/templates/", json={
        "name": "auth-module",
        "team": "DevTeam",
        "request": "Build an auth module with JWT",
        "max_cost_usd": 2.5,
    })
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "auth-module"
    assert data["team"] == "DevTeam"
    assert data["max_cost_usd"] == 2.5
    assert "id" in data


@pytest.mark.asyncio
async def test_create_template_invalid_team(client: AsyncClient):
    r = await client.post("/templates/", json={
        "name": "bad",
        "team": "NonExistentTeam",
        "request": "x",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_template_empty_name(client: AsyncClient):
    r = await client.post("/templates/", json={
        "name": "   ",
        "team": "DevTeam",
        "request": "x",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_templates_populated(client: AsyncClient, session):
    session.add(RunTemplate(name="t1", team="DevTeam", request="req1"))
    session.add(RunTemplate(name="t2", team="ResearchTeam", request="req2"))
    await session.commit()

    r = await client.get("/templates/")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "t1" in names
    assert "t2" in names


@pytest.mark.asyncio
async def test_delete_template(client: AsyncClient, session):
    t = RunTemplate(name="delete-me", team="DevTeam", request="r")
    session.add(t)
    await session.commit()
    await session.refresh(t)

    r = await client.delete(f"/templates/{t.id}")
    assert r.status_code == 204

    r2 = await client.get("/templates/")
    assert all(item["id"] != t.id for item in r2.json())


@pytest.mark.asyncio
async def test_delete_template_not_found(client: AsyncClient):
    r = await client.delete("/templates/999999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_template_without_cost(client: AsyncClient):
    r = await client.post("/templates/", json={
        "name": "minimal",
        "team": "DevTeam",
        "request": "Do something",
    })
    assert r.status_code == 201
    assert r.json()["max_cost_usd"] is None
