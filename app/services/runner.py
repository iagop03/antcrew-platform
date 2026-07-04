"""Background team runner — dispatches antcrew pipelines from the API.

Flow:
  POST /run → dispatch() → team.run() in thread pool → _store_result()

The antcrew event bus listener (core/listener.py) handles real-time event
logging and creates/updates Run rows on pipeline.start/end. After team.run()
finishes, _store_result() writes the full RunResult state and upserts tickets.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from antcrew.core.events import bus

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
from app.services.runs import upsert_tickets_from_run

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="antcrew-runner")

# Teams that can be instantiated with zero config (model defaults from env).
# Each entry: team_name -> (module_path, class_name)
_TEAM_REGISTRY: dict[str, tuple[str, str]] = {
    "DevTeam":      ("antcrew.teams.dev_team",      "DevTeam"),
    "FullStackTeam":("antcrew.teams.fullstack_team", "FullStackTeam"),
    "ResearchTeam": ("antcrew.teams.research_team",  "ResearchTeam"),
    "ContentTeam":  ("antcrew.teams.content_team",   "ContentTeam"),
}

AVAILABLE_TEAMS = list(_TEAM_REGISTRY)


def _make_team(team_name: str):
    """Instantiate a team with default LLM (reads ANTHROPIC_API_KEY from env)."""
    if team_name not in _TEAM_REGISTRY:
        raise ValueError(f"Unknown team {team_name!r}. Available: {AVAILABLE_TEAMS}")
    module_path, class_name = _TEAM_REGISTRY[team_name]
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()  # model=None → AnthropicModel() default


def _run_sync(team_name: str, request: str, thread_id: str):
    """Synchronous team run — called inside ThreadPoolExecutor."""
    team = _make_team(team_name)
    return team.run(request, thread_id=thread_id)


async def _store_result(result) -> None:
    """Persist the full RunResult state and upsert tickets after run completes."""
    from sqlmodel import select
    from app.models.run import Run

    run_id = result.state.get("_run_id") if hasattr(result, "state") else None
    if not run_id:
        return

    state_dict = result.to_dict() if hasattr(result, "to_dict") else {}

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            stmt = select(Run).where(Run.run_id == run_id)
            db_run = (await session.exec(stmt)).first()
            if db_run:
                db_run.state = state_dict
                session.add(db_run)
            await upsert_tickets_from_run(session, run_id, result.state)
            await session.commit()
    except Exception as exc:
        log.error("runner: failed to store result for run %s: %s", run_id, exc)


async def dispatch(
    team_name: str,
    request: str,
    thread_id: str = "default",
) -> Optional[str]:
    """Start a team run in the background. Returns run_id once pipeline.start fires.

    The run_id is captured from the first pipeline.start event emitted by the
    team. Returns None on timeout (10 s) — the run continues in background.
    """
    loop = asyncio.get_event_loop()
    run_id_future: asyncio.Future[str] = loop.create_future()

    def _on_pipeline_start(event) -> None:
        if event.run_id and not run_id_future.done():
            loop.call_soon_threadsafe(run_id_future.set_result, event.run_id)

    bus.subscribe("pipeline.start", _on_pipeline_start)

    async def _bg() -> None:
        try:
            result = await loop.run_in_executor(
                _executor, _run_sync, team_name, request, thread_id
            )
            await _store_result(result)
        except Exception as exc:
            log.error("runner: %s failed: %s", team_name, exc)
            if not run_id_future.done():
                loop.call_soon_threadsafe(run_id_future.set_result, None)
        finally:
            bus.unsubscribe("pipeline.start", _on_pipeline_start)

    asyncio.ensure_future(_bg())

    try:
        return await asyncio.wait_for(asyncio.shield(run_id_future), timeout=10.0)
    except asyncio.TimeoutError:
        log.warning("runner: pipeline.start not received within 10 s for %s", team_name)
        return None
