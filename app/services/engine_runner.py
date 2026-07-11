"""Background engine runner — dispatches antcrew capability-driven pipelines.

Flow:
    POST /engine/run
        → dispatch_engine()
            → emits pipeline.start on the global bus (DB row created by listener)
            → spawns _run_engine_sync() in the thread pool
                → EventLog + EventBusBridge (agent.start/end events)
                → Operator.run() until goal or error
                → returns (success, cost_usd)
            → emits pipeline.end with real LLM cost
        ← returns run_id immediately

The platform's existing listener, WebSocket stream, and /runs endpoints work
transparently for engine runs — no changes needed there.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import threading as _threading
import uuid as _uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from antcrew.core.events import bus, Event as BusEvent, new_run_id

log = logging.getLogger(__name__)

_MAX_WORKERS = int(os.environ.get("ANTCREW_WORKERS", "4"))
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="antcrew-engine")

# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

_cancel_events: dict[str, _threading.Event] = {}


def cancel_engine_run(run_id: str) -> bool:
    """Signal an in-flight engine run to stop. Returns True if the run was found."""
    event = _cancel_events.get(run_id)
    if event is None:
        return False
    event.set()
    return True


# ---------------------------------------------------------------------------
# HITL threading bridge
# ---------------------------------------------------------------------------

_engine_reviews: dict[str, _threading.Event] = {}
_engine_verdicts: dict[str, dict] = {}


def resolve_engine_review(
    review_id: str,
    verdict: str,
    feedback: str | None,
    new_content=None,
) -> bool:
    """Unblock a HitlReviewer waiting on *review_id*. Called from the review API.

    *new_content* carries the edited artifact body for verdict="edit"; passed
    through to HitlReviewer so it can write the modified artifact without an LLM call.
    """
    event = _engine_reviews.pop(review_id, None)
    if event is None:
        return False
    _engine_verdicts[review_id] = {"verdict": verdict, "feedback": feedback, "new_content": new_content}
    event.set()
    return True


def _make_review_callback(run_id: str, cap_name: str, event_log, timeout: int = 3600):
    """Return a blocking callable for HitlReviewer that integrates with the platform bus."""
    def request_review(content) -> dict:
        from antcrew_engine.engine.events import HitlRequested, HitlResolved

        review_id = str(_uuid.uuid4())
        event = _threading.Event()
        _engine_reviews[review_id] = event

        event_log.emit(HitlRequested(review_id=review_id, reviewed_capability=cap_name))

        bus.emit(BusEvent(
            "hitl.review_required",
            {
                "review_id":  review_id,
                "agent_name": f"engine:{cap_name}",
                "options":    ["approve", "reject"],
                "artifact":   content if isinstance(content, dict)
                              else {"content": str(content)[:2_000]},
            },
            run_id=run_id,
            thread_id="default",
        ))

        fired = event.wait(timeout=timeout)
        if not fired:
            _engine_reviews.pop(review_id, None)

        verdict_data = _engine_verdicts.pop(review_id, {"verdict": "timeout", "feedback": None})
        event_log.emit(HitlResolved(
            review_id=review_id,
            verdict=verdict_data.get("verdict", "timeout"),
        ))
        return verdict_data

    return request_review


def _patch_downstream_needs(registry, reviewed_cap: str, old_cond_str: str | None = None) -> None:
    """Replace the gating condition with '<cap>_approved' in all downstream executors' needs.

    old_cond_str defaults to f'{reviewed_cap}_exists' but should be the actual condition
    produced by the upstream capability (e.g. 'architecture_exists' for 'architect').
    """
    import dataclasses
    from antcrew_engine.engine import ConditionId

    old_cond  = ConditionId(old_cond_str or f"{reviewed_cap}_exists")
    new_cond  = ConditionId(f"{reviewed_cap}_approved")
    hitl_name = f"hitl_{reviewed_cap}"

    for executor in registry.all():
        if executor.descriptor.name == hitl_name:
            continue  # don't patch the reviewer itself
        if old_cond in executor.descriptor.needs:
            executor.descriptor = dataclasses.replace(
                executor.descriptor,
                needs=executor.descriptor.needs - frozenset([old_cond]) | frozenset([new_cond]),
            )


def _load_existing_codebase(store, source_dir: Path, goal_description: str) -> None:
    """Seed the store with an existing project's .py files + stub planning artifacts."""
    from antcrew_engine.engine import Artifact, ArtifactId, ArtifactKind

    _SKIP = frozenset([".antcrew", "__pycache__", "venv", ".venv", ".git", "node_modules"])

    loaded = 0
    for py_file in sorted(source_dir.rglob("*.py")):
        try:
            rel = py_file.relative_to(source_dir)
        except ValueError:
            continue
        if any(part in _SKIP for part in rel.parts):
            continue
        rel_str = str(rel).replace("\\", "/")
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        store.write(Artifact(
            id       = ArtifactId(f"src/{rel_str}"),
            kind     = ArtifactKind.SOURCE,
            content  = content,
            metadata = {"file_path": rel_str, "source": "from_dir"},
        ))
        loaded += 1

    log.info("engine runner: loaded %d source files from %s", loaded, source_dir)

    # Stub planning artifacts so the engine skips planning phases
    store.write(Artifact(
        id       = ArtifactId("requirements"),
        kind     = ArtifactKind.REQUIREMENTS,
        content  = (
            f"# Requirements\n\nExisting project loaded from `{source_dir}`.\n\n"
            f"Goal: {goal_description}"
        ),
        metadata = {"file_path": ".antcrew/requirements.md"},
    ))
    store.write(Artifact(
        id       = ArtifactId("architecture"),
        kind     = ArtifactKind.ARCHITECTURE,
        content  = (
            f"# Architecture\n\nExisting codebase — see source files loaded from `{source_dir}`."
        ),
        metadata = {"file_path": ".antcrew/architecture.md"},
    ))
    store.write(Artifact(
        id       = ArtifactId("task_graph"),
        kind     = ArtifactKind.TASK_GRAPH,
        content  = {
            "tasks": [{
                "id":          "existing_implementation",
                "description": goal_description,
                "status":      "done",
            }],
        },
        metadata = {"file_path": ".antcrew/task_graph.json"},
    ))

