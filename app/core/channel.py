"""PlatformChannel — antcrew BaseChannel implementation for the platform's HITL flow.

When a team agent with approval_required=True runs via POST /run/, this channel:
1. Emits hitl.review_required on the antcrew event bus (→ listener persists it → WS forwards)
2. Waits for a decision via one of two strategies:
   a. DB polling (DEFAULT, multi-worker): polls HitlReview table every HITL_POLL_INTERVAL_S
      seconds. Works across uvicorn workers. Use with PostgreSQL.
   b. In-memory Future (HITL_FUTURE_MODE=1, single-worker): concurrent.futures.Future resolved
      by POST /reviews/{review_id} calling resolve_review(). Near-instant, no DB overhead.
      Only safe with a single uvicorn worker — breaks silently in multi-worker deployments.

Thread-safety: concurrent.futures.Future is thread-safe by design. asyncio.wrap_future
bridges the thread's local event loop to the main loop via call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import uuid
from typing import Optional

import os as _os

log = logging.getLogger(__name__)

# Registry: review_id → concurrent.futures.Future[dict]
# Written from executor thread (PlatformChannel.send_for_review).
# Read+resolved from main event loop (resolve_review, called by HTTP handler).
# NOTE: only used when HITL_FUTURE_MODE=1. Breaks for multi-worker deployments.
_PENDING_REVIEWS: dict[str, concurrent.futures.Future] = {}

_REVIEW_TIMEOUT_S = float(_os.environ.get("HITL_TIMEOUT_S", "3600"))

# DB polling is the default — safe for multi-worker uvicorn deployments.
# Set HITL_FUTURE_MODE=1 to switch to in-memory Future (faster, single-worker only).
_USE_DB_POLLING = _os.environ.get("HITL_FUTURE_MODE", "0") != "1"
_POLL_INTERVAL_S = float(_os.environ.get("HITL_POLL_INTERVAL_S", "1.0"))


class PlatformChannel:
    """Drop-in replacement for ConsoleChannel/SlackChannel for platform-dispatched runs."""

    def __init__(self, timeout_s: Optional[float] = None) -> None:
        self._run_id: Optional[str] = None
        self._timeout_s: float = timeout_s if timeout_s is not None else _REVIEW_TIMEOUT_S

    def set_run_id(self, run_id: str) -> None:
        """Set once pipeline.start fires and we know the run_id."""
        self._run_id = run_id

    async def notify(self, message: str, **kwargs) -> None:
        pass  # notifications surface via bus events

    async def send_for_review(
        self,
        artifact,
        agent_name: str,
        session_id: str,
        response_options: Optional[list[str]] = None,
    ) -> dict:
        """Emit a review request and wait for a decision from the HTTP API."""
        review_id = str(uuid.uuid4())
        artifact_data = _serialize_artifact(artifact)
        options = response_options or ["approve", "edit", "reject"]

        from antcrew.core.events import bus
        bus.emit(
            "hitl.review_required",
            {
                "review_id": review_id,
                "agent_name": agent_name,
                "options": options,
                "artifact": artifact_data,
            },
            run_id=self._run_id,
            thread_id=session_id,
        )

        log.info(
            "platform HITL: waiting for review %s (agent=%s, run=%s, mode=%s)",
            review_id, agent_name, self._run_id,
            "db-poll" if _USE_DB_POLLING else "future (HITL_FUTURE_MODE=1)",
        )

        try:
            if _USE_DB_POLLING:
                decision = await _poll_db_for_decision(review_id, self._timeout_s)
            else:
                decision = await _wait_future(review_id, self._timeout_s)
            log.info("platform HITL: review %s resolved → %s", review_id, decision.get("decision"))
            return decision
        except asyncio.TimeoutError:
            log.warning("platform HITL: review %s timed out", review_id)
            await _mark_review_timed_out(review_id)
            return {"decision": "reject", "timeout": True}


async def _wait_future(review_id: str, timeout_s: float = _REVIEW_TIMEOUT_S) -> dict:
    """Single-worker strategy: block on an in-memory concurrent.futures.Future.

    The Future is resolved by resolve_review() called from the HTTP handler on
    the main event loop. asyncio.wrap_future bridges the two event loops.
    """
    fut: concurrent.futures.Future = concurrent.futures.Future()
    _PENDING_REVIEWS[review_id] = fut
    try:
        asyncio_fut = asyncio.wrap_future(fut)
        return await asyncio.wait_for(asyncio_fut, timeout=timeout_s)
    except asyncio.TimeoutError:
        return {"decision": "reject", "timeout": True}
    finally:
        _PENDING_REVIEWS.pop(review_id, None)


async def _poll_db_for_decision(review_id: str, timeout_s: float = _REVIEW_TIMEOUT_S) -> dict:
    """Multi-worker strategy: poll HitlReview table until status changes from 'pending'.

    Creates a short-lived async engine per call so it's safe to use from any
    event loop (each executor thread has its own asyncio.run() loop).
    Recommended for PostgreSQL; works with SQLite in single-server setups.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import select as _sel
    from sqlmodel.ext.asyncio.session import AsyncSession as _Session

    db_url = _os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./platform.db")
    _engine = create_async_engine(db_url, echo=False)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    try:
        while loop.time() < deadline:
            async with _Session(_engine, expire_on_commit=False) as _s:
                from app.models.run import HitlReview as _HitlReview
                row = (await _s.exec(
                    _sel(_HitlReview).where(_HitlReview.review_id == review_id)
                )).first()
                if row is not None and row.status != "pending":
                    return {
                        "decision": row.decision or "reject",
                        "edited": row.edited_json,
                        "feedback": row.feedback,
                    }
            await asyncio.sleep(_POLL_INTERVAL_S)
    finally:
        await _engine.dispose()

    return {"decision": "reject", "timeout": True}


async def _mark_review_timed_out(review_id: str) -> None:
    """Update HitlReview row to status='timeout' when the wait expires."""
    from datetime import datetime, timezone
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import select as _sel
    from sqlmodel.ext.asyncio.session import AsyncSession as _Session

    db_url = _os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./platform.db")
    _engine = create_async_engine(db_url, echo=False)
    try:
        async with _Session(_engine, expire_on_commit=False) as _s:
            from app.models.run import HitlReview as _HitlReview
            row = (await _s.exec(_sel(_HitlReview).where(_HitlReview.review_id == review_id))).first()
            if row and row.status == "pending":
                row.status = "timeout"
                row.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
                _s.add(row)
                await _s.commit()
    except Exception as exc:
        log.warning("platform HITL: failed to mark review %s as timeout: %s", review_id, exc)
    finally:
        await _engine.dispose()


def resolve_review(review_id: str, decision: dict) -> bool:
    """Resolve a pending review via the in-memory Future strategy.

    Called from the main event loop HTTP handler (POST /reviews/:id).
    Returns True if resolved, False if not found or already done.
    No-op in DB polling mode (resolution happens via DB row update itself).
    """
    if _USE_DB_POLLING:
        return True  # resolution is the DB row update itself
    fut = _PENDING_REVIEWS.get(review_id)
    if fut is None or fut.done():
        return False
    fut.set_result(decision)
    return True


def _serialize_artifact(artifact) -> object:
    """Convert a Pydantic model or list to a JSON-serializable structure."""
    if isinstance(artifact, list):
        return [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in artifact
        ]
    if hasattr(artifact, "model_dump"):
        return artifact.model_dump()
    return artifact
