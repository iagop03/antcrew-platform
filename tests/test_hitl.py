"""Tests for the HITL (Human-in-the-Loop) review flow."""
from __future__ import annotations

import concurrent.futures
import json

import pytest
from httpx import AsyncClient

from app.models.run import HitlReview
from app.core.channel import _PENDING_REVIEWS, resolve_review


@pytest.mark.asyncio
async def test_review_not_found(client: AsyncClient):
    r = await client.post("/reviews/nonexistent-review-id", json={"decision": "approve"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_review_invalid_decision(client: AsyncClient, session):
    session.add(HitlReview(
        review_id="rev-bad-decision",
        run_id="r1",
        agent_name="pm",
        artifact_json="{}",
        options_json='["approve","reject"]',
        status="pending",
    ))
    await session.commit()

    r = await client.post("/reviews/rev-bad-decision", json={"decision": "invalidoption"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_review_approve_resolves_future(client: AsyncClient, session, monkeypatch):
    import app.core.channel as _ch
    # This test exercises the in-memory Future path (HITL_FUTURE_MODE=1)
    monkeypatch.setattr(_ch, "_USE_DB_POLLING", False)

    review_id = "rev-approve-test"
    session.add(HitlReview(
        review_id=review_id,
        run_id="r1",
        agent_name="pm",
        artifact_json='{"title": "PRD v1"}',
        options_json='["approve","reject"]',
        status="pending",
    ))
    await session.commit()

    # Pre-register a future as if PlatformChannel created it
    fut: concurrent.futures.Future = concurrent.futures.Future()
    _PENDING_REVIEWS[review_id] = fut

    try:
        r = await client.post(f"/reviews/{review_id}", json={"decision": "approve"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "approved"   # verb → noun mapping
        assert data["decision"] == "approve"

        # Future should be resolved
        assert fut.done()
        result = fut.result(timeout=0)
        assert result["decision"] == "approve"
    finally:
        _PENDING_REVIEWS.pop(review_id, None)


@pytest.mark.asyncio
async def test_review_reject_with_feedback(client: AsyncClient, session, monkeypatch):
    import app.core.channel as _ch
    monkeypatch.setattr(_ch, "_USE_DB_POLLING", False)

    review_id = "rev-reject-test"
    session.add(HitlReview(
        review_id=review_id,
        run_id="r1",
        agent_name="pm",
        artifact_json="{}",
        options_json='["approve","reject","feedback"]',
        status="pending",
    ))
    await session.commit()

    fut: concurrent.futures.Future = concurrent.futures.Future()
    _PENDING_REVIEWS[review_id] = fut

    try:
        r = await client.post(f"/reviews/{review_id}", json={
            "decision": "feedback",
            "feedback": "Please add more detail",
        })
        assert r.status_code == 200
        assert r.json()["feedback"] == "Please add more detail"
        assert fut.done()
        assert fut.result(timeout=0)["feedback"] == "Please add more detail"
    finally:
        _PENDING_REVIEWS.pop(review_id, None)


@pytest.mark.asyncio
async def test_review_already_resolved(client: AsyncClient, session):
    review_id = "rev-already-done"
    session.add(HitlReview(
        review_id=review_id,
        run_id="r1",
        agent_name="pm",
        artifact_json="{}",
        options_json='["approve","reject"]',
        status="approve",  # already resolved
        decision="approve",
    ))
    await session.commit()

    r = await client.post(f"/reviews/{review_id}", json={"decision": "reject"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_resolve_review_helper(monkeypatch):
    import app.core.channel as _ch
    monkeypatch.setattr(_ch, "_USE_DB_POLLING", False)

    review_id = "helper-test"
    fut: concurrent.futures.Future = concurrent.futures.Future()
    _PENDING_REVIEWS[review_id] = fut

    try:
        found = resolve_review(review_id, {"decision": "approve"})
        assert found is True
        assert fut.done()
        assert fut.result(timeout=0)["decision"] == "approve"

        # Calling again returns False (already done)
        assert resolve_review(review_id, {"decision": "reject"}) is False
    finally:
        _PENDING_REVIEWS.pop(review_id, None)


@pytest.mark.asyncio
async def test_resolve_review_not_found(monkeypatch):
    import app.core.channel as _ch
    monkeypatch.setattr(_ch, "_USE_DB_POLLING", False)
    assert resolve_review("does-not-exist", {"decision": "approve"}) is False


@pytest.mark.asyncio
async def test_list_reviews_pending(client: AsyncClient, session):
    session.add(HitlReview(
        review_id="rev-list-p1", run_id="r1", agent_name="pm",
        artifact_json="{}", options_json='["approve"]', status="pending",
    ))
    session.add(HitlReview(
        review_id="rev-list-p2", run_id="r2", agent_name="pm",
        artifact_json="{}", options_json='["approve"]', status="approved",
    ))
    await session.commit()

    r = await client.get("/reviews/?status=pending")
    assert r.status_code == 200
    data = r.json()
    ids = [d["review_id"] for d in data]
    assert "rev-list-p1" in ids
    assert "rev-list-p2" not in ids


@pytest.mark.asyncio
async def test_list_reviews_filter_run_id(client: AsyncClient, session):
    session.add(HitlReview(
        review_id="rev-filt-1", run_id="run-A", agent_name="pm",
        artifact_json="{}", options_json='["approve"]', status="pending",
    ))
    session.add(HitlReview(
        review_id="rev-filt-2", run_id="run-B", agent_name="pm",
        artifact_json="{}", options_json='["approve"]', status="pending",
    ))
    await session.commit()

    r = await client.get("/reviews/?run_id=run-A")
    data = r.json()
    assert all(d["run_id"] == "run-A" for d in data)


@pytest.mark.asyncio
async def test_cancel_run_resolves_pending_hitl(client: AsyncClient, session, monkeypatch):
    import app.core.channel as _ch
    monkeypatch.setattr(_ch, "_USE_DB_POLLING", False)

    from app.models.run import Run

    session.add(Run(run_id="r-hitl-cancel", team="DevTeam", request="x", status="running"))
    review = HitlReview(
        review_id="rev-to-cancel", run_id="r-hitl-cancel", agent_name="pm",
        artifact_json="{}", options_json='["approve"]', status="pending",
    )
    session.add(review)
    await session.commit()

    # Register future as if PlatformChannel created it
    fut: concurrent.futures.Future = concurrent.futures.Future()
    _PENDING_REVIEWS["rev-to-cancel"] = fut

    try:
        r = await client.post("/runs/r-hitl-cancel/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        # Future was resolved with reject by cancel_run
        assert fut.done()
        assert fut.result(timeout=0)["cancelled"] is True
    finally:
        _PENDING_REVIEWS.pop("rev-to-cancel", None)


@pytest.mark.asyncio
async def test_hitl_flag_in_pipeline_request(client):
    """POST /run with hitl:true should pass force_hitl=True to dispatch."""
    from unittest.mock import AsyncMock, patch
    with patch("app.api.pipeline.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = "r-hitl"
        r = await client.post("/run/", json={
            "team": "DevTeam", "request": "build something", "hitl": True,
        })
    assert r.status_code == 202
    assert r.json()["hitl"] is True
    call_kwargs = mock_dispatch.call_args.kwargs
    assert call_kwargs["force_hitl"] is True


# ---------------------------------------------------------------------------
# DB polling strategy: _poll_db_for_decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poll_db_returns_when_resolved(tmp_path, monkeypatch):
    """_poll_db_for_decision returns once the HitlReview row transitions from pending."""
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel, select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.models.run import HitlReview
    import app.core.channel as _ch

    # On-disk SQLite so both engines (setup + poll) share the same file
    db_file = tmp_path / "poll_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    # Speed up the poll interval for the test
    monkeypatch.setattr(_ch, "_POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(_ch, "_REVIEW_TIMEOUT_S", 5.0)

    # Set up the DB schema and insert a pending review
    setup_engine = create_async_engine(db_url)
    async with setup_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with AsyncSession(setup_engine, expire_on_commit=False) as sess:
        sess.add(HitlReview(
            review_id="poll-rev-001", run_id="r-poll", agent_name="pm",
            artifact_json="{}", options_json='["approve"]', status="pending",
        ))
        await sess.commit()

    # Resolve the review after a short delay using a separate session
    async def _resolve_after():
        await asyncio.sleep(0.08)
        async with AsyncSession(setup_engine, expire_on_commit=False) as sess:
            row = (await sess.exec(
                select(HitlReview).where(HitlReview.review_id == "poll-rev-001")
            )).first()
            row.status = "approved"
            row.decision = "approve"
            sess.add(row)
            await sess.commit()

    resolve_task = asyncio.create_task(_resolve_after())

    result = await asyncio.wait_for(
        _ch._poll_db_for_decision("poll-rev-001"),
        timeout=3.0,
    )
    await resolve_task
    await setup_engine.dispose()

    assert result["decision"] == "approve"


@pytest.mark.asyncio
async def test_resolve_review_noop_in_db_polling_mode(monkeypatch):
    """resolve_review() returns True immediately when HITL_DB_POLLING=1 (no Future needed)."""
    import app.core.channel as _ch

    monkeypatch.setattr(_ch, "_USE_DB_POLLING", True)
    # No Future registered — resolve_review should still return True
    result = _ch.resolve_review("any-review-id", {"decision": "approve"})
    assert result is True