AVAILABLE_ENGINE_CAPABILITIES = [
    "Architect",
    "TaskPlanner",
    "CodeGenerator",
    "CodeRegenerator",
    "DependencyInstaller",
    "DocGenerator",
    "HitlReviewer",
    "TestGenerator",
    "TestRunner",
    "BugFixer",
    "CodeReviewer",
    "ReviewFixer",
]

_GOAL_META_REL = Path(".antcrew") / "goal.json"


# ---------------------------------------------------------------------------
# Goal meta persistence (mirrors antcrew/cli/engine_cmd.py)
# ---------------------------------------------------------------------------

def _save_goal_meta(
    output: Path, description: str, tech: list[str],
    conditions: list[str], full: bool,
) -> None:
    meta = {"description": description, "tech": tech, "conditions": conditions, "full": full}
    meta_path = output / _GOAL_META_REL
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _load_goal_meta(output: Path) -> dict | None:
    meta_path = output / _GOAL_META_REL
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------

def _build_engine_registry(
    llm,
    *,
    capability_models: "dict[str, str] | None" = None,
    max_tasks: int = 12,
    parallel_workers: int = 5,
):
    from antcrew_engine.capabilities import (
        Architect, BugFixer, CodeGenerator, CodeRegenerator, CodeReviewer,
        DependencyInstaller, DocGenerator, ReviewFixer, TaskPlanner, TestGenerator, TestRunner,
    )
    from antcrew_engine.config import build_llm as _build_llm
    from antcrew_engine.engine import CapabilityRegistry

    _overrides = {
        name: _build_llm(model_str)
        for name, model_str in (capability_models or {}).items()
    }

    def _llm(cap_name: str):
        return _overrides.get(cap_name, llm)

    registry = CapabilityRegistry()
    registry.register(Architect(llm=_llm("architect")))
    registry.register(TaskPlanner(llm=_llm("task_planner"), max_tasks=max_tasks))
    registry.register(CodeGenerator(llm=_llm("code_generator"), parallel_workers=parallel_workers))
    registry.register(DependencyInstaller(llm=_llm("dependency_installer")))
    registry.register(TestGenerator(llm=_llm("test_generator")))
    registry.register(TestRunner())
    registry.register(BugFixer(llm=_llm("bug_fixer")))
    registry.register(CodeRegenerator(llm=_llm("code_regenerator")))
    registry.register(CodeReviewer(llm=_llm("code_reviewer")))
    registry.register(ReviewFixer(llm=_llm("review_fixer")))
    registry.register(DocGenerator(llm=_llm("doc_generator")))
    return registry


