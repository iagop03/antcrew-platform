"""Round 8 integration tests.

Covers:
- GET /reviews/{review_id} — individual review endpoint
- Model wiring: CustomPipelineRequest.model field + dispatch_custom model param
- GET /run/teams includes "custom"
- validate_agent_dag in dispatch_custom (bad input_key → 422)
- GET /workspaces/{id}/reviews — pending reviews by workspace
- PATCH /workspaces/{id}/hitl-timeout — per-workspace HITL timeout
- PlatformChannel per-workspace timeout wiring
- SlackNotifyChannel (unit test: posts and delegates)
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# 1. GET /reviews/{review_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_review_by_id(client: AsyncClient, session):
    from datetime import datetime, timezone
    from app.models.run import HitlReview

    review = HitlReview(
        review_id="r8-review-get",
        run_id="run-r8-1",
        agent_name="PMAgent",
        status="pending",
    )
    session.add(review)
    await session.commit()

    r = await client.get("/reviews/r8-review-get")
    assert r.status_code == 200
    data = r.json()
    assert data["review_id"] == "r8-review-get"
    assert data["agent_name"] == "PMAgent"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_get_review_by_id_not_found(client: AsyncClient):
    r = await client.get("/reviews/nonexistent-review-r8")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_review_by_id_workspace_scoping(client: AsyncClient, session):
    """A key scoped to workspace A cannot read a review from workspace B's run."""
    import hashlib
    from app.models.run import ApiKey, Run, HitlReview

    raw = "ws-scoped-key-r8"
    session.add(ApiKey(
        label="ws-scoped-r8",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        workspace_id=99,
        role="reviewer",
    ))
    session.add(Run(run_id="run-ws-other-r8", team="DevTeam", request="x", workspace_id=100))
    session.add(HitlReview(
        review_id="review-ws-other-r8",
        run_id="run-ws-other-r8",
        agent_name="PMAgent",
        status="pending",
    ))
    await session.commit()

    r = await client.get("/reviews/review-ws-other-r8", headers={"X-Api-Key": raw})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 2. CustomPipelineRequest model field
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_custom_pipeline_model_field_accepted(client: AsyncClient):
    """model field on CustomPipelineRequest is accepted and doesn't 422."""
    r = await client.post("/run/pipeline", json={
        "request": "test model field",
        "steps": [{"name": "step1", "system_prompt": "You are helpful."}],
        "model": "claude",
    })
    # 202 or 500 (team not importable in test env), NOT 422
    assert r.status_code in (202, 500)


@pytest.mark.asyncio
async def test_custom_pipeline_model_field_default(client: AsyncClient):
    """model defaults to 'claude' when omitted."""
    from app.api.pipeline import CustomPipelineRequest, AgentStepConfig
    req = CustomPipelineRequest(
        request="hello",
        steps=[AgentStepConfig(name="s1", system_prompt="hello")],
    )
    assert req.model == "claude"


# ---------------------------------------------------------------------------
# 3. GET /run/teams includes "custom"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_teams_list_includes_custom(client: AsyncClient):
    r = await client.get("/run/teams")
    assert r.status_code == 200
    teams = r.json()["teams"]
    assert "custom" in teams
    assert "DevTeam" in teams


# ---------------------------------------------------------------------------
# 4. validate_agent_dag in dispatch_custom
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_custom_pipeline_bad_dag_rejected(client: AsyncClient):
    """Steps with an input_key that's never produced should return 422."""
    r = await client.post("/run/pipeline", json={
        "request": "test dag",
        "steps": [
            {"name": "step1", "system_prompt": "p1", "output_key": "prd"},
            {"name": "step2", "system_prompt": "p2", "input_key": "tickets"},  # not produced
        ],
    })
    assert r.status_code == 422
    assert "input_key" in r.json()["detail"].lower() or "tickets" in r.json()["detail"]


@pytest.mark.asyncio
async def test_validate_custom_dag_valid():
    """Direct unit test of _validate_custom_dag with a valid pipeline."""
    from app.services.runner import _validate_custom_dag
    _validate_custom_dag([
        {"name": "a", "input_key": "request", "output_key": "prd"},
        {"name": "b", "input_key": "prd", "output_key": "tickets"},
        {"name": "c", "input_key": "tickets", "output_key": ""},
    ])  # no exception → valid


@pytest.mark.asyncio
async def test_validate_custom_dag_invalid():
    """Direct unit test of _validate_custom_dag with a missing key."""
    from app.services.runner import _validate_custom_dag
    with pytest.raises(ValueError, match="input_key"):
        _validate_custom_dag([
            {"name": "a", "input_key": "request", "output_key": "prd"},
            {"name": "b", "input_key": "code"},  # "code" never produced
        ])


