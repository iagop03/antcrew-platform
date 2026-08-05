"""Tests for POST /run and GET /runs/:id/state."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.run import Run


# ---------------------------------------------------------------------------
# GET /run/teams
# ---------------------------------------------------------------------------

async def test_list_teams(client):
    r = await client.get("/run/teams")
    assert r.status_code == 200
    data = r.json()
    assert "teams" in data
    assert "DevTeam" in data["teams"]
    assert "ResearchTeam" in data["teams"]


# ---------------------------------------------------------------------------
# GET /run/teams/{team}/agents
# ---------------------------------------------------------------------------

async def test_get_team_agents_devteam(client):
    r = await client.get("/run/teams/DevTeam/agents")
    assert r.status_code == 200
    data = r.json()
    assert data["team"] == "DevTeam"
    agents = data["agents"]
    assert len(agents) == 3
    names = [a["name"] for a in agents]
    assert "BusinessAnalystAgent" in names
    assert "PMAgent" in names
    assert "BackendDevAgent" in names
    assert agents[0]["position"] == 0


async def test_get_team_agents_fullstackteam(client):
    r = await client.get("/run/teams/FullStackTeam/agents")
    assert r.status_code == 200
    assert len(r.json()["agents"]) == 10


async def test_get_team_agents_unknown_returns_404(client):
    r = await client.get("/run/teams/UnknownTeam/agents")
    assert r.status_code == 404


async def test_get_team_agents_has_required_fields(client):
    r = await client.get("/run/teams/ResearchTeam/agents")
    assert r.status_code == 200
    for ag in r.json()["agents"]:
        assert ag["name"]
        assert ag["description"]
        assert "position" in ag


# ---------------------------------------------------------------------------
# POST /run — validation
# ---------------------------------------------------------------------------

async def test_post_run_unknown_team(client):
    r = await client.post("/run/", json={"team": "UnknownTeam", "request": "do stuff"})
    assert r.status_code == 422


async def test_post_run_empty_request(client):
    r = await client.post("/run/", json={"team": "DevTeam", "request": "   "})
    assert r.status_code == 422


async def test_post_run_missing_fields(client):
    r = await client.post("/run/", json={"team": "DevTeam"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /run — successful dispatch
# ---------------------------------------------------------------------------

async def test_post_run_returns_202_with_run_id(client):
    with patch("app.api.pipeline.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = "abc123def456"
        r = await client.post("/run/", json={"team": "DevTeam", "request": "Build auth"})

    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "accepted"
    assert data["run_id"] == "abc123def456"
    assert data["team"] == "DevTeam"
    assert "hint" in data


async def test_post_run_returns_202_even_without_run_id(client):
    """If dispatch times out before pipeline.start, run_id is None but still 202."""
    with patch("app.api.pipeline.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = None
        r = await client.post("/run/", json={"team": "ResearchTeam", "request": "Research AI"})

    assert r.status_code == 202
    assert r.json()["run_id"] is None


async def test_post_run_passes_thread_id(client):
    with patch("app.api.pipeline.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = "r1"
        await client.post(
            "/run/",
            json={"team": "DevTeam", "request": "x", "thread_id": "my-thread"},
        )
    mock_dispatch.assert_called_once_with("DevTeam", "x", "my-thread", max_cost_usd=None, created_by=None, workspace_id=None, force_hitl=False, repo_url=None, repo_token=None, client_label=None, write_back=False, model="", model_overrides=None)


# ---------------------------------------------------------------------------
# GET /runs/:id/state
# ---------------------------------------------------------------------------

async def test_run_state_not_found(client):
    r = await client.get("/runs/nonexistent/state")
    assert r.status_code == 404


async def test_run_state_still_running(client, session):
    run = Run(run_id="r-running", team="DevTeam", request="x", status="running", state=None)
    session.add(run)
    await session.commit()

    r = await client.get("/runs/r-running/state")
    assert r.status_code == 404
    assert "running" in r.json()["detail"]


async def test_run_state_available(client, session):
    state = {
        "run_id": "r-done",
        "cost_usd": 0.05,
        "state": {"prd": {"title": "Auth"}, "tickets": []},
    }
    run = Run(run_id="r-done", team="DevTeam", request="x", status="success", state=state)
    session.add(run)
    await session.commit()

    r = await client.get("/runs/r-done/state")
    assert r.status_code == 200
    data = r.json()
    assert data["run_id"] == "r-done"
    assert data["state"]["prd"]["title"] == "Auth"


# ---------------------------------------------------------------------------
# runner helpers (unit tests, no HTTP)
# ---------------------------------------------------------------------------

def test_make_team_unknown_raises():
    from app.services.runner import _make_team
    with pytest.raises(ValueError, match="Unknown team"):
        _make_team("FakeTeam")


def test_available_teams_list():
    from app.services.runner import AVAILABLE_TEAMS
    assert "DevTeam" in AVAILABLE_TEAMS
    assert "FullStackTeam" in AVAILABLE_TEAMS
    assert "ResearchTeam" in AVAILABLE_TEAMS
    assert "ContentTeam" in AVAILABLE_TEAMS


# ---------------------------------------------------------------------------
# pipeline_builder.py unit tests
# ---------------------------------------------------------------------------

_MINIMAL_DEFN = {
    "nodes": [
        {"id": "ba", "type": "business_analyst", "label": "BA", "model": "claude"},
        {"id": "pm", "type": "pm", "label": "PM", "model": "claude"},
    ],
    "edges": [{"from": "ba", "to": "pm", "condition": None}],
}

_CUSTOM_DEFN = {
    "nodes": [
        {
            "id": "custom_1", "type": "custom_1", "label": "Data Analyst", "model": "claude",
            "agent_cfg": {
                "system_prompt": "You are a data analyst. Analyze {request} and produce a report.",
                "role_description": "Analyzes data and produces concise reports",
            },
        },
        {"id": "pm", "type": "pm", "label": "PM", "model": "claude"},
    ],
    "edges": [{"from": "custom_1", "to": "pm", "condition": None}],
}


def test_build_team_unknown_type_no_cfg_raises(monkeypatch):
    """Unknown agent type without agent_cfg must raise ValueError (no silent failure)."""
    import antcrew
    import antcrew.agents.registry as _registry
    from unittest.mock import MagicMock
    monkeypatch.setattr(antcrew, "build_llm", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(antcrew, "Supervisor", lambda **kw: MagicMock())

    from app.services.pipeline_builder import build_team_from_definition
    defn = {
        "nodes": [{"id": "unknown_xyz", "type": "unknown_xyz", "label": "X", "model": "claude"}],
        "edges": [{"from": "unknown_xyz", "to": "unknown_xyz"}],
    }
    with pytest.raises(ValueError, match="Unknown agent type"):
        build_team_from_definition(defn)


def test_build_team_custom_agent_with_system_prompt(monkeypatch):
    """Node with agent_cfg.system_prompt instantiates TemplateAgent instead of raising."""
    import antcrew
    import antcrew.agents.registry as _registry
    from unittest.mock import MagicMock
    from antcrew.agents.template_agent import TemplateAgent

    mock_llm = MagicMock()
    monkeypatch.setattr(antcrew, "build_llm", lambda *a, **kw: mock_llm)
    monkeypatch.setattr(antcrew, "Supervisor", lambda **kw: MagicMock())
    # pm is a real registered agent — mock it too to avoid real LLM
    monkeypatch.setattr(_registry, "instantiate_agent", lambda name, llm: (
        None if name == "custom_1" else MagicMock()
    ))

    from app.services.pipeline_builder import build_team_from_definition
    agents, _ = build_team_from_definition(_CUSTOM_DEFN)

    assert "custom_1" in agents
    assert isinstance(agents["custom_1"], TemplateAgent)
    assert agents["custom_1"].name == "custom_1"


def test_build_team_parallel_node(monkeypatch):
    """Node with type='parallel' builds a ParallelGroup with one agent per member."""
    import antcrew
    import antcrew.agents.registry as _registry
    from unittest.mock import MagicMock
    from antcrew.core.supervisor import ParallelGroup

    monkeypatch.setattr(antcrew, "build_llm", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(antcrew, "Supervisor", lambda **kw: MagicMock())
    monkeypatch.setattr(_registry, "instantiate_agent", lambda name, llm: MagicMock())

    from app.services.pipeline_builder import build_team_from_definition
    defn = {
        "nodes": [
            {"id": "pm", "type": "pm", "label": "PM", "model": "claude"},
            {
                "id": "coding",
                "type": "parallel",
                "label": "Coding",
                "model": "claude",
                "members": [
                    {"type": "backend_dev"},
                    {"type": "frontend_dev"},
                ],
            },
        ],
        "edges": [{"from": "pm", "to": "coding"}],
    }
    agents, _ = build_team_from_definition(defn)

    assert "pm" in agents
    assert "coding" in agents
    assert isinstance(agents["coding"], ParallelGroup)
    assert len(agents["coding"]._agents) == 2


def test_build_team_parallel_node_no_members_raises(monkeypatch):
    """Parallel node with empty members list must raise ValueError."""
    import antcrew
    import antcrew.agents.registry as _registry
    from unittest.mock import MagicMock

    monkeypatch.setattr(antcrew, "build_llm", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(antcrew, "Supervisor", lambda **kw: MagicMock())
    monkeypatch.setattr(_registry, "instantiate_agent", lambda name, llm: MagicMock())

    from app.services.pipeline_builder import build_team_from_definition
    defn = {
        "nodes": [
            {"id": "coding", "type": "parallel", "label": "Coding", "model": "claude", "members": []},
        ],
        "edges": [{"from": "coding", "to": "coding"}],
    }
    with pytest.raises(ValueError, match="no members"):
        build_team_from_definition(defn)


def test_build_team_parallel_node_unknown_member_type_raises(monkeypatch):
    """Unknown agent type inside a parallel node must raise ValueError."""
    import antcrew
    import antcrew.agents.registry as _registry
    from unittest.mock import MagicMock

    monkeypatch.setattr(antcrew, "build_llm", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(antcrew, "Supervisor", lambda **kw: MagicMock())
    monkeypatch.setattr(_registry, "instantiate_agent", lambda name, llm: None)

    from app.services.pipeline_builder import build_team_from_definition
    defn = {
        "nodes": [
            {
                "id": "coding", "type": "parallel", "label": "Coding", "model": "claude",
                "members": [{"type": "unknown_xyz"}],
            },
        ],
        "edges": [{"from": "coding", "to": "coding"}],
    }
    with pytest.raises(ValueError, match="Unknown agent type in parallel node"):
        build_team_from_definition(defn)


def test_build_team_custom_agent_missing_system_prompt_raises(monkeypatch):
    """agent_cfg present but system_prompt empty still raises ValueError."""
    import antcrew
    import antcrew.agents.registry as _registry
    from unittest.mock import MagicMock
    monkeypatch.setattr(antcrew, "build_llm", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(antcrew, "Supervisor", lambda **kw: MagicMock())
    monkeypatch.setattr(_registry, "instantiate_agent", lambda name, llm: None)

    from app.services.pipeline_builder import build_team_from_definition
    defn = {
        "nodes": [{"id": "custom_1", "type": "custom_1", "label": "X", "model": "claude",
                   "agent_cfg": {"system_prompt": "   "}}],
        "edges": [{"from": "custom_1", "to": "custom_1"}],
    }
    with pytest.raises(ValueError, match="Unknown agent type"):
        build_team_from_definition(defn)