def _build_engine_validators():
    from antcrew_engine.capabilities.validators import (
        AllTasksCompletedValidator, CodeReviewedValidator, DependenciesInstalledValidator,
        DocumentationExistsValidator, TestsExistValidator, TestsPassValidator, artifact_validators,
    )
    return [
        *artifact_validators(
            ("requirements", "requirements_exists"),
            ("architecture", "architecture_exists"),
            ("task_graph",   "task_graph_exists"),
        ),
        AllTasksCompletedValidator(),
        DependenciesInstalledValidator(),
        TestsExistValidator(),
        TestsPassValidator(),
        CodeReviewedValidator(),
        DocumentationExistsValidator(),
    ]


def _build_engine_goal(description: str, tech: list[str], conditions: list[str], full: bool):
    from antcrew_engine.engine import (
        Condition, ConditionId, Constraints, DesiredProjectState, Goal,
    )

    default_conditions = [
        ("requirements_exists",    "requirements document written"),
        ("architecture_exists",    "architecture designed"),
        ("task_graph_exists",      "tasks planned"),
        ("implementation_exists",  "all tasks implemented"),
        ("dependencies_installed", "project dependencies installed"),
        ("tests_exist",            "test suite written"),
        ("tests_pass",             "tests passing"),
        ("code_reviewed",          "code reviewed and approved"),
        ("documentation_exists",   "README.md written"),
    ] if full else [
        ("requirements_exists", "requirements document written"),
        ("architecture_exists", "architecture designed"),
        ("task_graph_exists",   "tasks planned"),
    ]

    cond_set = (
        frozenset(Condition(ConditionId(c.strip()), c.strip()) for c in conditions)
        if conditions else
        frozenset(Condition(ConditionId(cid), desc) for cid, desc in default_conditions)
    )

    return Goal(
        description=description,
        desired_state=DesiredProjectState(cond_set),
        constraints=Constraints(tech_stack=tuple(tech)) if tech else Constraints(),
    )


# ---------------------------------------------------------------------------
# Sync executor — runs in thread pool, returns (success, cost_usd)
# ---------------------------------------------------------------------------

def _run_engine_sync(
    run_id: str,
    goal_description: str,
    model: str,
    tech: list[str],
    conditions: list[str],
    full: bool,
    max_iter: int,
    output_dir: Optional[Path],
    fix_attempts: int = 3,
    hitl_after: list[str] | None = None,
    source_dir: Optional[Path] = None,
    stop_event: Optional[_threading.Event] = None,
    hitl_max_rejections: int = 5,
    max_cost_usd: Optional[float] = None,
    capability_models: "dict[str, str] | None" = None,
    max_tasks: int = 12,
    parallel_workers: int = 5,
    byok_api_key: Optional[str] = None,
    byok_base_url: Optional[str] = None,
) -> tuple[bool, float]:
    from antcrew_engine.capabilities.hitl_reviewer import HitlReviewer
    from antcrew_engine.capabilities.validators import artifact_validators
    from antcrew_engine.config import build_llm
    from antcrew_engine.engine import ConditionId, EventLog, FilesystemStore, MemoryStore, Operator
    from antcrew_engine.engine.bus_bridge import EventBusBridge

    hitl_after = hitl_after or []

    llm = build_llm(model, prompt_caching=True, api_key=byok_api_key or None, base_url=byok_base_url or None)
    event_log = EventLog()
    EventBusBridge(event_log, run_id=run_id)

    goal = _build_engine_goal(goal_description, tech, conditions, full)
    store = FilesystemStore(output_dir) if output_dir else MemoryStore()
    registry = _build_engine_registry(
        llm,
        capability_models=capability_models,
        max_tasks=max_tasks,
        parallel_workers=parallel_workers,
    )
    validators = _build_engine_validators()

    # Load existing codebase if source_dir is provided
    if source_dir is not None:
        _load_existing_codebase(store, source_dir, goal_description)

    # Wire HITL reviewers.
    # Auto-detect the actual artifact ID and triggering condition from the registry
    # so --hitl-after architect correctly reads "architecture" (not "architect").
    import dataclasses as _dc
    for cap_name in hitl_after:
        source_exec = next((e for e in registry.all() if e.descriptor.name == cap_name), None)
        triggers_condition: str | None = None
        artifact_id:        str | None = None
        if source_exec:
            exists_conds = [
                str(c) for c in source_exec.descriptor.produces
                if str(c).endswith("_exists")
            ]
            if exists_conds:
                triggers_condition = exists_conds[0]
                artifact_id        = triggers_condition[: -len("_exists")]

        callback = _make_review_callback(run_id, cap_name, event_log)
        reviewer = HitlReviewer(
            reviewed_capability=cap_name,
            request_review=callback,
            artifact_id=artifact_id,
            triggers_condition=triggers_condition,
        )
        registry.register(reviewer)
        # Validator: '<cap>_approval' artifact → '<cap>_approved' condition
        validators += artifact_validators((f"{cap_name}_approval", f"{cap_name}_approved"))
        # Patch downstream executors to require '<cap>_approved' instead of the real exists cond
        _patch_downstream_needs(registry, cap_name, triggers_condition or f"{cap_name}_exists")

    total_limits = {"code_regenerator": 2, "review_fixer": 3}
    total_limits.update({f"hitl_{cap}": hitl_max_rejections for cap in hitl_after})

    operator = Operator(
        registry, validators, event_log,
        max_iterations=max_iter,
        retry_limits={"test_runner": 1, "bug_fixer": fix_attempts, "code_reviewer": 2},
        total_limits=total_limits,
        stop_event=stop_event,
        max_cost_usd=max_cost_usd,
    )

    success = False
    try:
        operator.run(store, goal)
        success = True
        if output_dir:
            _save_goal_meta(output_dir, goal_description, tech, conditions, full)
    except Exception as exc:
        log.error("engine runner: run %s failed: %s", run_id, exc)

    cost_usd = round(llm.get_usage_summary()["total_cost_usd"], 6)
    return success, cost_usd


