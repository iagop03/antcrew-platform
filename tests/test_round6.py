"""Round 6 integration tests — eval judge_model, EvalSchedule fields,
workspace default_repo_url, pipeline fallback, Slack artifact excerpt,
anti-duplicate serve check, duration_s push, status command.
"""
from __future__ import annotations

import json
import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# 1. EvalRequest accepts judge_model
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eval_request_accepts_judge_model(client: AsyncClient):
    """POST /evals/ should accept judge_model without 422."""
    r = await client.post("/evals/", json={
        "team": "DevTeam",
        "request": "test",
        "judge_model": "claude",
    })
    assert r.status_code == 202
    assert r.json()["team"] == "DevTeam"


# ---------------------------------------------------------------------------
# 2. EvalSchedule create stores model/judge_model/expect_review_verdict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eval_schedule_stores_new_fields(client: AsyncClient):
    r = await client.post("/eval-schedules/", json={
        "name": "my-sched",
        "team": "DevTeam",
        "request": "test sched",
        "interval_hours": 12.0,
        "model": "gpt-4o",
        "judge_model": "claude",
        "expect_min_tickets": 2,
        "expect_review_verdict": "approve",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["model"] == "gpt-4o"
    assert data["judge_model"] == "claude"
    assert data["expect_review_verdict"] == "approve"
    assert data["expect_min_tickets"] == 2


# ---------------------------------------------------------------------------
# 3. Workspace: create with default_repo_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_create_with_default_repo_url(client: AsyncClient):
    r = await client.post("/workspaces/", json={
        "name": "My Workspace",
        "slug": "my-ws-r6",
        "default_repo_url": "https://github.com/org/repo",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["default_repo_url"] == "https://github.com/org/repo"


@pytest.mark.asyncio
async def test_workspace_create_invalid_repo_url_rejected(client: AsyncClient):
    r = await client.post("/workspaces/", json={
        "name": "Bad WS",
        "slug": "bad-ws-r6",
        "default_repo_url": "not-a-url",
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 4. PATCH /workspaces/{id}/repo sets and clears default_repo_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_workspace_repo_url(client: AsyncClient):
    r = await client.post("/workspaces/", json={"name": "W", "slug": "w-repo-test-r6"})
    assert r.status_code == 201
    ws_id = r.json()["id"]

    r2 = await client.patch(f"/workspaces/{ws_id}/repo", json={
        "default_repo_url": "https://github.com/foo/bar",
    })
    assert r2.status_code == 200
    assert r2.json()["default_repo_url"] == "https://github.com/foo/bar"

    r3 = await client.patch(f"/workspaces/{ws_id}/repo", json={"default_repo_url": None})
    assert r3.status_code == 200
    assert r3.json()["default_repo_url"] is None


@pytest.mark.asyncio
async def test_patch_workspace_repo_404(client: AsyncClient):
    r = await client.patch("/workspaces/99999/repo", json={"default_repo_url": None})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 5. _build_hitl_payload: Slack format includes artifact excerpt
# ---------------------------------------------------------------------------

def test_slack_payload_includes_artifact_excerpt():
    from app.core.listener import _build_hitl_payload, _extract_artifact_excerpt

    artifact = {
        "tickets": [
            {"title": "Implement login endpoint"},
            {"title": "Add JWT middleware"},
        ]
    }
    excerpt = _extract_artifact_excerpt(artifact)
    assert "Implement login endpoint" in excerpt
    assert "(+1 more)" in excerpt

    payload_str = _build_hitl_payload(
        "https://hooks.slack.com/services/T00/B00/xxx",
        review_id="r1",
        run_id="run-abc",
        agent_name="PMAgent",
        artifact=artifact,
        options=["approve", "reject"],
    )
    payload = json.loads(payload_str)
    block_texts = " ".join(
        str(b.get("text", {}).get("text", "")) + str(b.get("fields", ""))
        for b in payload["blocks"]
    )
    assert "Implement login endpoint" in block_texts


def test_slack_payload_no_artifact():
    """Empty artifact should not add an Artifact: block."""
    from app.core.listener import _build_hitl_payload
    payload_str = _build_hitl_payload(
        "https://hooks.slack.com/services/T00/B00/xxx",
        review_id="r1",
        run_id="run-abc",
        agent_name="PMAgent",
        artifact={},
        options=["approve", "reject"],
    )
    payload = json.loads(payload_str)
    artifact_blocks = [
        b for b in payload["blocks"]
        if b.get("type") == "section"
        and "Artifact:" in str(b.get("text", {}).get("text", ""))
    ]
    assert artifact_blocks == []


def test_plain_json_payload_includes_artifact_excerpt():
    from app.core.listener import _build_hitl_payload

    payload_str = _build_hitl_payload(
        "https://myhooks.example.com/hook",
        review_id="r2",
        run_id="run-xyz",
        agent_name="DevAgent",
        artifact={"title": "My PRD"},
        options=["approve"],
    )
    data = json.loads(payload_str)
    assert data["artifact_excerpt"] == "My PRD"


# ---------------------------------------------------------------------------
# 6. _extract_artifact_excerpt covers different shapes
# ---------------------------------------------------------------------------

def test_extract_artifact_excerpt_title():
    from app.core.listener import _extract_artifact_excerpt
    assert _extract_artifact_excerpt({"title": "PRD Title"}) == "PRD Title"


def test_extract_artifact_excerpt_empty():
    from app.core.listener import _extract_artifact_excerpt
    assert _extract_artifact_excerpt({}) == ""
    assert _extract_artifact_excerpt(None) == ""


def test_extract_artifact_excerpt_tickets_count():
    from app.core.listener import _extract_artifact_excerpt
    artifact = {"tickets": [{"title": "T1"}, {"title": "T2"}, {"title": "T3"}]}
    result = _extract_artifact_excerpt(artifact)
    assert result == "T1 (+2 more)"


# ---------------------------------------------------------------------------
# 7. _push_run_to_platform sends duration_s
# ---------------------------------------------------------------------------

def test_push_run_to_platform_sends_duration_s(monkeypatch):
    """_push_run_to_platform should include duration_s in the JSON payload."""
    import sys
    captured: dict = {}

    class FakeResponse:
        status_code = 201
        def raise_for_status(self): pass
        def json(self): return {"run_id": "abc"}

    def fake_post(url, *, json, headers, timeout):
        captured["payload"] = json
        return FakeResponse()

    fake_httpx = type(sys)("httpx")
    fake_httpx.post = staticmethod(fake_post)
    fake_httpx.HTTPStatusError = Exception
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    # Force re-import of the module so it picks up the mock
    import importlib
    import antcrew.cli._run_helpers as mod
    importlib.reload(mod)

    mod._push_run_to_platform(
        "http://localhost:8000", None, {"_cost_usd": 0.01},
        team="DevTeam", request="test", thread="default", llm=None,
        duration_s=12.345,
    )
    assert "duration_s" in captured["payload"]
    assert captured["payload"]["duration_s"] == 12.345


# ---------------------------------------------------------------------------
# 8. workspace default_repo_url is returned by GET /workspaces/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_default_repo_url_roundtrip(client: AsyncClient):
    r = await client.post("/workspaces/", json={
        "name": "RepoWS",
        "slug": "repo-ws-r6",
        "default_repo_url": "https://github.com/org/default-repo",
    })
    assert r.status_code == 201
    ws_id = r.json()["id"]

    r2 = await client.get(f"/workspaces/{ws_id}")
    assert r2.status_code == 200
    assert r2.json()["default_repo_url"] == "https://github.com/org/default-repo"
