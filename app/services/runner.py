"""Background team runner — dispatches antcrew pipelines from the API.

Flow:
  POST /run → dispatch() → _run_sync() in thread pool → _store_result()

HITL flow: if force_hitl=True (from `POST /run { "hitl": true }`) OR any agent has
approval_required=True, dispatch injects PlatformChannel and calls run_interactive().
The channel blocks the executor thread on a concurrent.futures.Future until
POST /reviews/:id resolves it.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from antcrew.core.events import bus
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import engine
from app.core.channel import PlatformChannel
from app.services.runs import upsert_tickets_from_run

log = logging.getLogger(__name__)

_MAX_WORKERS = int(os.environ.get("ANTCREW_WORKERS", "4"))
_DISPATCH_TIMEOUT = float(os.environ.get("ANTCREW_DISPATCH_TIMEOUT", "10"))
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="antcrew-runner")

# Per-workspace asyncio locks used to serialise budget check + budget update pairs.
# Prevents two concurrent dispatch() calls from both reading the same total_cost_usd
# before either run has committed its cost (TOCTOU on the budget gate).
# Only effective within a single process — PostgreSQL FOR UPDATE covers multi-process.
_budget_locks: dict[int, asyncio.Lock] = {}


def _get_budget_lock(workspace_id: int) -> asyncio.Lock:
    if workspace_id not in _budget_locks:
        _budget_locks[workspace_id] = asyncio.Lock()
    return _budget_locks[workspace_id]

def _build_team_registry() -> dict[str, tuple[str, str]]:
    """Merge built-in teams with any custom teams from ANTCREW_TEAMS env var.

    ANTCREW_TEAMS format (comma-separated): "my.module:MyTeam,other.module:OtherTeam"
    Custom teams shadow built-ins with the same name.

    CustomTeam is intentionally excluded from the default registry — it requires
    Python-level agent composition and is only useful when configured via ANTCREW_TEAMS.
    """
    registry: dict[str, tuple[str, str]] = {
        "DevTeam":       ("antcrew.teams.dev_team",        "DevTeam"),
        "FullStackTeam": ("antcrew.teams.fullstack_team",  "FullStackTeam"),
        "ResearchTeam":  ("antcrew.teams.research_team",   "ResearchTeam"),
        "ContentTeam":   ("antcrew.teams.content_team",    "ContentTeam"),
        "FeatureTeam":   ("antcrew.agents.feature_agent",  "FeatureTeam"),
    }
    extra = os.environ.get("ANTCREW_TEAMS", "").strip()
    for entry in extra.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            module_path, class_name = entry.rsplit(":", 1)
            registry[class_name] = (module_path.strip(), class_name.strip())
        except ValueError:
            log.warning("runner: invalid ANTCREW_TEAMS entry %r — expected 'module.path:ClassName'", entry)
    return registry


_TEAM_REGISTRY = _build_team_registry()
AVAILABLE_TEAMS = list(_TEAM_REGISTRY)
# Includes "custom" (POST /run/pipeline) for discoverability in GET /run/teams
ALL_PIPELINE_TYPES = AVAILABLE_TEAMS + ["custom", "engine"]


def _make_team(
    team_name: str,
    max_cost_usd: Optional[float] = None,
    model: str = "",
    byok_api_key: Optional[str] = None,
):
    if team_name == "custom":
        raise ValueError(
            "Use POST /run/pipeline (dispatch_custom) for custom pipelines — "
            "'custom' cannot be dispatched via _make_team."
        )
    if team_name not in _TEAM_REGISTRY:
        raise ValueError(f"Unknown team {team_name!r}. Available: {AVAILABLE_TEAMS}")
    module_path, class_name = _TEAM_REGISTRY[team_name]
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    kwargs: dict = {}
    if model or byok_api_key:
        from antcrew.config import build_llm as _build_llm
        llm = _build_llm(model or "claude", api_key=byok_api_key)
        if max_cost_usd is not None:
            llm.max_cost_usd = max_cost_usd
        kwargs["llm"] = llm
    elif max_cost_usd is not None:
        kwargs["max_cost_usd"] = max_cost_usd
    try:
        return cls(**kwargs)
    except TypeError as exc:
        if "llm" in kwargs:
            log.warning(
                "_make_team: %s does not accept llm= kwarg (%s) — falling back to default LLM",
                class_name, exc,
            )
            kwargs.pop("llm")
            if max_cost_usd is not None:
                kwargs["max_cost_usd"] = max_cost_usd
            return cls(**kwargs)
        raise


_REPO_SKIP = {
    '.git', '__pycache__', 'node_modules', '.venv', 'venv', 'dist', 'build',
    '.mypy_cache', '.pytest_cache', '.tox', 'coverage', '.eggs',
}
_REPO_SRC_EXTS = {
    '.py', '.ts', '.tsx', '.js', '.jsx', '.go', '.rs', '.java', '.cs',
    '.rb', '.php', '.swift', '.kt', '.cpp', '.c', '.h', '.yaml', '.yml',
    '.toml', '.json', '.md',
}
_REPO_MAX_FILE_CHARS = 6000
_REPO_MAX_FILES = 25
_REPO_MAX_CONTEXT_CHARS = 50_000
_REPO_CLONE_TIMEOUT_S = 120


def _build_repo_context(repo_dir: Path) -> str:
    """Walk a cloned repo and return a compact context string for LLM injection."""
    tree_lines = ["## Repository file tree\n```"]
    src_files: list[Path] = []

    for path in sorted(repo_dir.rglob("*")):
        rel = path.relative_to(repo_dir)
        parts = rel.parts
        if any(p in _REPO_SKIP or p.startswith('.') for p in parts):
            continue
        indent = "  " * (len(parts) - 1)
        tree_lines.append(f"{indent}{path.name}{'/' if path.is_dir() else ''}")
        if path.is_file() and path.suffix.lower() in _REPO_SRC_EXTS:
            src_files.append(path)

    tree_lines.append("```\n")
    context_parts = ["\n".join(tree_lines), "## Key source files\n"]
    total_chars = sum(len(p) for p in context_parts)

    for fp in src_files[:_REPO_MAX_FILES]:
        if total_chars >= _REPO_MAX_CONTEXT_CHARS:
            context_parts.append("... [context truncated — too many files]\n")
            break
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(content) > _REPO_MAX_FILE_CHARS:
            content = content[:_REPO_MAX_FILE_CHARS] + "\n... [truncated]"
        rel = fp.relative_to(repo_dir)
        snippet = f"### {rel}\n```{fp.suffix.lstrip('.')}\n{content}\n```\n"
        context_parts.append(snippet)
        total_chars += len(snippet)

    return "\n".join(context_parts)


async def _inject_repo_context(
    repo_url: str, request: str, repo_token: Optional[str] = None
) -> tuple[Path, str]:
    """Clone *repo_url* (depth=1) to a temp dir and prepend its context to *request*.

    Returns (tmp_dir, augmented_request). Caller must clean up tmp_dir.
    Raises RuntimeError on clone failure or if repo_url targets an internal host.
    """
    from app.core.security import validate_external_url
    try:
        validate_external_url(repo_url)
    except ValueError as exc:
        raise RuntimeError(f"Blocked repository URL: {exc}") from exc

    clone_url = repo_url
    if repo_token and repo_url.startswith("https://"):
        # Inject token into URL for private repos: https://token@github.com/...
        clone_url = repo_url.replace("https://", f"https://{repo_token}@", 1)

    tmp = Path(tempfile.mkdtemp(prefix="antcrew-repo-"))
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", "--single-branch", clone_url, str(tmp),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_REPO_CLONE_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"git clone timed out after {_REPO_CLONE_TIMEOUT_S}s: {repo_url}")

        if proc.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            err = (stderr or b"").decode(errors="replace")[:400]
            raise RuntimeError(f"git clone failed (exit {proc.returncode}): {err}")

        context = _build_repo_context(tmp)
        augmented = (
            f"[Repository context cloned from {repo_url}]\n\n"
            f"{context}\n"
            f"---\n\n"
            f"{request}"
        )
        return tmp, augmented
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _serialize_state(state: dict) -> dict:
    """Convert a LangGraph state dict to a JSON-serializable form."""
    from pydantic import BaseModel

    def _v(val):
        if isinstance(val, BaseModel):
            return val.model_dump()
        if isinstance(val, list):
            return [_v(item) for item in val]
        if isinstance(val, dict):
            return {k: _v(v) for k, v in val.items()}
        return val

    return {k: _v(v) for k, v in state.items() if not k.startswith("_")}


def _run_sync(
    team_name: str,
    request: str,
    thread_id: str,
    max_cost_usd: Optional[float],
    platform_channel: PlatformChannel,
    force_hitl: bool,
    byok_api_key: Optional[str] = None,
):
    """Run a team synchronously in the executor thread.

    When force_hitl=True all agents get the platform channel and run_interactive is used.
    Otherwise only agents already marked approval_required=True get the channel.
    """
    team = _make_team(team_name, max_cost_usd=max_cost_usd, byok_api_key=byok_api_key)
    all_agents = list(getattr(team, "_agents", {}).values())

    if force_hitl:
        # Force HITL on all agents regardless of their approval_required flag
        for agent in all_agents:
            if not getattr(agent, "channel", None):
                agent.channel = platform_channel
            agent.approval_required = True
        return team.run_interactive(request, thread_id=thread_id)

    hitl_agents = [a for a in all_agents if getattr(a, "approval_required", False)]
    if hitl_agents:
        for agent in hitl_agents:
            if not getattr(agent, "channel", None):
                agent.channel = platform_channel
        return team.run_interactive(request, thread_id=thread_id)

    return team.run(request, thread_id=thread_id)


async def _set_run_attribution(
    run_id: str, created_by: Optional[str], workspace_id: Optional[int]
) -> None:
    from sqlmodel import select
    from app.models.run import Run
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
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
        log.warning("runner: failed to set attribution for run %s: %s", run_id, exc)


async def _store_result(result) -> None:
    """Persist the full run state and upsert tickets. Retries up to 3 times.

    Handles both RunResult (from team.run()) and plain dict (from team.run_interactive()).
    """
    from sqlmodel import select
    from app.models.run import Run

    if isinstance(result, dict):
        # run_interactive() returns final_state = app.get_state(config).values
        run_id = result.get("_run_id")
        state_dict = _serialize_state(result)
        raw_state = result
    elif hasattr(result, "state") and isinstance(result.state, dict):
        run_id = result.state.get("_run_id")
        state_dict = result.to_dict() if hasattr(result, "to_dict") else result.state
        raw_state = result.state
    else:
        return

    if not run_id:
        return

    for attempt in range(3):
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                stmt = select(Run).where(Run.run_id == run_id)
                db_run = (await session.exec(stmt)).first()
                if db_run:
                    db_run.state = state_dict
                    session.add(db_run)
                await upsert_tickets_from_run(session, run_id, raw_state)
                await session.commit()  # single commit for state + tickets
            return
        except Exception as exc:
            if attempt == 2:
                log.error("runner: failed to store result for run %s after 3 attempts: %s", run_id, exc)
            else:
                await asyncio.sleep(2 ** attempt)


async def _check_workspace_budget(workspace_id: int) -> None:
    """Raise ValueError if the workspace has exhausted its budget or subscription is suspended.

    Must be called while holding _get_budget_lock(workspace_id) to prevent TOCTOU races
    where two concurrent dispatch() calls both read the same total_cost_usd before either
    run has committed its cost.  The lock serialises check↔mark pairs within a process;
    FOR UPDATE serialises across multiple processes on PostgreSQL.

    Residual risk: in-flight runs whose cost has not yet been committed by
    _mark_workspace_budget_status() are invisible to this check regardless of locking.
    A reservation column would close that gap; this implementation trades full accuracy
    for simplicity while making concurrent-dispatch overruns impossible.
    """
    from sqlmodel import select
    from app.models.run import Workspace
    from app.services.billing import BLOCKED_STATUSES

    async with AsyncSession(engine, expire_on_commit=False) as sess:
        # FOR UPDATE: row-level lock held until session closes.
        # Prevents two concurrent budget checks from reading the same total_cost_usd
        # on PostgreSQL (multi-process).  On SQLite/aiosqlite this is a no-op —
        # the asyncio lock in dispatch() covers that case.
        try:
            stmt = select(Workspace).where(Workspace.id == workspace_id).with_for_update()
            ws = (await sess.exec(stmt)).first()
        except Exception:
            ws = (await sess.exec(select(Workspace).where(Workspace.id == workspace_id))).first()

        if ws is None:
            return
        if ws.subscription_status in BLOCKED_STATUSES:
            raise ValueError(
                f"Workspace subscription is '{ws.subscription_status}'. "
                "Please update your billing details to continue running pipelines."
            )
        if ws.max_cost_usd is None:
            return
        if ws.total_cost_usd >= ws.max_cost_usd:
            raise ValueError(
                f"Workspace budget exhausted: ${ws.total_cost_usd:.4f} spent of "
                f"${ws.max_cost_usd:.2f} limit. Update the workspace budget to continue."
            )


async def _mark_workspace_budget_status(workspace_id: int) -> None:
    """After a run completes, recompute total spend via SQL SUM and update cached field.

    Acquires _get_budget_lock(workspace_id) so that a concurrent dispatch() cannot
    pass _check_workspace_budget() while this write is in-flight — the pair
    (check, mark) is always mutually exclusive within the same process.
    """
    from sqlmodel import select
    from app.models.run import Workspace, Run

    try:
        async with _get_budget_lock(workspace_id):
            async with AsyncSession(engine, expire_on_commit=False) as session:
                ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
                if ws is None:
                    return
                costs = await session.exec(
                    select(Run.cost_usd).where(Run.workspace_id == workspace_id)
                )
                total = sum(c or 0.0 for c in costs.all())
                ws.total_cost_usd = total
                if ws.max_cost_usd is not None and total >= ws.max_cost_usd:
                    log.warning(
                        "runner: workspace %d budget exhausted ($%.4f of $%.2f limit)",
                        workspace_id, total, ws.max_cost_usd,
                    )
                session.add(ws)
                await session.commit()
    except Exception as exc:
        log.warning("runner: failed to update budget for workspace %d: %s", workspace_id, exc)


async def dispatch(
    team_name: str,
    request: str,
    thread_id: str = "default",
    *,
    max_cost_usd: Optional[float] = None,
    created_by: Optional[str] = None,
    workspace_id: Optional[int] = None,
    force_hitl: bool = False,
    repo_url: Optional[str] = None,
    repo_token: Optional[str] = None,
) -> Optional[str]:
    """Start a team run in the background. Returns run_id once pipeline.start fires.

    force_hitl=True injects PlatformChannel into ALL agents for this run,
    regardless of their approval_required setting.

    repo_url, if set, is cloned depth=1 and its file tree + source files are
    prepended to the request so agents have codebase context. repo_token can be
    a GitHub/GitLab personal access token for private repos (injected into the
    HTTPS URL — never stored or logged).
    """
    if workspace_id is not None:
        async with _get_budget_lock(workspace_id):
            await _check_workspace_budget(workspace_id)

    # Look up per-workspace HITL timeout and BYOK key in a single DB call
    _hitl_timeout: Optional[float] = None
    _byok_api_key: Optional[str] = None
    if workspace_id is not None:
        from sqlmodel import select as _sel
        from app.models.run import Workspace as _WS
        async with AsyncSession(engine, expire_on_commit=False) as _sess:
            _ws = (await _sess.exec(_sel(_WS).where(_WS.id == workspace_id))).first()
            if _ws:
                if _ws.hitl_timeout_s is not None:
                    _hitl_timeout = _ws.hitl_timeout_s
                if getattr(_ws, "llm_key_mode", "managed") == "byok":
                    from app.core.byok import get_workspace_llm_key
                    _byok_api_key = await get_workspace_llm_key(_sess, workspace_id, "anthropic")

    # Clone repo and inject context before dispatching to the thread pool.
    _tmp_repo_dir: Optional[Path] = None
    effective_request = request
    if repo_url:
        try:
            _tmp_repo_dir, effective_request = await _inject_repo_context(
                repo_url, request, repo_token=repo_token
            )
            log.info("runner: injected repo context from %s (%d chars)", repo_url, len(effective_request))
        except Exception as exc:
            log.warning("runner: repo clone failed for %s — running without context: %s", repo_url, exc)

    loop = asyncio.get_running_loop()
    run_id_future: asyncio.Future[str] = loop.create_future()
    platform_channel = PlatformChannel(timeout_s=_hitl_timeout)

    def _on_pipeline_start(event) -> None:
        if event.run_id and not run_id_future.done():
            platform_channel.set_run_id(event.run_id)
            loop.call_soon_threadsafe(run_id_future.set_result, event.run_id)

    bus.subscribe("pipeline.start", _on_pipeline_start)

    async def _bg() -> None:
        try:
            fn = functools.partial(
                _run_sync, team_name, effective_request, thread_id,
                max_cost_usd, platform_channel, force_hitl, _byok_api_key,
            )
            result = await loop.run_in_executor(_executor, fn)
            await _store_result(result)
            if workspace_id is not None:
                await _mark_workspace_budget_status(workspace_id)
        except Exception as exc:
            log.error("runner: %s failed: %s", team_name, exc)
            if not run_id_future.done():
                loop.call_soon_threadsafe(run_id_future.set_result, None)
        finally:
            bus.unsubscribe("pipeline.start", _on_pipeline_start)
            if _tmp_repo_dir is not None:
                shutil.rmtree(_tmp_repo_dir, ignore_errors=True)

    asyncio.ensure_future(_bg())

    try:
        run_id = await asyncio.wait_for(asyncio.shield(run_id_future), timeout=_DISPATCH_TIMEOUT)
        if (created_by or workspace_id is not None) and run_id:
            # Await attribution before returning so workspace_id is set before the
            # 202 response reaches the client — eliminates the race on GET /runs/.
            await _set_run_attribution(run_id, created_by, workspace_id)
        return run_id
    except asyncio.TimeoutError:
        log.warning("runner: pipeline.start not received within %.0f s for %s", _DISPATCH_TIMEOUT, team_name)
        return None


def _validate_custom_dag(steps: list[dict]) -> None:
    """Validate that each step's input_key is available at that point in the pipeline.

    Available keys start with {"request"} and grow as each step's output_key is
    produced. Raises ValueError on the first missing key so errors surface early,
    before any LLM call is made.
    """
    available: set[str] = {"request"}
    for i, step in enumerate(steps):
        name = step.get("name") or f"step[{i}]"
        input_key = step.get("input_key") or "request"
        output_key = step.get("output_key") or ""
        if input_key not in available:
            raise ValueError(
                f"Step {i} ({name!r}): input_key {input_key!r} is not available at this point. "
                f"Available keys: {sorted(available)}. "
                "Check that a prior step produces this key via its output_key."
            )
        if output_key:
            available.add(output_key)


def _run_custom_sync(
    steps: list[dict],
    request: str,
    thread_id: str,
    max_cost_usd: Optional[float],
    platform_channel: PlatformChannel,
    force_hitl: bool,
    model: str = "claude",
    byok_api_key: Optional[str] = None,
):
    """Build a CustomTeam from inline TemplateAgent configs and run it."""
    from antcrew.agents.template_agent import TemplateAgent
    from antcrew.teams.custom_team import CustomTeam
    from antcrew.config import build_llm

    llm = build_llm(model or "claude", api_key=byok_api_key)
    if max_cost_usd is not None:
        llm.max_cost_usd = max_cost_usd

    agents = [TemplateAgent(step, llm=llm) for step in steps]
    team = CustomTeam(steps=agents, llm=llm)

    # CustomTeam._agents is a flat list; regular teams use a dict — handle both
    _raw = getattr(team, "_agents", [])
    all_agents = list(_raw.values()) if isinstance(_raw, dict) else list(_raw)

    if force_hitl:
        for agent in all_agents:
            if not getattr(agent, "channel", None):
                agent.channel = platform_channel
            agent.approval_required = True
        return team.run_interactive(request, thread_id=thread_id)

    hitl_agents = [a for a in all_agents if getattr(a, "approval_required", False)]
    if hitl_agents:
        for agent in hitl_agents:
            if not getattr(agent, "channel", None):
                agent.channel = platform_channel
        return team.run_interactive(request, thread_id=thread_id)

    return team.run(request, thread_id=thread_id)


async def dispatch_custom(
    steps: list[dict],
    request: str,
    thread_id: str = "default",
    *,
    max_cost_usd: Optional[float] = None,
    created_by: Optional[str] = None,
    workspace_id: Optional[int] = None,
    force_hitl: bool = False,
    model: str = "claude",
) -> Optional[str]:
    """Dispatch a custom pipeline defined by a list of TemplateAgent step configs."""
    try:
        from antcrew.agents.template_agent import TemplateAgent  # noqa: F401
        from antcrew.teams.custom_team import CustomTeam  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            f"CustomTeam or TemplateAgent not available: {exc}. "
            "Ensure antcrew >= 0.14 is installed."
        ) from exc

    # Validate DAG before touching the thread pool
    _validate_custom_dag(steps)

    if workspace_id is not None:
        async with _get_budget_lock(workspace_id):
            await _check_workspace_budget(workspace_id)

    # Look up per-workspace HITL timeout and BYOK key in a single DB call
    _hitl_timeout: Optional[float] = None
    _byok_api_key: Optional[str] = None
    if workspace_id is not None:
        from sqlmodel import select as _sel
        from app.models.run import Workspace as _WS
        async with AsyncSession(engine, expire_on_commit=False) as _sess:
            _ws = (await _sess.exec(_sel(_WS).where(_WS.id == workspace_id))).first()
            if _ws:
                if _ws.hitl_timeout_s is not None:
                    _hitl_timeout = _ws.hitl_timeout_s
                if getattr(_ws, "llm_key_mode", "managed") == "byok":
                    _provider = "openai" if (
                        model.startswith("gpt") or model.startswith("o1")
                        or model.startswith("o3") or model.startswith("openai:")
                    ) else "anthropic"
                    from app.core.byok import get_workspace_llm_key
                    _byok_api_key = await get_workspace_llm_key(_sess, workspace_id, _provider)

    loop = asyncio.get_running_loop()
    run_id_future: asyncio.Future[str] = loop.create_future()
    platform_channel = PlatformChannel(timeout_s=_hitl_timeout)

    def _on_pipeline_start(event) -> None:
        if event.run_id and not run_id_future.done():
            platform_channel.set_run_id(event.run_id)
            loop.call_soon_threadsafe(run_id_future.set_result, event.run_id)

    bus.subscribe("pipeline.start", _on_pipeline_start)

    async def _bg() -> None:
        try:
            fn = functools.partial(
                _run_custom_sync, steps, request, thread_id,
                max_cost_usd, platform_channel, force_hitl, model, _byok_api_key,
            )
            result = await loop.run_in_executor(_executor, fn)
            await _store_result(result)
            if workspace_id is not None:
                await _mark_workspace_budget_status(workspace_id)
        except Exception as exc:
            log.error("runner: custom pipeline failed: %s", exc)
            if not run_id_future.done():
                loop.call_soon_threadsafe(run_id_future.set_result, None)
        finally:
            bus.unsubscribe("pipeline.start", _on_pipeline_start)

    asyncio.ensure_future(_bg())

    try:
        run_id = await asyncio.wait_for(asyncio.shield(run_id_future), timeout=_DISPATCH_TIMEOUT)
        if (created_by or workspace_id is not None) and run_id:
            await _set_run_attribution(run_id, created_by, workspace_id)
        return run_id
    except asyncio.TimeoutError:
        log.warning("runner: custom pipeline.start not received within %.0f s", _DISPATCH_TIMEOUT)
        return None


def shutdown() -> None:
    """Shutdown the thread pool. Call during app teardown."""
    _executor.shutdown(wait=False, cancel_futures=True)