# ---------------------------------------------------------------------------
# Async dispatch
# ---------------------------------------------------------------------------

async def _check_engine_workspace(workspace_id: int) -> None:
    """Block engine runs when the workspace subscription is cancelled/unpaid or over budget.

    Mirrors runner._check_workspace_budget but kept local so engine_runner has
    no coupling to runner internals — both modules import BLOCKED_STATUSES directly.
    """
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _db_engine
    from app.models.run import Workspace
    from app.services.billing import BLOCKED_STATUSES

    async with AsyncSession(_db_engine, expire_on_commit=False) as session:
        ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
        if ws is None:
            return
        if ws.subscription_status in BLOCKED_STATUSES:
            raise ValueError(
                f"Workspace subscription is '{ws.subscription_status}'. "
                "Please update your billing details to continue using the engine."
            )
        if ws.max_cost_usd is not None and ws.total_cost_usd >= ws.max_cost_usd:
            raise ValueError(
                f"Workspace budget exhausted: ${ws.total_cost_usd:.4f} spent of "
                f"${ws.max_cost_usd:.2f} limit. Update the workspace budget to continue."
            )


async def dispatch_engine(
    goal: str,
    *,
    model: str = "claude",
    capability_models: "dict[str, str] | None" = None,
    tech: list[str] | None = None,
    conditions: list[str] | None = None,
    full: bool = True,
    max_iter: int = 50,
    fix_attempts: int = 3,
    hitl_after: list[str] | None = None,
    hitl_max_rejections: int = 5,
    max_cost_usd: Optional[float] = None,
    output_dir: Optional[Path] = None,
    source_dir: Optional[Path] = None,
    resume: bool = False,
    created_by: Optional[str] = None,
    workspace_id: Optional[int] = None,
    max_tasks: int = 12,
    parallel_workers: int = 5,
) -> str:
    """Start a capability-driven engine run in the background.

    Emits pipeline.start synchronously before returning so the DB row is created
    immediately. agent.start / agent.end arrive via EventBusBridge as the engine
    progresses. pipeline.end with the real LLM cost is emitted once the run finishes.

    When resume=True and output_dir is set, loads the goal from .antcrew/goal.json
    in output_dir (ignoring the goal argument if goal.json exists), and the
    FilesystemStore picks up previously produced artifacts automatically.

    Returns run_id.
    """
    tech = tech or []
    conditions = conditions or []
    hitl_after = hitl_after or []

    if workspace_id is not None:
        await _check_engine_workspace(workspace_id)

    # Resume: load goal from persisted metadata, let goal arg override description.
    if resume and output_dir is not None:
        meta = _load_goal_meta(output_dir)
        if meta:
            if not goal:
                goal = meta["description"]
            tech = tech or meta.get("tech", [])
            conditions = conditions or meta.get("conditions", [])
            full = meta.get("full", full)
            log.info("engine runner: resuming from %s — goal: %s", output_dir, goal)

    if not goal:
        raise ValueError("goal is required (or use resume=True with a prior output_dir)")

    # Fetch BYOK key if this workspace uses customer-supplied LLM keys
    _byok_api_key: Optional[str] = None
    _byok_base_url: Optional[str] = None
    if workspace_id is not None:
        from sqlmodel import select as _sel
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.core.database import engine as _db_engine
        from app.models.run import Workspace as _WS
        async with AsyncSession(_db_engine, expire_on_commit=False) as _sess:
            _ws = (await _sess.exec(_sel(_WS).where(_WS.id == workspace_id))).first()
            if _ws and getattr(_ws, "llm_key_mode", "managed") == "byok":
                from app.core.byok import get_workspace_llm_key_for_model
                _byok = await get_workspace_llm_key_for_model(_sess, workspace_id, model)
                if _byok:
                    _byok_api_key = _byok.key
                    _byok_base_url = _byok.base_url

    run_id = new_run_id()
    stop_event = _threading.Event()
    _cancel_events[run_id] = stop_event

    # Emit pipeline.start now so the listener creates the Run DB row before we return.
    bus.emit(BusEvent(
        "pipeline.start",
        {
            "request": goal,
            "team": "engine",
            "run_id": run_id,
            "thread_id": "default",
        },
        run_id=run_id,
        thread_id="default",
    ))

    loop = asyncio.get_running_loop()

    async def _bg() -> None:
        success, cost_usd = False, 0.0
        try:
            fn = functools.partial(
                _run_engine_sync,
                run_id, goal, model, tech, conditions, full, max_iter, output_dir,
                fix_attempts, hitl_after, source_dir, stop_event, hitl_max_rejections,
                max_cost_usd, capability_models, max_tasks, parallel_workers,
                _byok_api_key, _byok_base_url,
            )
            success, cost_usd = await loop.run_in_executor(_executor, fn)
        except Exception as exc:
            log.error("engine runner: background task for %s raised: %s", run_id, exc)
        finally:
            _cancel_events.pop(run_id, None)
            bus.emit(BusEvent(
                "pipeline.end",
                {
                    "success": success,
                    "cost_usd": cost_usd,
                    "run_id": run_id,
                    "thread_id": "default",
                },
                run_id=run_id,
                thread_id="default",
            ))
            # Persist compact state so /runs/{id}/artifacts can serve engine files.
            await _store_engine_state(run_id, goal, output_dir)
            # Update workspace budget totals (mirrors runner.dispatch behaviour).
            if workspace_id is not None:
                from app.services.runner import _mark_workspace_budget_status
                await _mark_workspace_budget_status(workspace_id)

    asyncio.ensure_future(_bg())

    if created_by or workspace_id is not None:
        asyncio.ensure_future(_set_run_attribution(run_id, created_by, workspace_id))

    return run_id


