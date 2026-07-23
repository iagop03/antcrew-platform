"""Ola 3 tests — model diff (POST /run/compare) + prompt regression (POST /evals/regression).

Feature 6: Model diff — run the same request against two LLM backends, diff the outputs.
Feature 7: Prompt regression — replay historical runs with current prompts, detect degradation.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import CompareRun, EvalRun, Run


# ---------------------------------------------------------------------------
# Feature 6: Model Diff — POST /run/compare
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compare_unknown_team(client: AsyncClient):
    r = await client.post("/run/compare", json={
        "team": "NoSuchTeam", "request": "Build auth", "model_b": "gpt-4o",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_compare_same_model_rejected(client: AsyncClient):
    r = await client.post("/run/compare", json={
        "team": "DevTeam", "request": "Build auth",
        "model_a": "claude", "model_b": "claude",
    })
    assert r.status_code == 422
    assert "different" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_compare_returns_202_with_ids(client: AsyncClient, session: AsyncSession):
    run_a_id = str(uuid.uuid4())
    run_b_id = str(uuid.uuid4())
    # Stub runs so DB lookups succeed
    session.add(Run(run_id=run_a_id, team="DevTeam", request="Build auth", status="running"))
    session.add(Run(run_id=run_b_id, team="DevTeam", request="Build auth", status="running"))
    await session.commit()

    with patch("app.api.compare.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.side_effect = [run_a_id, run_b_id]
        r = await client.post("/run/compare", json={
            "team": "DevTeam",
            "request": "Build auth system",
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
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_compare_passes_model_to_dispatch(client: AsyncClient, session: AsyncSession):
    run_a_id = str(uuid.uuid4())
    run_b_id = str(uuid.uuid4())
    session.add(Run(run_id=run_a_id, team="DevTeam", request="x", status="running"))
    session.add(Run(run_id=run_b_id, team="DevTeam", request="x", status="running"))
    await session.commit()

    with patch("app.api.compare.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.side_effect = [run_a_id, run_b_id]
        await client.post("/run/compare", json={
            "team": "DevTeam", "request": "x", "model_a": "claude", "model_b": "gpt-4o",
        })

    calls = mock_dispatch.call_args_list
    assert len(calls) == 2
    models = {calls[0].kwargs.get("model"), calls[1].kwargs.get("model")}
    assert models == {"claude", "gpt-4o"}


@pytest.mark.asyncio
async def test_compare_get_running_state(client: AsyncClient, session: AsyncSession):
    cmp_id = str(uuid.uuid4())
    run_a = Run(run_id="cmp-a-run", team="DevTeam", request="x", status="running")
    run_b = Run(run_id="cmp-b-run", team="DevTeam", request="x", status="running")
    session.add(run_a)
    session.add(run_b)
    session.add(CompareRun(
        compare_id=cmp_id,
        run_id_a="cmp-a-run",
        run_id_b="cmp-b-run",
        model_a="claude",
        model_b="gpt-4o",
        team="DevTeam",
        request="x",
    ))
    await session.commit()

    r = await client.get(f"/run/compare/{cmp_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "running"
    assert data["diff"] is None
    assert data["model_a"]["name"] == "claude"
    assert data["model_b"]["name"] == "gpt-4o"


@pytest.mark.asyncio
async def test_compare_get_done_returns_diff(client: AsyncClient, session: AsyncSession):
    cmp_id = str(uuid.uuid4())
    state_a = {
        "tickets": [{"title": "Add login"}, {"title": "Add logout"}],
        "code_artifacts": [
            {"filename": "auth.py"},
            {"filename": "main.py"},
        ],
        "prd": {"title": "Auth Feature"},
        "review_verdict": "approved",
    }
    state_b = {
        "tickets": [{"title": "Add login"}, {"title": "Add registration"}],
        "code_artifacts": [
            {"filename": "main.py"},
            {"filename": "models.py"},
        ],
        "prd": {"title": "Authentication System"},
        "review_verdict": "approved",
    }
    session.add(Run(
        run_id="done-a", team="DevTeam", request="x", status="success",
        cost_usd=0.25, duration_s=45.0, state=state_a,
    ))
    session.add(Run(
        run_id="done-b", team="DevTeam", request="x", status="success",
        cost_usd=0.18, duration_s=38.0, state=state_b,
    ))
    session.add(CompareRun(
        compare_id=cmp_id,
        run_id_a="done-a",
        run_id_b="done-b",
        model_a="claude",
        model_b="gpt-4o",
        team="DevTeam",
        request="x",
    ))
    await session.commit()

    r = await client.get(f"/run/compare/{cmp_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "done"
    assert data["model_a"]["cost_usd"] == pytest.approx(0.25)
    assert data["model_b"]["cost_usd"] == pytest.approx(0.18)
    assert data["model_b"]["cost_usd"] < data["model_a"]["cost_usd"]

    diff = data["diff"]
    assert diff is not None
    # "Add logout" only in A; "Add registration" only in B; "Add login" shared
    assert "Add logout" in diff["tickets"]["only_in_a"]
    assert "Add registration" in diff["tickets"]["only_in_b"]
    assert "Add login" in diff["tickets"]["shared"]
    # "auth.py" only in A, "models.py" only in B, "main.py" shared
    assert "auth.py" in diff["code_files"]["only_in_a"]
    assert "models.py" in diff["code_files"]["only_in_b"]
    assert "main.py" in diff["code_files"]["shared"]
    # cost winner is model_b (cheaper)
    assert diff["summary"]["cost_usd"]["winner"] == "b"
    assert diff["summary"]["duration_s"]["winner"] == "b"


@pytest.mark.asyncio
async def test_compare_get_not_found(client: AsyncClient):
    r = await client.get("/run/compare/no-such-id")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_compare_list(client: AsyncClient, session: AsyncSession):
    session.add(Run(run_id="list-a1", team="DevTeam", request="x", status="running"))
    session.add(Run(run_id="list-b1", team="DevTeam", request="x", status="running"))
    session.add(CompareRun(
        compare_id="list-cmp-1",
        run_id_a="list-a1",
        run_id_b="list-b1",
        model_a="claude",
        model_b="ollama:llama3",
        team="DevTeam",
        request="Build something",
    ))
    await session.commit()

    r = await client.get("/run/compare")
    assert r.status_code == 200
    rows = r.json()
    assert any(row["compare_id"] == "list-cmp-1" for row in rows)
    entry = next(row for row in rows if row["compare_id"] == "list-cmp-1")
    assert entry["model_a"] == "claude"
    assert entry["model_b"] == "ollama:llama3"


# ---------------------------------------------------------------------------
# Feature 7: Prompt Regression — POST /evals/regression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regression_empty_run_ids(client: AsyncClient):
    r = await client.post("/evals/regression", json={"run_ids": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_regression_too_many_run_ids(client: AsyncClient):
    ids = [str(uuid.uuid4()) for _ in range(21)]
    r = await client.post("/evals/regression", json={"run_ids": ids})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_regression_run_not_found(client: AsyncClient):
    r = await client.post("/evals/regression", json={"run_ids": ["no-such-run"]})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_regression_creates_evals(client: AsyncClient, session: AsyncSession):
    base_run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=base_run_id,
        team="DevTeam",
        request="Build login feature",
        status="success",
        state={
            "tickets": [{"title": "T1"}, {"title": "T2"}, {"title": "T3"}],
            "code_artifacts": [{"filename": "auth.py"}, {"filename": "main.py"}],
            "review_verdict": "approved",
        },
    ))
    await session.commit()

    from unittest.mock import patch as _patch
    with _patch("app.api.evals.run_eval_sync"):
        r = await client.post("/evals/regression", json={"run_ids": [base_run_id]})

    assert r.status_code == 202
    data = r.json()
    assert data["regression_id"]
    assert data["baseline_count"] == 1
    assert len(data["cases"]) == 1

    case = data["cases"][0]
    assert case["baseline_run_id"] == base_run_id
    assert case["eval_id"]
    assert case["baseline"]["tickets"] == 3
    assert case["baseline"]["code_files"] == 2
    assert case["baseline"]["review_verdict"] == "approved"


@pytest.mark.asyncio
async def test_regression_uses_original_team(client: AsyncClient, session: AsyncSession):
    base_run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=base_run_id,
        team="ResearchTeam",
        request="Research quantum computing",
        status="success",
        state={},
    ))
    await session.commit()

    from unittest.mock import patch as _patch
    with _patch("app.api.evals.run_eval_sync"):
        r = await client.post("/evals/regression", json={"run_ids": [base_run_id]})

    assert r.status_code == 202
    case = r.json()["cases"][0]
    assert case["team"] == "ResearchTeam"


@pytest.mark.asyncio
async def test_regression_team_override(client: AsyncClient, session: AsyncSession):
    base_run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=base_run_id,
        team="DevTeam",
        request="Build something",
        status="success",
        state={},
    ))
    await session.commit()

    from unittest.mock import patch as _patch
    with _patch("app.api.evals.run_eval_sync"):
        r = await client.post("/evals/regression", json={
            "run_ids": [base_run_id],
            "team": "ContentTeam",
        })

    assert r.status_code == 202
    assert r.json()["cases"][0]["team"] == "ContentTeam"


@pytest.mark.asyncio
async def test_regression_invalid_team_override(client: AsyncClient, session: AsyncSession):
    base_run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=base_run_id,
        team="DevTeam",
        request="x",
        status="success",
        state={},
    ))
    await session.commit()

    r = await client.post("/evals/regression", json={
        "run_ids": [base_run_id],
        "team": "GhostTeam",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_regression_stored_in_db(client: AsyncClient, session: AsyncSession):
    base_run_id = str(uuid.uuid4())
    session.add(Run(
        run_id=base_run_id, team="DevTeam", request="x", status="success", state={},
    ))
    await session.commit()

    from unittest.mock import patch as _patch
    with _patch("app.api.evals.run_eval_sync"):
        r = await client.post("/evals/regression", json={"run_ids": [base_run_id]})

    regression_id = r.json()["regression_id"]
    eval_id = r.json()["cases"][0]["eval_id"]

    # verify EvalRun was written with the regression_id
    from sqlmodel import select as _sel
    row = (await session.exec(_sel(EvalRun).where(EvalRun.eval_id == eval_id))).first()
    assert row is not None
    assert row.regression_id == regression_id


# ---------------------------------------------------------------------------
# Feature 7: GET /evals/regression/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regression_get_not_found(client: AsyncClient):
    r = await client.get("/evals/regression/no-such-id")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_regression_get_running(client: AsyncClient, session: AsyncSession):
    reg_id = str(uuid.uuid4())
    session.add(EvalRun(
        eval_id="reg-ev-1",
        team="DevTeam",
        request="x",
        name=f"[reg:{reg_id[:8]}] x",
        status="running",
        regression_id=reg_id,
    ))
    await session.commit()

    r = await client.get(f"/evals/regression/{reg_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["regression_id"] == reg_id
    assert data["status"] == "running"
    assert data["total"] == 1
    assert data["running"] == 1
    assert data["done"] == 0


@pytest.mark.asyncio
async def test_regression_get_done_with_results(client: AsyncClient, session: AsyncSession):
    reg_id = str(uuid.uuid4())
    session.add(EvalRun(
        eval_id="reg-ev-pass",
        team="DevTeam",
        request="x",
        name=f"[reg:{reg_id[:8]}] x",
        status="done",
        report={"passed": True, "overall_score": 0.9, "agent_scores": {}},
        cost_usd=0.05,
        elapsed_ms=12000.0,
        regression_id=reg_id,
    ))
    session.add(EvalRun(
        eval_id="reg-ev-fail",
        team="DevTeam",
        request="y",
        name=f"[reg:{reg_id[:8]}] y",
        status="done",
        report={"passed": False, "overall_score": 0.4, "agent_scores": {}},
        cost_usd=0.07,
        elapsed_ms=15000.0,
        regression_id=reg_id,
    ))
    await session.commit()

    r = await client.get(f"/evals/regression/{reg_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "done"
    assert data["total"] == 2
    assert data["done"] == 2
    assert data["passed"] == 1
    assert data["failed"] == 1
    assert data["regression_rate"] == pytest.approx(0.5)  # 1 of 2 failed


@pytest.mark.asyncio
async def test_regression_filter_in_list(client: AsyncClient, session: AsyncSession):
    reg_id = str(uuid.uuid4())
    session.add(EvalRun(
        eval_id="reg-list-ev",
        team="DevTeam",
        request="z",
        status="running",
        regression_id=reg_id,
    ))
    session.add(EvalRun(eval_id="standalone-ev", team="DevTeam", request="w", status="done"))
    await session.commit()

    r = await client.get(f"/evals/?regression_id={reg_id}")
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["eval_id"] == "reg-list-ev"


# ---------------------------------------------------------------------------
# Feature 6 + 7: runner.dispatch model param wiring
# ---------------------------------------------------------------------------

def test_dispatch_signature_accepts_model():
    """dispatch() must accept model= keyword so compare.py can pass it."""
    import inspect
    from app.services.runner import dispatch
    sig = inspect.signature(dispatch)
    assert "model" in sig.parameters
    param = sig.parameters["model"]
    assert param.default == ""
