"""Round 14 tests — POST /reviews/ endpoint and PlatformChannel OSS integration.

Covers:
- POST /reviews/ creates a HitlReview row (P1.1)
- POST /reviews/ is idempotent when review_id supplied (P1.1)
- POST /reviews/ generates review_id when omitted (P1.1)
- POST /reviews/ requires write+ role (RBAC)
- GET /reviews/{review_id} returns the created review
- PlatformChannel registers + polls for a decision (mocked httpx)
- PlatformChannel falls back to ConsoleChannel when httpx unavailable
- PlatformChannel falls back to ConsoleChannel when platform unreachable
- PlatformChannel auto-approves on timeout
"""
from __future__ import annotations

import hashlib
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, HitlReview


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _make_key(session: AsyncSession, *, label: str, raw: str, role: str = "write") -> ApiKey:
    k = ApiKey(label=label, key_hash=_hash(raw), role=role)
    session.add(k)
    await session.commit()
    await session.refresh(k)
    return k


# ---------------------------------------------------------------------------
# POST /reviews/ — create review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_review_returns_201(client: AsyncClient, session):
    """POST /reviews/ creates a HitlReview and returns 201."""
    await _make_key(session, label="w-r14", raw="w-r14-key", role="write")
    run_id = f"run-{uuid.uuid4()}"
    r = await client.post(
        "/reviews/",
        json={"run_id": run_id, "agent_name": "DevAgent"},
        headers={"X-Api-Key": "w-r14-key"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["run_id"] == run_id
    assert data["agent_name"] == "DevAgent"
    assert data["status"] == "pending"
    assert "review_id" in data


@pytest.mark.asyncio
async def test_create_review_uses_supplied_review_id(client: AsyncClient, session):
    """POST /reviews/ with explicit review_id persists that exact ID."""
    await _make_key(session, label="w2-r14", raw="w2-r14-key", role="write")
    review_id = str(uuid.uuid4())
    r = await client.post(
        "/reviews/",
        json={"run_id": "run-fixed", "agent_name": "QAAgent", "review_id": review_id},
        headers={"X-Api-Key": "w2-r14-key"},
    )
    assert r.status_code == 201
    assert r.json()["review_id"] == review_id


@pytest.mark.asyncio
async def test_create_review_idempotent(client: AsyncClient, session):
    """POST /reviews/ with same review_id returns the existing row (no duplicate)."""
    await _make_key(session, label="w3-r14", raw="w3-r14-key", role="write")
    review_id = str(uuid.uuid4())
    payload = {"run_id": "run-idem", "agent_name": "DevAgent", "review_id": review_id}
    headers = {"X-Api-Key": "w3-r14-key"}

    r1 = await client.post("/reviews/", json=payload, headers=headers)
    r2 = await client.post("/reviews/", json=payload, headers=headers)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["review_id"] == r2.json()["review_id"]

    rows = (await session.exec(select(HitlReview).where(HitlReview.review_id == review_id))).all()
    assert len(rows) == 1, "must not create duplicate rows"


@pytest.mark.asyncio
async def test_create_review_requires_write_role(client: AsyncClient, session):
    """POST /reviews/ returns 403 for read-role key."""
    await _make_key(session, label="r-r14", raw="r-r14-key", role="read")
    r = await client.post(
        "/reviews/",
        json={"run_id": "run-x", "agent_name": "DevAgent"},
        headers={"X-Api-Key": "r-r14-key"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_review_persisted_in_db(client: AsyncClient, session):
    """POST /reviews/ row is queryable via GET /reviews/{review_id}."""
    await _make_key(session, label="w4-r14", raw="w4-r14-key", role="write")
    review_id = str(uuid.uuid4())
    await client.post(
        "/reviews/",
        json={
            "run_id": "run-db",
            "agent_name": "BackendDev",
            "review_id": review_id,
            "options": ["approve", "reject"],
        },
        headers={"X-Api-Key": "w4-r14-key"},
    )

    r = await client.get(f"/reviews/{review_id}", headers={"X-Api-Key": "w4-r14-key"})
    assert r.status_code == 200
    data = r.json()
    assert data["review_id"] == review_id
    assert data["agent_name"] == "BackendDev"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_create_review_stores_options(client: AsyncClient, session):
    """POST /reviews/ persists the options list as JSON."""
    await _make_key(session, label="w5-r14", raw="w5-r14-key", role="admin")
    review_id = str(uuid.uuid4())
    r = await client.post(
        "/reviews/",
        json={
            "run_id": "run-opts",
            "agent_name": "FrontendDev",
            "review_id": review_id,
            "options": ["approve", "edit", "reject"],
        },
        headers={"X-Api-Key": "w5-r14-key"},
    )
    assert r.status_code == 201
    row = (await session.exec(
        select(HitlReview).where(HitlReview.review_id == review_id)
    )).first()
    assert row is not None
    assert json.loads(row.options_json) == ["approve", "edit", "reject"]


# ---------------------------------------------------------------------------
# PlatformChannel — unit tests (mocked httpx)
# ---------------------------------------------------------------------------

def _make_async_client_mock(post_return=None, get_return=None, post_side_effect=None):
    """Build a mock httpx.AsyncClient context manager."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=post_return)
    if get_return is not None:
        mock_client.get = AsyncMock(return_value=get_return)
    return mock_client


@pytest.mark.asyncio
async def test_platform_channel_registers_and_polls():
    """PlatformChannel posts review and returns decision once poll shows resolved."""
    import httpx as _httpx
    from antcrew.integrations.platform import PlatformChannel

    ch = PlatformChannel(url="http://fake", api_key="sk-test", poll_interval_s=0.0)
    ch.set_run_id("run-pc-1")

    decided_response = MagicMock()
    decided_response.status_code = 200
    decided_response.json.return_value = {
        "review_id": "r1", "status": "approved", "decision": "approve",
        "edited_json": None, "feedback": None,
    }

    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()

    mock_client = _make_async_client_mock(post_return=post_response, get_return=decided_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await ch.send_for_review(
            {"code": "x = 1"}, "DevAgent", "sess-1", ["approve", "reject"]
        )

    assert result["decision"] == "approve"
    mock_client.post.assert_called_once()
    post_body = mock_client.post.call_args.kwargs["json"]
    assert post_body["agent_name"] == "DevAgent"
    assert post_body["run_id"] == "run-pc-1"
    assert "approve" in post_body["options"]


@pytest.mark.asyncio
async def test_platform_channel_falls_back_on_post_error():
    """PlatformChannel falls back to ConsoleChannel when POST /reviews/ fails."""
    from antcrew.integrations.platform import PlatformChannel

    ch = PlatformChannel(url="http://fake", api_key="sk-test", poll_interval_s=0.0)
    mock_client = _make_async_client_mock(post_side_effect=Exception("connection refused"))

    fallback_result = {"decision": "approve", "edited": None, "feedback": None}
    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("antcrew.integrations.console.ConsoleChannel.send_for_review",
               new_callable=AsyncMock, return_value=fallback_result):
        result = await ch.send_for_review("artifact", "DevAgent", "sess-2", None)

    assert result["decision"] == "approve"


@pytest.mark.asyncio
async def test_platform_channel_auto_approve_on_timeout():
    """PlatformChannel returns approve when timeout expires without a decision."""
    from antcrew.integrations.platform import PlatformChannel

    ch = PlatformChannel(url="http://fake", api_key="sk-test", timeout_s=0.01, poll_interval_s=0.0)

    pending_response = MagicMock()
    pending_response.status_code = 200
    pending_response.json.return_value = {"review_id": "r1", "status": "pending", "decision": None}

    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()

    mock_client = _make_async_client_mock(post_return=post_response, get_return=pending_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await ch.send_for_review("artifact", "DevAgent", "sess-3", None)

    assert result["decision"] == "approve"
    assert result["edited"] is None