# ---------------------------------------------------------------------------
# 5. GET /workspaces/{id}/reviews
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_reviews_endpoint(client: AsyncClient, session):
    from app.models.run import Workspace, Run, HitlReview

    ws = Workspace(name="ReviewWS", slug="review-ws-r8")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    session.add(Run(run_id="run-ws-review-r8", team="DevTeam", request="x", workspace_id=ws.id))
    session.add(HitlReview(
        review_id="ws-review-pending-r8",
        run_id="run-ws-review-r8",
        agent_name="PMAgent",
        status="pending",
    ))
    session.add(HitlReview(
        review_id="ws-review-approved-r8",
        run_id="run-ws-review-r8",
        agent_name="DevAgent",
        status="approved",
    ))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/reviews")
    assert r.status_code == 200
    reviews = r.json()
    assert len(reviews) == 1
    assert reviews[0]["review_id"] == "ws-review-pending-r8"


@pytest.mark.asyncio
async def test_workspace_reviews_status_filter(client: AsyncClient, session):
    from app.models.run import Workspace, Run, HitlReview

    ws = Workspace(name="ReviewWS2", slug="review-ws2-r8")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    session.add(Run(run_id="run-ws-r8-2", team="DevTeam", request="y", workspace_id=ws.id))
    session.add(HitlReview(
        review_id="ws-r8-approved",
        run_id="run-ws-r8-2",
        agent_name="Dev",
        status="approved",
    ))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/reviews?status=approved")
    assert r.status_code == 200
    assert len(r.json()) == 1

    r2 = await client.get(f"/workspaces/{ws.id}/reviews?status=pending")
    assert r2.status_code == 200
    assert len(r2.json()) == 0


@pytest.mark.asyncio
async def test_workspace_reviews_404(client: AsyncClient):
    r = await client.get("/workspaces/99999/reviews")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 6. PATCH /workspaces/{id}/hitl-timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_hitl_timeout_set(client: AsyncClient):
    r = await client.post("/workspaces/", json={"name": "TW", "slug": "tw-r8"})
    ws_id = r.json()["id"]

    r2 = await client.patch(f"/workspaces/{ws_id}/hitl-timeout", json={"hitl_timeout_s": 1800.0})
    assert r2.status_code == 200
    assert r2.json()["hitl_timeout_s"] == 1800.0


@pytest.mark.asyncio
async def test_workspace_hitl_timeout_clear(client: AsyncClient):
    r = await client.post("/workspaces/", json={"name": "TW2", "slug": "tw2-r8"})
    ws_id = r.json()["id"]

    await client.patch(f"/workspaces/{ws_id}/hitl-timeout", json={"hitl_timeout_s": 600.0})
    r2 = await client.patch(f"/workspaces/{ws_id}/hitl-timeout", json={"hitl_timeout_s": None})
    assert r2.status_code == 200
    assert r2.json()["hitl_timeout_s"] is None


@pytest.mark.asyncio
async def test_workspace_hitl_timeout_zero_rejected(client: AsyncClient):
    r = await client.post("/workspaces/", json={"name": "TW3", "slug": "tw3-r8"})
    ws_id = r.json()["id"]

    r2 = await client.patch(f"/workspaces/{ws_id}/hitl-timeout", json={"hitl_timeout_s": 0})
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_workspace_hitl_timeout_404(client: AsyncClient):
    r = await client.patch("/workspaces/99999/hitl-timeout", json={"hitl_timeout_s": 300.0})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 7. PlatformChannel per-workspace timeout
# ---------------------------------------------------------------------------

def test_platform_channel_uses_custom_timeout():
    """PlatformChannel stores the per-workspace timeout correctly."""
    from app.core.channel import PlatformChannel, _REVIEW_TIMEOUT_S

    ch_default = PlatformChannel()
    assert ch_default._timeout_s == _REVIEW_TIMEOUT_S

    ch_custom = PlatformChannel(timeout_s=120.0)
    assert ch_custom._timeout_s == 120.0


# ---------------------------------------------------------------------------
# 8. SlackNotifyChannel (CLI)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slack_notify_channel_posts_and_returns_approve():
    """SlackNotifyChannel._post is called, and review falls through to console (mocked)."""
    from antcrew.cli._run_helpers import SlackNotifyChannel

    posted = []

    class FakeConsole:
        async def notify(self, message, **kwargs): pass
        async def send_for_review(self, artifact, agent_name, session_id, response_options=None):
            return {"decision": "approve"}

    ch = SlackNotifyChannel("https://example.slack.com/fake", console_ch=FakeConsole())
    ch._post = lambda payload: posted.append(payload)  # intercept

    result = await ch.send_for_review({"artifact": "x"}, "PMAgent", "sess-1")
    assert len(posted) == 1
    assert "PMAgent" in posted[0].get("text", "")
    assert result["decision"] == "approve"


@pytest.mark.asyncio
async def test_slack_notify_channel_nonfatal_on_bad_url():
    """SlackNotifyChannel swallows network errors without raising."""
    from antcrew.cli._run_helpers import SlackNotifyChannel

    class FakeConsole:
        async def send_for_review(self, *a, **kw): return {"decision": "reject"}

    ch = SlackNotifyChannel("https://this-url-will-fail.invalid/webhook", console_ch=FakeConsole())
    # Should not raise even though the HTTP call will fail
    result = await ch.send_for_review({"artifact": "x"}, "DevAgent", "sess-2")
    assert result["decision"] == "reject"
