"""Tests for engine artifact visibility, condition progress, and engine model diff.

Covers:
- GET /runs/{id}/artifacts for engine MemoryStore runs (state-embedded content)
- GET /runs/{id}/artifacts backward-compat: MemoryStore run without artifacts
- GET /engine/runs/{run_id}/progress condition tracking
- POST /run/compare with team="engine"
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import CompareRun, Event, Run


# ---------------------------------------------------------------------------
# GET /runs/{id}/artifacts — engine MemoryStore run (artifacts in state)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_memstore_artifacts_served_from_state(client: AsyncClient, session: AsyncSession):
    """After _store_engine_state saves artifacts, /artifacts returns them with content."""
    run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=run_id,
        team="engine",
        request="Build API",
        status="success",
        state={
            "engine": True,
            "goal": "Build API",
            "output_dir": None,
            "conditions_satisfied": ["requirements_exists", "implementation_exists"],
            "conditions_expected": ["requirements_exists", "implementation_exists"],
            "code_artifacts": [
                {"file_path": "src/main.py", "content": "def main(): pass"},
                {"file_path": "src/api.py", "content": "from fastapi import FastAPI"},
            ],
            "test_artifacts": [
                {"file_path": "tests/test_main.py", "content": "def test_main(): pass"},
            ],
            "doc_artifacts": [
                {"file_path": "README.md", "content": "# My API"},
            ],
        },
    ))
    await session.commit()

    r = await client.get(f"/runs/{run_id}/artifacts")
    assert r.status_code == 200
    data = r.json()
    assert data["engine"] is True
    assert len(data["code_artifacts"]) == 2
    assert data["code_artifacts"][0]["file_path"] == "src/main.py"
    assert data["code_artifacts"][0]["content"] == "def main(): pass"
    assert len(data["test_artifacts"]) == 1
    assert len(data["doc_artifacts"]) == 1
    assert data["devops_artifacts"] == []


@pytest.mark.asyncio
async def test_engine_memstore_no_artifacts_returns_note(client: AsyncClient, session: AsyncSession):
    """Backward compat: MemoryStore engine run without embedded artifacts returns the old note."""
    run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=run_id,
        team="engine",
        request="Build API",
        status="success",
        state={"engine": True, "goal": "Build API", "output_dir": None},
    ))
    await session.commit()

    r = await client.get(f"/runs/{run_id}/artifacts")
    assert r.status_code == 200
    data = r.json()
    assert data["engine"] is True
    assert data["artifacts"] == []
    assert "not persisted" in data["note"]


@pytest.mark.asyncio
async def test_engine_memstore_artifacts_zip(client: AsyncClient, session: AsyncSession):
    """State-embedded artifacts can be downloaded as ZIP."""
    run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=run_id,
        team="engine",
        request="Build API",
        status="success",
        state={
            "engine": True,
            "goal": "Build API",
            "output_dir": None,
            "code_artifacts": [{"file_path": "src/main.py", "content": "hello"}],
            "test_artifacts": [],
            "doc_artifacts": [],
        },
    ))
    await session.commit()

    r = await client.get(f"/runs/{run_id}/artifacts.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


@pytest.mark.asyncio
async def test_engine_memstore_no_artifacts_zip_404(client: AsyncClient, session: AsyncSession):
    """MemoryStore engine run without state artifacts returns 404 on ZIP download."""
    run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=run_id,
        team="engine",
        request="x",
        status="success",
        state={"engine": True, "goal": "x", "output_dir": None},
    ))
    await session.commit()

    r = await client.get(f"/runs/{run_id}/artifacts.zip")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /engine/runs/{run_id}/progress
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_progress_not_found(client: AsyncClient):
    r = await client.get("/engine/runs/no-such-run/progress")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_progress_wrong_team(client: AsyncClient, session: AsyncSession):
    """Progress endpoint rejects non-engine runs."""
    run_id = str(uuid.uuid4())
    session.add(Run(run_id=run_id, team="DevTeam", request="x", status="success"))
    await session.commit()

    r = await client.get(f"/engine/runs/{run_id}/progress")
    assert r.status_code == 422
    assert "engine" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_progress_satisfied_conditions(client: AsyncClient, session: AsyncSession):
    """Progress returns satisfied and pending conditions from state."""
    run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=run_id,
        team="engine",
        request="Build API",
        status="success",
        state={
            "engine": True,
            "goal": "Build API",
            "output_dir": None,
            "conditions_expected": [
                "requirements_exists", "architecture_exists",
                "implementation_exists", "tests_pass",
            ],
            "conditions_satisfied": ["requirements_exists", "architecture_exists"],
        },
    ))
    await session.commit()

    r = await client.get(f"/engine/runs/{run_id}/progress")
    assert r.status_code == 200
    data = r.json()
    assert data["run_id"] == run_id
    assert data["status"] == "success"
    assert data["goal"] == "Build API"
    assert data["total_conditions"] == 4
    assert data["satisfied_count"] == 2
    assert data["conditions"]["requirements_exists"] == "satisfied"
    assert data["conditions"]["architecture_exists"] == "satisfied"
    assert data["conditions"]["implementation_exists"] == "not_reached"
    assert data["conditions"]["tests_pass"] == "not_reached"


@pytest.mark.asyncio
async def test_progress_running_conditions_are_pending(client: AsyncClient, session: AsyncSession):
    """While the run is still active, unsatisfied conditions show as 'pending'."""
    run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=run_id,
        team="engine",
        request="x",
        status="running",
        state={
            "engine": True,
            "goal": "x",
            "output_dir": None,
            "conditions_expected": ["requirements_exists", "implementation_exists"],
            "conditions_satisfied": ["requirements_exists"],
        },
    ))
    await session.commit()

    r = await client.get(f"/engine/runs/{run_id}/progress")
    assert r.status_code == 200
    data = r.json()
    assert data["conditions"]["requirements_exists"] == "satisfied"
    assert data["conditions"]["implementation_exists"] == "pending"


@pytest.mark.asyncio
async def test_progress_includes_capability_history(client: AsyncClient, session: AsyncSession):
    """Progress lists completed capabilities from the event log."""
    run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=run_id,
        team="engine",
        request="x",
        status="success",
        state={
            "engine": True,
            "goal": "x",
            "output_dir": None,
            "conditions_expected": [],
            "conditions_satisfied": [],
        },
    ))
    session.add(Event(
        run_id=run_id,
        event_type="agent.start",
        payload={"agent_name": "Architect", "run_id": run_id},
        timestamp=1000.0,
    ))
    session.add(Event(
        run_id=run_id,
        event_type="agent.end",
        payload={
            "agent_name": "Architect",
            "duration_s": 12.5,
            "cost_usd": 0.04,
            "produced_keys": ["architecture"],
            "run_id": run_id,
        },
        timestamp=1012.5,
    ))
    await session.commit()

    r = await client.get(f"/engine/runs/{run_id}/progress")
    assert r.status_code == 200
    caps = r.json()["capabilities_executed"]
    assert len(caps) == 1
    assert caps[0]["name"] == "Architect"
    assert caps[0]["duration_s"] == pytest.approx(12.5)
    assert caps[0]["cost_usd"] == pytest.approx(0.04)
    assert "architecture" in caps[0]["produced"]


# ---------------------------------------------------------------------------
# POST /run/compare with team="engine"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_compare_no_goal_rejected(client: AsyncClient):
    """Engine compare without a goal returns 422."""
    r = await client.post("/run/compare", json={
        "team": "engine",
        "model_a": "claude",
        "model_b": "gpt-4o",
    })
    assert r.status_code == 422
    assert "goal" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_engine_compare_same_model_rejected(client: AsyncClient):
    r = await client.post("/run/compare", json={
        "team": "engine",
        "goal": "Build API",
        "model_a": "claude",
        "model_b": "claude",
    })
    assert r.status_code == 422
    assert "different" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_engine_compare_dispatches_two_runs(client: AsyncClient, session: AsyncSession):
    """Engine compare dispatches two dispatch_engine calls and returns compare_id."""
    run_a_id = str(uuid.uuid4())
    run_b_id = str(uuid.uuid4())
    session.add(Run(run_id=run_a_id, team="engine", request="Build API", status="running"))
    session.add(Run(run_id=run_b_id, team="engine", request="Build API", status="running"))
    await session.commit()

    with patch("app.api.compare.dispatch_engine", new_callable=AsyncMock) as mock_engine:
        mock_engine.side_effect = [run_a_id, run_b_id]
        r = await client.post("/run/compare", json={
            "team": "engine",
            "goal": "Build a REST API",
            "model_a": "claude",
            "model_b": "gpt-4o",
        })

    assert r.status_code == 202
    data = r.json()
    assert data["compare_id"]
    assert data["run_id_a"] == run_a_id
    assert data["run_id_b"] == run_b_id
    assert data["model_a"] == "claude"
    assert data["model_b"] == "gpt-4o"

    # Each call must have received the correct model
    calls = mock_engine.call_args_list
    assert len(calls) == 2
    models = {calls[0].kwargs.get("model"), calls[1].kwargs.get("model")}
    assert models == {"claude", "gpt-4o"}

    # goal passed as positional or keyword
    goals = {calls[0].kwargs.get("goal") or calls[0].args[0] if calls[0].args else None,
             calls[1].kwargs.get("goal") or calls[1].args[0] if calls[1].args else None}
    assert all(g == "Build a REST API" or g is None for g in goals)


@pytest.mark.asyncio
async def test_engine_compare_diff_includes_doc_and_test_files(
    client: AsyncClient, session: AsyncSession
):
    """Engine compare diff includes doc_files and test_files diffs."""
    cmp_id = str(uuid.uuid4())
    state_a = {
        "engine": True,
        "code_artifacts": [{"file_path": "src/main.py"}],
        "test_artifacts": [{"file_path": "tests/test_main.py"}],
        "doc_artifacts":  [{"file_path": "README.md"}],
    }
    state_b = {
        "engine": True,
        "code_artifacts": [{"file_path": "src/main.py"}, {"file_path": "src/api.py"}],
        "test_artifacts": [{"file_path": "tests/test_api.py"}],
        "doc_artifacts":  [{"file_path": "README.md"}, {"file_path": "CONTRIBUTING.md"}],
    }
    session.add(Run(
        run_id="eng-cmp-a", team="engine", request="Build API", status="success",
        cost_usd=0.30, duration_s=55.0, state=state_a,
    ))
    session.add(Run(
        run_id="eng-cmp-b", team="engine", request="Build API", status="success",
        cost_usd=0.22, duration_s=42.0, state=state_b,
    ))
    session.add(CompareRun(
        compare_id=cmp_id,
        run_id_a="eng-cmp-a",
        run_id_b="eng-cmp-b",
        model_a="claude",
        model_b="gpt-4o",
        team="engine",
        request="Build a REST API",
    ))
    await session.commit()

    r = await client.get(f"/run/compare/{cmp_id}")
    assert r.status_code == 200
    diff = r.json()["diff"]
    assert diff is not None

    # code_files: only_in_b = api.py, shared = main.py
    assert "src/api.py" in diff["code_files"]["only_in_b"]
    assert "src/main.py" in diff["code_files"]["shared"]

    # doc_files: only_in_b = CONTRIBUTING.md, shared = README.md
    assert "CONTRIBUTING.md" in diff["doc_files"]["only_in_b"]
    assert "README.md" in diff["doc_files"]["shared"]

    # test_files: only_in_a = test_main.py, only_in_b = test_api.py
    assert "tests/test_main.py" in diff["test_files"]["only_in_a"]
    assert "tests/test_api.py" in diff["test_files"]["only_in_b"]

    assert diff["summary"]["cost_usd"]["winner"] == "b"
    assert diff["summary"]["duration_s"]["winner"] == "b"


@pytest.mark.asyncio
async def test_engine_compare_stored_in_db(client: AsyncClient, session: AsyncSession):
    """Engine compare row is persisted with team='engine' and request=goal."""
    from sqlmodel import select as _sel

    run_a_id = str(uuid.uuid4())
    run_b_id = str(uuid.uuid4())
    session.add(Run(run_id=run_a_id, team="engine", request="x", status="running"))
    session.add(Run(run_id=run_b_id, team="engine", request="x", status="running"))
    await session.commit()

    with patch("app.api.compare.dispatch_engine", new_callable=AsyncMock) as mock_engine:
        mock_engine.side_effect = [run_a_id, run_b_id]
        r = await client.post("/run/compare", json={
            "team": "engine",
            "goal": "Build a FastAPI service",
            "model_a": "claude",
            "model_b": "gpt-4o",
        })

    assert r.status_code == 202
    cmp_id = r.json()["compare_id"]

    row = (await session.exec(
        _sel(CompareRun).where(CompareRun.compare_id == cmp_id)
    )).first()
    assert row is not None
    assert row.team == "engine"
    assert row.request == "Build a FastAPI service"


# ---------------------------------------------------------------------------
# Existing team compare still works after CompareRequest.request made optional
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_team_compare_still_requires_request(client: AsyncClient):
    """Team compare without request field returns 422."""
    r = await client.post("/run/compare", json={
        "team": "DevTeam",
        "model_a": "claude",
        "model_b": "gpt-4o",
    })
    assert r.status_code == 422
    assert "request" in r.json()["detail"].lower()
