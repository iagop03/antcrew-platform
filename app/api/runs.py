"""REST endpoints for pipeline runs."""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role
from app.core.database import get_session
from app.core.exceptions import (
    RunNotFoundError, RunNotAccessibleError, RunNotRunningError, StateNotAvailableError,
)
from app.models.run import Run, Event as DBEvent
from app.services.runs import cancel_run, get_run, get_run_events, get_run_tickets, get_run_stats, list_runs

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(require_api_key)],
)


class RunUpload(BaseModel):
    """Pre-computed run result from a local `antcrew run --push-to` execution."""
    team: str
    request: str
    thread_id: str = "default"
    cost_usd: float = 0.0
    duration_s: Optional[float] = None
    state: Optional[dict] = None


def _assert_run_access(run: Run, ctx: WorkspaceContext) -> None:
    """Raise 403 if the API key is workspace-scoped and doesn't own this run."""
    from app.core.auth import ws_accessible
    if ctx.workspace_ids is not None and not ws_accessible(run.workspace_id, ctx):
        raise RunNotAccessibleError()


@router.post("/upload", status_code=201, response_model=Run,
             dependencies=[Depends(require_role("admin", "write"))])
async def upload_run(
    body: RunUpload,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Store a local CLI run result on the platform dashboard.

    Called by ``antcrew run --push-to <platform-url>`` after a successful local run.
    The run appears in the dashboard immediately with status ``success``.
    Tickets in ``state.tickets`` are upserted via the normal ticket pipeline.
    """
    from app.services.runner import AVAILABLE_TEAMS
    from app.services.runs import upsert_tickets_from_run

    if body.team not in AVAILABLE_TEAMS:
        raise HTTPException(422, f"Unknown team {body.team!r}. Available: {AVAILABLE_TEAMS}")
    if not body.request.strip():
        raise HTTPException(422, "request must not be empty")

    run = Run(
        run_id=str(uuid.uuid4()),
        thread_id=body.thread_id,
        team=body.team,
        request=body.request.strip(),
        status="success",
        cost_usd=body.cost_usd,
        duration_s=body.duration_s,
        state=body.state,
        workspace_id=ctx.workspace_id,
        created_by=ctx.created_by,
        finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    if body.state:
        await upsert_tickets_from_run(session, run.run_id, body.state, workspace_id=run.workspace_id)
        await session.commit()

    return run


@router.get("/stats")
async def stats(
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Aggregate counts and total cost. Scoped to the API key's workspace if set."""
    return await get_run_stats(session, workspace_ids=ctx.workspace_ids)


@router.get("/", response_model=list[Run])
async def index(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    since_id: Optional[int] = Query(None, description="Cursor: return runs with id < since_id"),
    team: Optional[str] = None,
    team_prefix: Optional[str] = Query(None, description="Filter by team name prefix, e.g. 'pipeline:'"),
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    return await list_runs(
        session, limit=limit, offset=offset, team=team, team_prefix=team_prefix,
        status=status, since_id=since_id, workspace_ids=ctx.workspace_ids,
    )


@router.get("/compare-artifacts")
async def compare_artifacts(
    run_a: str = Query(..., description="First run_id"),
    run_b: str = Query(..., description="Second run_id"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return a file-level diff between artifacts of two engine runs.

    For each file present in either run, returns: status (added/removed/changed/unchanged),
    lines_added, lines_removed. File content is NOT returned — only statistics.
    Supports MemoryStore runs (state.code_artifacts) and FilesystemStore runs (output_dir).
    """
    import difflib

    run_a_obj = await get_run(session, run_a)
    run_b_obj = await get_run(session, run_b)
    if not run_a_obj:
        raise RunNotFoundError(run_a)
    if not run_b_obj:
        raise RunNotFoundError(run_b)
    _assert_run_access(run_a_obj, ctx)
    _assert_run_access(run_b_obj, ctx)

    def _collect_artifacts(run: "Run") -> dict[str, str]:
        """Return {file_path: content} for all artifacts in a run."""
        result: dict[str, str] = {}
        out_dir = _engine_output_dir(run)
        if out_dir and out_dir.exists():
            for p in sorted(out_dir.rglob("*")):
                if p.is_file() and not any(part in _ENGINE_SKIP_DIRS for part in p.parts):
                    try:
                        result[str(p.relative_to(out_dir))] = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass
        elif run.state:
            s = run.state
            for key in ("code_artifacts", "test_artifacts", "doc_artifacts"):
                for art in s.get(key) or []:
                    fp = art.get("file_path", "")
                    if fp:
                        result[fp] = art.get("content", "")
        return result

    arts_a = _collect_artifacts(run_a_obj)
    arts_b = _collect_artifacts(run_b_obj)
    all_paths = sorted(set(arts_a) | set(arts_b))

    diff_entries = []
    for path in all_paths:
        if path in arts_a and path not in arts_b:
            status = "removed"
            lines_added = lines_removed = 0
        elif path not in arts_a and path in arts_b:
            status = "added"
            lines_added = len(arts_b[path].splitlines())
            lines_removed = 0
        else:
            lines_a = arts_a[path].splitlines(keepends=True)
            lines_b = arts_b[path].splitlines(keepends=True)
            if lines_a == lines_b:
                status = "unchanged"
                lines_added = lines_removed = 0
            else:
                status = "changed"
                opcodes = difflib.SequenceMatcher(None, lines_a, lines_b).get_opcodes()
                lines_added = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag in ("insert", "replace"))
                lines_removed = sum(i2 - i1 for tag, i1, i2, _, _ in opcodes if tag in ("delete", "replace"))
        diff_entries.append({
            "file_path": path,
            "status": status,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
        })

    changed = sum(1 for e in diff_entries if e["status"] == "changed")
    added = sum(1 for e in diff_entries if e["status"] == "added")
    removed = sum(1 for e in diff_entries if e["status"] == "removed")
    return {
        "run_a": run_a,
        "run_b": run_b,
        "summary": {"changed": changed, "added": added, "removed": removed,
                    "unchanged": len(diff_entries) - changed - added - removed},
        "files": diff_entries,
    }


@router.get("/{run_id}", response_model=Run)
async def detail(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    return run


@router.post("/{run_id}/cancel", response_model=Run,
             dependencies=[Depends(require_role("admin", "write"))])
async def cancel(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Mark a running run as cancelled. The background thread continues until it finishes
    naturally — this only updates the DB status immediately."""
    existing = await get_run(session, run_id)
    if not existing:
        raise RunNotFoundError(run_id)
    _assert_run_access(existing, ctx)
    run = await cancel_run(session, run_id)
    if run is None:
        raise RunNotRunningError(run_id, existing.status)
    return run


@router.get("/{run_id}/state")
async def state(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    """Return the full serialized RunResult state for a completed run."""
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    if run.state is None:
        raise StateNotAvailableError(run_id, run.status)
    return run.state


@router.get("/{run_id}/tickets")
async def tickets(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Return tickets produced by a specific run."""
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    return await get_run_tickets(session, run_id)


_ENGINE_SKIP_DIRS = {".antcrew"}


def _engine_output_dir(run: "Run") -> Path | None:
    """Return the engine output_dir path if the run has one stored."""
    if run.team != "engine" or not run.state:
        return None
    d = run.state.get("output_dir")
    return Path(d) if d else None


@router.get("/{run_id}/artifacts")
async def artifacts(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return generated artifacts for a completed run.

    For engine runs: lists files produced in output_dir (if persisted to disk).
    For team runs: returns code/devops/doc/test artifact lists from run state.
    """
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    # Engine run path
    output_dir = _engine_output_dir(run)
    if run.team == "engine":
        if output_dir is None:
            # MemoryStore run: content is serialized into Run.state post-completion.
            s = run.state or {}
            if s.get("code_artifacts") or s.get("test_artifacts") or s.get("doc_artifacts"):
                return {
                    "run_id": run_id,
                    "status": run.status,
                    "engine": True,
                    "code_artifacts":   s.get("code_artifacts")   or [],
                    "test_artifacts":   s.get("test_artifacts")   or [],
                    "doc_artifacts":    s.get("doc_artifacts")    or [],
                    "devops_artifacts": [],
                }
            return {"run_id": run_id, "status": run.status, "engine": True,
                    "artifacts": [], "note": "Run used in-memory store — files not persisted"}
        if not output_dir.exists():
            return {"run_id": run_id, "status": run.status, "engine": True,
                    "artifacts": [], "note": f"output_dir not found on server: {output_dir}"}
        file_list = [
            {"file_path": str(p.relative_to(output_dir)), "size_bytes": p.stat().st_size}
            for p in sorted(output_dir.rglob("*"))
            if p.is_file() and not any(part in _ENGINE_SKIP_DIRS for part in p.parts)
        ]
        return {"run_id": run_id, "status": run.status, "engine": True,
                "output_dir": str(output_dir), "artifacts": file_list}

    # Team run path (original behaviour)
    if run.state is None:
        raise StateNotAvailableError(run_id, run.status)
    s = run.state
    return {
        "run_id": run_id,
        "status": run.status,
        "code_artifacts":   s.get("code_artifacts")   or [],
        "devops_artifacts": s.get("devops_artifacts") or [],
        "doc_artifacts":    s.get("doc_artifacts")    or [],
        "test_artifacts":   s.get("test_artifacts")   or [],
    }


@router.get("/{run_id}/artifacts.zip")
async def artifacts_zip(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> StreamingResponse:
    """Download all artifacts as a ZIP archive.

    For engine runs: zips every file under output_dir (excluding .antcrew/ metadata).
    For team runs: zips code/test/devops/doc artifacts from run state.
    """
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    buf = io.BytesIO()

    # Engine run path
    output_dir = _engine_output_dir(run)
    if run.team == "engine":
        if output_dir is None:
            s = run.state or {}
            all_state_arts = (
                (s.get("code_artifacts") or [])
                + (s.get("test_artifacts") or [])
                + (s.get("doc_artifacts") or [])
            )
            if not all_state_arts:
                raise HTTPException(
                    404, "Engine run used in-memory store — artifacts were not persisted to disk"
                )
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for art in all_state_arts:
                    path = art.get("file_path") or ""
                    content = art.get("content") or ""
                    if path:
                        zf.writestr(path.lstrip("/"), content)
            buf.seek(0)
            filename = f"antcrew-engine-{run_id[:12]}.zip"
            return StreamingResponse(
                buf, media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        if not output_dir.exists():
            raise HTTPException(404, f"output_dir not found on server: {output_dir}")
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(output_dir.rglob("*")):
                if p.is_file() and not any(part in _ENGINE_SKIP_DIRS for part in p.parts):
                    zf.write(p, str(p.relative_to(output_dir)))
        buf.seek(0)
        filename = f"antcrew-engine-{run_id[:12]}.zip"
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Team run path (original behaviour)
    if run.state is None:
        raise StateNotAvailableError(run_id, run.status)
    s = run.state
    all_artifacts = (
        (s.get("code_artifacts") or [])
        + (s.get("test_artifacts") or [])
        + (s.get("devops_artifacts") or [])
        + (s.get("doc_artifacts") or [])
    )
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for art in all_artifacts:
            if isinstance(art, dict):
                path = art.get("file_path") or art.get("path") or ""
                content = art.get("content") or ""
            else:
                path = getattr(art, "file_path", "") or getattr(art, "path", "") or ""
                content = getattr(art, "content", "") or ""
            if path:
                zf.writestr(path.lstrip("/"), content)
    buf.seek(0)
    filename = f"antcrew-{run_id[:12]}.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/blocking-tickets")
async def blocking_tickets(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Return open blocking (manual-action) tickets for a run.

    When a run has status ``blocked``, this endpoint shows what a human needs
    to complete before the pipeline can continue.  Resolve each ticket via
    ``PATCH /tickets/{ticket_id}/status`` with ``{"status": "done"}``.
    """
    from sqlmodel import select as _sel
    from app.models.run import Ticket

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    result = await session.exec(
        _sel(Ticket)
        .where(Ticket.run_id == run_id, Ticket.blocking == True)  # noqa: E712
        .order_by(Ticket.created_at)
    )
    return {"run_id": run_id, "status": run.status, "blocking_tickets": list(result.all())}


@router.post(
    "/{run_id}/unblock",
    dependencies=[Depends(require_role("admin"))],
)
async def force_unblock(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Force-unblock a blocked run without requiring ticket resolution (admin only).

    Marks all open blocking tickets for this run as ``done`` and sets
    run.status back to ``running``.  Use this to recover from stuck pipelines
    or when the manual step was completed outside the platform.
    """
    from sqlmodel import select as _sel
    from app.models.run import Ticket
    from app.services.engine_runner import resolve_manual_action

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    if run.status != "blocked":
        raise HTTPException(409, f"Run {run_id!r} is not blocked (status={run.status!r})")

    tickets = (await session.exec(
        _sel(Ticket).where(
            Ticket.run_id == run_id,
            Ticket.blocking == True,  # noqa: E712
            Ticket.status != "done",
        )
    )).all()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for t in tickets:
        t.status = "done"
        t.updated_at = now
        session.add(t)
        resolve_manual_action(t.ticket_id)

    run.status = "running"
    session.add(run)
    await session.commit()
    return {"run_id": run_id, "unblocked_tickets": len(tickets), "status": "running"}


@router.get("/{run_id}/events", response_model=list[DBEvent])
async def events(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)
    return await get_run_events(session, run_id)


@router.get("/{run_id}/activity")
async def activity(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> list[dict]:
    """Merged timeline of Event rows and HitlAuditEntry rows for a run, sorted by timestamp.

    Each item has the shape::

        {"ts": "<ISO-8601>", "type": "event"|"hitl", "kind": "<str>", "payload": {...}}

    Events use ``event_type`` as ``kind``; HITL entries use the audit ``action`` as ``kind``.
    """
    from sqlmodel import select as _sel
    from app.models.run import HitlReview, HitlAuditEntry
    from datetime import timezone

    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    db_events = await get_run_events(session, run_id)

    # HITL audit entries are linked via HitlReview, which holds the run_id.
    reviews = (await session.exec(
        _sel(HitlReview).where(HitlReview.run_id == run_id)
    )).all()

    review_map: dict[str, Any] = {r.review_id: r for r in reviews}

    audit_entries: list[Any] = []
    if reviews:
        review_ids = [r.review_id for r in reviews]
        audit_entries = (await session.exec(
            _sel(HitlAuditEntry).where(HitlAuditEntry.review_id.in_(review_ids))
        )).all()

    items: list[dict] = []

    for ev in db_events:
        # Event.timestamp is a unix float; convert to UTC ISO-8601.
        ts_dt = datetime.fromtimestamp(ev.timestamp, tz=timezone.utc) if ev.timestamp else \
            ev.recorded_at.replace(tzinfo=timezone.utc)
        items.append({
            "ts": ts_dt.isoformat(),
            "type": "event",
            "kind": ev.event_type,
            "payload": ev.payload,
        })

    for ae in audit_entries:
        review = review_map.get(ae.review_id)
        ts_dt = ae.created_at.replace(tzinfo=timezone.utc)
        items.append({
            "ts": ts_dt.isoformat(),
            "type": "hitl",
            "kind": ae.action,
            "payload": {
                "review_id": ae.review_id,
                "actor_label": ae.actor_label,
                "note": ae.note,
                "agent_name": review.agent_name if review else None,
                "decision": review.decision if review else None,
            },
        })

    items.sort(key=lambda x: x["ts"])
    return items


class _ReplayRequest(BaseModel):
    model: Optional[str] = None          # override model; defaults to original
    goal: Optional[str] = None           # override goal description
    conditions: Optional[list[str]] = None  # override conditions list


@router.post(
    "/{run_id}/replay",
    status_code=202,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def replay(
    run_id: str,
    body: _ReplayRequest,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Re-run a completed engine run with optional parameter overrides.

    Loads goal, model, conditions, and output_dir from the original run's state.
    The new run starts fresh (no resume) so artifacts are regenerated from scratch.
    Returns the new run_id immediately; the run executes in the background.
    """
    run = await get_run(session, run_id)
    if not run:
        raise RunNotFoundError(run_id)
    _assert_run_access(run, ctx)

    if run.team != "engine":
        raise HTTPException(400, "Replay is only supported for engine runs")

    state = run.state or {}
    if not state.get("goal"):
        raise HTTPException(422, "Original run has no goal metadata — cannot replay")

    from pathlib import Path as _Path
    from app.services.engine_runner import dispatch_engine

    orig_output_dir = state.get("output_dir")
    new_run_id = await dispatch_engine(
        goal=body.goal or state["goal"],
        model=body.model or "claude",
        conditions=body.conditions or state.get("conditions_expected") or [],
        full=True,
        output_dir=_Path(orig_output_dir).parent / uuid.uuid4().hex if orig_output_dir else None,
        workspace_id=run.workspace_id,
        created_by=ctx.created_by,
    )
    return {"run_id": new_run_id, "replayed_from": run_id}
