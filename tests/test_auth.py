"""Tests for API key auth."""
from __future__ import annotations

import os
import pytest


async def test_health_always_open(client):
    """Health endpoint never requires auth."""
    r = await client.get("/health")
    assert r.status_code == 200


async def test_no_key_required_when_env_not_set(client, monkeypatch):
    """When PLATFORM_API_KEY is unset, all endpoints are open."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    r = await client.get("/runs/")
    assert r.status_code == 200


async def test_valid_key_accepted(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.get("/runs/", headers={"X-Api-Key": "test-secret"})
    assert r.status_code == 200


async def test_wrong_key_rejected(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.get("/runs/", headers={"X-Api-Key": "wrong"})
    assert r.status_code == 401


async def test_missing_key_rejected(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.get("/runs/")
    assert r.status_code == 401


async def test_tickets_protected(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.get("/tickets/")
    assert r.status_code == 401


async def test_run_endpoint_protected(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.post("/run/", json={"team": "DevTeam", "request": "x"})
    assert r.status_code == 401
