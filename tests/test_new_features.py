"""Tests for: templates workspace scoping, eval comparison, artifacts ZIP, review status validation."""
from __future__ import annotations

import io
import zipfile

import pytest
from httpx import AsyncClient

from app.models.run import EvalRun, Run, RunTemplate


# ---------------------------------------------------------------------------
# Templates — workspace scoping (security fix)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_template_hitl_field_persisted(client: AsyncClient):
    r = await client.post("/templates/", json={
        "name": "hitl-template",
        "team": "DevTeam",
        "request": "Build auth",
        "hitl": True,
    })
    assert r.status_code == 201
    assert r.json()["hitl"] is True


@pytest.mark.asyncio
async def test_template_hitl_defaults_false(client: AsyncClient):
    r = await client.post("/templates/", json={
        "name": "no-hitl",
        "team": "DevTeam",
        "request": "Build something",
    })
    assert r.status_code == 201
    assert r.json()["hitl"] is False


@pytest.mark.asyncio
async def test_create_template_no_body_workspace_id(client: AsyncClient):
    """workspace_id is no longer accepted in the body — silently ignored."""
    r = await client.post("/templates/", json={
        "name": "no-ws-field",
        "team": "DevTeam",
        "request": "x",
    })
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# Eval comparison
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eval_compare_not_found(client: AsyncClient):
    r = await client.get("/evals/compare?a=no-such&b=also-not")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_eval_compare_only_a_missing(client: AsyncClient, session):
    session.add(EvalRun(
        eval_id="cmp-b",
        team="DevTeam",
        request="x",
        status="done",
        report={"passed": True, "overall_score": 0.9},
    ))
    await session.commit()

    r = await client.get("/evals/compare?a=no-such&b=cmp-b")
    assert r.status_code == 404
    assert "Baseline" in r.json()["detail"]


@pytest.mark.asyncio
async def test_eval_compare_improvement(client: AsyncClient, session):
    session.add(EvalRun(
        eval_id="cmp-base",
        team="DevTeam",
        request="x",
        status="done",
        report={"passed": False, "overall_score": 0.6},
        cost_usd=0.10,
        elapsed_ms=5000.0,
    ))
    session.add(EvalRun(
        eval_id="cmp-cand",
        team="DevTeam",
        request="x",
        status="done",
        report={"passed": True, "overall_score": 0.9},
        cost_usd=0.12,
        elapsed_ms=4800.0,
    ))
    await session.commit()

    r = await client.get("/evals/compare?a=cmp-base&b=cmp-cand")
    assert r.status_code == 200
    data = r.json()
    assert data["baseline"]["overall_score"] == pytest.approx(0.6)
    assert data["candidate"]["overall_score"] == pytest.approx(0.9)
    assert data["delta"]["overall_score"] == pytest.approx(0.3, abs=0.001)
    assert data["delta"]["improved"] is True
    assert data["delta"]["regression"] is False


@pytest.mark.asyncio
async def test_eval_compare_regression(client: AsyncClient, session):
    session.add(EvalRun(
        eval_id="reg-base",
        team="DevTeam",
        request="x",
        status="done",
        report={"passed": True, "overall_score": 0.9},
    ))
    session.add(EvalRun(
        eval_id="reg-cand",
        team="DevTeam",
        request="x",
        status="done",
        report={"passed": False, "overall_score": 0.7},
    ))
    await session.commit()

    r = await client.get("/evals/compare?a=reg-base&b=reg-cand")
    assert r.status_code == 200
    data = r.json()
    assert data["delta"]["regression"] is True
    assert data["delta"]["improved"] is False


# ---------------------------------------------------------------------------
# Artifacts ZIP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_artifacts_zip_not_found(client: AsyncClient):
    r = await client.get("/runs/no-such-run/artifacts.zip")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_artifacts_zip_no_state(client: AsyncClient, session):
    session.add(Run(run_id="zip-run-1", team="DevTeam", request="x", status="running"))
    await session.commit()
    r = await client.get("/runs/zip-run-1/artifacts.zip")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_artifacts_zip_downloads(client: AsyncClient, session):
    state = {
        "code_artifacts": [
            {"file_path": "src/main.py", "content": "print('hello')"},
            {"file_path": "src/utils.py", "content": "def helper(): pass"},
        ],
        "test_artifacts": [{"file_path": "tests/test_main.py", "content": "def test_hi(): pass"}],
        "devops_artifacts": [],
        "doc_artifacts": [],
    }
    session.add(Run(run_id="zip-run-2", team="DevTeam", request="x", status="success", state=state))
    await session.commit()

    r = await client.get("/runs/zip-run-2/artifacts.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]

    # Verify ZIP contents
    buf = io.BytesIO(r.content)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert "src/main.py" in names
        assert "src/utils.py" in names
        assert "tests/test_main.py" in names
        assert zf.read("src/main.py").decode() == "print('hello')"


# ---------------------------------------------------------------------------
# Reviews — status validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reviews_invalid_status_returns_422(client: AsyncClient):
    r = await client.get("/reviews/?status=pendign")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_reviews_valid_status_accepted(client: AsyncClient):
    for status in ("pending", "approved", "rejected", "edited", "timeout"):
        r = await client.get(f"/reviews/?status={status}")
        assert r.status_code == 200, f"Status {status!r} rejected unexpectedly"
