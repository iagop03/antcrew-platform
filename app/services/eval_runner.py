"""Eval execution service — extracted from app/api/evals.py.

Keeps the thread-pool worker and config dataclass in the service layer
so both the evals API and the eval scheduler can import from here without
creating API-layer circular imports.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_MAX_WORKERS = 2
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="antcrew-eval")


@dataclass
class EvalRunConfig:
    """Internal representation of an eval job — decoupled from the HTTP schema."""
    team: str
    request: str
    name: str = ""
    model: str = ""
    judge_model: str = ""
    expect_min_tickets: int = 0
    expect_min_code_files: int = 0
    expect_review_verdict: str = ""


def run_eval_sync(eval_id: str, cfg: EvalRunConfig, loop: asyncio.AbstractEventLoop) -> None:
    """Execute an eval pipeline in the thread pool, then persist results.

    Creates its own DB engine per call — async SQLAlchemy connection pools are
    bound to the loop that created them and cannot be shared across threads.
    Emits eval.start / eval.done events to the main uvicorn loop via
    call_soon_threadsafe so the WebSocket dashboard updates in real time.
    """
    import asyncio as _aio
    from datetime import datetime, timezone
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession as _Sess
    from sqlmodel import select as _sel

    _db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./platform.db")

    async def _load_run_id() -> str | None:
        from app.models.run import EvalRun as _EvalRun
        _local_engine = create_async_engine(_db_url, echo=False)
        try:
            async with _Sess(_local_engine, expire_on_commit=False) as sess:
                row = (await sess.exec(_sel(_EvalRun).where(_EvalRun.eval_id == eval_id))).first()
                return row.run_id if row else None
        finally:
            await _local_engine.dispose()

    _run_id: str | None = _aio.run(_load_run_id())

    async def _persist(updates: dict) -> None:
        from app.models.run import EvalRun, Run as _Run
        _local_engine = create_async_engine(_db_url, echo=False)
        try:
            async with _Sess(_local_engine, expire_on_commit=False) as sess:
                row = (await sess.exec(_sel(EvalRun).where(EvalRun.eval_id == eval_id))).first()
                if row:
                    for k, v in updates.items():
                        setattr(row, k, v)
                    sess.add(row)

                    # Mirror terminal status to the linked stub Run.
                    if row.run_id and updates.get("status") in ("done", "error"):
                        run = (await sess.exec(
                            _sel(_Run).where(_Run.run_id == row.run_id)
                        )).first()
                        if run:
                            run.status = "success" if updates["status"] == "done" else "error"
                            run.cost_usd = updates.get("cost_usd", 0.0)
                            run.finished_at = updates.get("finished_at")
                            if run.created_at and run.finished_at:
                                fa = run.finished_at.replace(tzinfo=None) if run.finished_at.tzinfo else run.finished_at
                                ca = run.created_at.replace(tzinfo=None) if run.created_at.tzinfo else run.created_at
                                run.duration_s = (fa - ca).total_seconds()
                            sess.add(run)

                    await sess.commit()
        finally:
            await _local_engine.dispose()

    def _emit(event_type: str, payload: dict) -> None:
        try:
            from antcrew.core.events import bus, Event as _Event
            import time as _t
            ev = _Event(
                type=event_type,
                run_id=_run_id,
                thread_id="eval",
                payload={"eval_id": eval_id, **payload},
                timestamp=_t.time(),
            )
            loop.call_soon_threadsafe(bus.publish, ev)
        except Exception:
            pass

    t0 = time.perf_counter()
    _emit("eval.start", {"team": cfg.team, "name": cfg.name or cfg.request[:60]})
    try:
        from antcrew.eval.case import EvalCase
        from antcrew.eval.runner import EvalRunner
        from app.services.runner import _make_team

        team = _make_team(cfg.team, model=cfg.model)
        case = EvalCase(
            request=cfg.request,
            name=cfg.name or cfg.request[:60],
            expect_min_tickets=cfg.expect_min_tickets,
            expect_min_code_files=cfg.expect_min_code_files,
            expect_review_verdict=cfg.expect_review_verdict,
        )
        judge_llm = None
        if cfg.judge_model:
            try:
                from antcrew.config import build_llm as _build_llm
                judge_llm = _build_llm(cfg.judge_model)
            except Exception as _exc:
                log.warning("eval runner: failed to build judge_llm %r: %s", cfg.judge_model, _exc)
        runner = EvalRunner(team, judge_llm=judge_llm)
        report = runner.run_one(case)
        elapsed = (time.perf_counter() - t0) * 1000

        _aio.run(_persist({
            "status": "done",
            "report": report.to_dict(),
            "cost_usd": report.cost_usd,
            "elapsed_ms": round(elapsed, 1),
            "finished_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }))
        _emit("eval.done", {
            "team": cfg.team,
            "status": "done",
            "passed": report.passed,
            "overall_score": report.overall_score,
            "elapsed_ms": round(elapsed, 1),
        })
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        log.error("eval runner: %s failed: %s", eval_id, exc)
        from datetime import datetime, timezone as _tz
        _aio.run(_persist({
            "status": "error",
            "error": str(exc),
            "elapsed_ms": round(elapsed, 1),
            "finished_at": datetime.now(_tz.utc).replace(tzinfo=None),
        }))
        _emit("eval.done", {"team": cfg.team, "status": "error", "error": str(exc)})
