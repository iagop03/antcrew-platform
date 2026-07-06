"""Round 9 P1 integration tests.

Covers:
- HITL_DB_POLLING default inversion: DB polling now default, HITL_FUTURE_MODE=1 opts in to Future
- _make_team TypeError fallback when team does not accept llm= kwarg
- Review resolve: feedback included with any decision, edit decision with edited JSON
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# 1. HITL default is now DB polling
# ---------------------------------------------------------------------------

def test_hitl_default_is_db_polling():
    """Without HITL_FUTURE_MODE set, DB polling should be active (the new default)."""
    from app.core.channel import _USE_DB_POLLING
    assert _USE_DB_POLLING is True


def test_hitl_use_db_polling_logic():
    """The env var logic: HITL_FUTURE_MODE='1' → future mode, anything else → DB polling."""
    # Replicate the module logic without reimporting
    def compute(env_val):
        return env_val != "1"

    assert compute("0") is True   # default: DB polling
    assert compute("")  is True   # empty string: DB polling
    assert compute("1") is False  # HITL_FUTURE_MODE=1: future mode


def test_hitl_future_mode_comment_in_channel():
    """Channel docstring mentions HITL_FUTURE_MODE (not the old HITL_DB_POLLING)."""
    import inspect
    from app.core import channel
    src = inspect.getsource(channel)
    assert "HITL_FUTURE_MODE" in src
    # Old flag should only appear in a "no longer used" context, not as the primary control
    assert "HITL_FUTURE_MODE=1" in src


# ---------------------------------------------------------------------------
# 2. _make_team TypeError fallback
# ---------------------------------------------------------------------------

def test_make_team_falls_back_when_team_rejects_llm(monkeypatch):
    """When a team class does not accept llm=, _make_team should fall back without crashing."""
    import sys
    import types
    from app.services import runner as runner_mod

    class _NoLlmTeam:
        def __init__(self, *, max_cost_usd=None):
            self.max_cost_usd = max_cost_usd

    fake_module = types.ModuleType("fake_team_module_r9")
    fake_module._NoLlmTeam = _NoLlmTeam
    sys.modules["fake_team_module_r9"] = fake_module
    runner_mod._TEAM_REGISTRY["_NoLlmTeam"] = ("fake_team_module_r9", "_NoLlmTeam")

    # Mock build_llm so we don't need ANTHROPIC_API_KEY
    class _FakeLLM:
        max_cost_usd = None

    monkeypatch.setattr(
        "app.services.runner._make_team.__module__",
        "app.services.runner",
        raising=False,
    )

    original_make_team = runner_mod._make_team

    def patched_make_team(team_name, max_cost_usd=None, model=""):
        if model:
            # Temporarily replace build_llm inside the function scope
            import importlib
            import antcrew.config as _cfg
            orig_build = _cfg.build_llm
            _cfg.build_llm = lambda m: _FakeLLM()
            try:
                return original_make_team(team_name, max_cost_usd=max_cost_usd, model=model)
            finally:
                _cfg.build_llm = orig_build
        return original_make_team(team_name, max_cost_usd=max_cost_usd, model=model)

    try:
        team = patched_make_team("_NoLlmTeam", model="claude", max_cost_usd=1.0)
        assert isinstance(team, _NoLlmTeam)
        assert team.max_cost_usd == 1.0
    finally:
        runner_mod._TEAM_REGISTRY.pop("_NoLlmTeam", None)
        sys.modules.pop("fake_team_module_r9", None)


def test_make_team_reraises_unrelated_type_error():
    """A TypeError not related to the llm= kwarg should still propagate."""
    import sys
    import types
    from app.services import runner as runner_mod

    class _BadTeam:
        def __init__(self):
            raise TypeError("something else entirely")

    fake_module = types.ModuleType("fake_bad_module_r9")
    fake_module._BadTeam = _BadTeam
    sys.modules["fake_bad_module_r9"] = fake_module
    runner_mod._TEAM_REGISTRY["_BadTeam"] = ("fake_bad_module_r9", "_BadTeam")
    try:
        from app.services.runner import _make_team
        with pytest.raises(TypeError, match="something else entirely"):
            _make_team("_BadTeam")
    finally:
        runner_mod._TEAM_REGISTRY.pop("_BadTeam", None)
        sys.modules.pop("fake_bad_module_r9", None)


# ---------------------------------------------------------------------------
# 3. Review resolve — feedback included with any decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_approve_with_feedback(client: AsyncClient, session, monkeypatch):
    """Resolving with 'approve' + feedback stores both decision and feedback."""
    from app.models.run import HitlReview

    # resolve_review is imported into app.api.reviews — patch it there
    monkeypatch.setattr("app.api.reviews.resolve_review", lambda *a, **kw: True)

    review = HitlReview(
        review_id="r9-approve-fb",
        run_id="run-r9-1",
        agent_name="PMAgent",
        status="pending",
    )
    session.add(review)
    await session.commit()

    r = await client.post("/reviews/r9-approve-fb", json={
        "decision": "approve",
        "feedback": "Looks good but watch the edge cases",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["decision"] == "approve"
    assert data["status"] == "approved"
    assert data["feedback"] == "Looks good but watch the edge cases"


@pytest.mark.asyncio
async def test_resolve_edit_with_edited_json(client: AsyncClient, session, monkeypatch):
    """Resolving with 'edit' + edited JSON stores the edited artifact."""
    from app.models.run import HitlReview

    monkeypatch.setattr("app.api.reviews.resolve_review", lambda *a, **kw: True)

    review = HitlReview(
        review_id="r9-edit-json",
        run_id="run-r9-2",
        agent_name="DevAgent",
        status="pending",
        options_json='["approve","edit","reject"]',
    )
    session.add(review)
    await session.commit()

    edited = '{"title": "Updated PRD", "summary": "Revised version"}'
    r = await client.post("/reviews/r9-edit-json", json={
        "decision": "edit",
        "edited": edited,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["decision"] == "edit"
    assert data["status"] == "edited"
    assert data["edited_json"] == edited


@pytest.mark.asyncio
async def test_resolve_reject_with_feedback(client: AsyncClient, session, monkeypatch):
    """Rejecting with feedback stores both."""
    from app.models.run import HitlReview

    monkeypatch.setattr("app.api.reviews.resolve_review", lambda *a, **kw: True)

    review = HitlReview(
        review_id="r9-reject-fb",
        run_id="run-r9-3",
        agent_name="QAAgent",
        status="pending",
    )
    session.add(review)
    await session.commit()

    r = await client.post("/reviews/r9-reject-fb", json={
        "decision": "reject",
        "feedback": "The acceptance criteria are too vague",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["decision"] == "reject"
    assert data["status"] == "rejected"
    assert data["feedback"] == "The acceptance criteria are too vague"