async def _set_run_attribution(
    run_id: str,
    created_by: Optional[str],
    workspace_id: Optional[int],
) -> None:
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _db_engine
    from app.models.run import Run

    try:
        async with AsyncSession(_db_engine, expire_on_commit=False) as session:
            result = await session.exec(select(Run).where(Run.run_id == run_id))
            run = result.first()
            if run:
                if created_by:
                    run.created_by = created_by
                if workspace_id is not None:
                    run.workspace_id = workspace_id
                session.add(run)
                await session.commit()
    except Exception as exc:
        log.warning("engine runner: failed to set attribution for %s: %s", run_id, exc)


async def _store_engine_state(
    run_id: str,
    goal: str,
    output_dir: Optional[Path],
) -> None:
    """Persist a compact state dict to run.state so /artifacts endpoints can serve files."""
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _db_engine
    from app.models.run import Run

    state: dict = {
        "engine": True,
        "goal": goal,
        "output_dir": str(output_dir) if output_dir else None,
    }
    try:
        async with AsyncSession(_db_engine, expire_on_commit=False) as session:
            run = (await session.exec(select(Run).where(Run.run_id == run_id))).first()
            if run:
                run.state = state
                session.add(run)
                await session.commit()
    except Exception as exc:
        log.warning("engine runner: failed to store state for %s: %s", run_id, exc)


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)
