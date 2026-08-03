"""Round 19 tests — admin panel, promo system, PLATFORM_API_KEY startup check.

Covers:
A1  require_platform_admin blocks non-admin session (403)
A2  require_platform_admin blocks missing session (401)
A3  require_platform_admin passes for is_platform_admin=True user
A4  GET /admin/stats returns correct counts
A5  GET /admin/workspaces returns list
A6  PATCH /admin/workspaces/{id} changes override, locked, is_trial
A7  PATCH /admin/workspaces/{id} → 404 for unknown workspace
A8  GET /admin/campaigns returns list
A9  POST /admin/campaigns creates campaign
A10 PATCH /admin/campaigns/{id} toggles active
A11 DELETE /admin/campaigns/{id} removes campaign
A12 GET /admin/users returns user list
A13 PATCH /admin/users/{id}/admin toggles is_platform_admin
A14 POST /admin/make-admin with valid token → grants admin
A15 POST /admin/make-admin with wrong token → 403
A16 POST /admin/make-admin for unknown email → 404
B1  get_cost_multiplier: override wins over everything
B2  get_cost_multiplier: campaign applies when not locked
B3  get_cost_multiplier: campaign skipped when locked
B4  get_cost_multiplier: trial multiplier when no override/campaign
B5  get_cost_multiplier: default managed when no trial/override/campaign
C1  GET /public/active-promo → inactive when no campaign
C2  GET /public/active-promo → active when 0x campaign running
C3  GET /public/active-promo → inactive when campaign multiplier != 0
C4  POST /trial/register → 404 when no active promo
C5  POST /trial/register → 201 when promo active
D1  startup _check_platform_api_key_prod raises in prod
D2  startup _check_platform_api_key_prod passes in dev
D3  startup _check_platform_api_key_prod passes when key not set in prod
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.admin import Campaign
from app.models.auth import User, UserSession
from app.models.run import Workspace

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_CSRF = "test-csrf-admin"
_CSRF_COOKIE = {"csrf_token": _CSRF}
_CSRF_HEADER = {"X-CSRF-Token": _CSRF}
_ADMIN_TOKEN = "super-secret-admin-token"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def _make_admin_session(
    session: AsyncSession,
    email: str = "admin@example.com",
    is_platform_admin: bool = True,
) -> tuple[User, str]:
    """Create User + UserSession.  Returns (user, raw_token)."""
    user = User(
        email=email,
        password_hash=_sha256("pw"),
        is_platform_admin=is_platform_admin,
    )
    session.add(user)
    await session.flush()

    raw_token = secrets.token_urlsafe(32)
    token_hash = _sha256(raw_token)
    us = UserSession(
        token_hash=token_hash,
        user_id=user.id,
        expires_at=_utcnow() + timedelta(days=1),
        revoked=False,
    )
    session.add(us)
    await session.commit()
    await session.refresh(user)
    return user, raw_token


async def _make_workspace(session: AsyncSession, name: str = "ws", slug: str = "ws") -> Workspace:
    ws = Workspace(name=name, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


async def _make_campaign(
    session: AsyncSession,
    name: str = "promo",
    multiplier: float = 0.0,
    active: bool = True,
    offset_hours: int = 0,
) -> Campaign:
    now = _utcnow()
    c = Campaign(
        name=name,
        multiplier=multiplier,
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=48) + timedelta(hours=offset_hours),
        active=active,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return c


def _admin_cookies(raw_token: str) -> dict:
    return {"antcrew_session": raw_token, **_CSRF_COOKIE}


# ===========================================================================
# A — Admin auth and CRUD
# ===========================================================================

@pytest.mark.asyncio
async def test_a1_non_admin_session_rejected(client: AsyncClient, session: AsyncSession):
    """Non-admin user → 403 on all /admin/* routes."""
    _, token = await _make_admin_session(session, "notadmin@example.com", is_platform_admin=False)
    r = await client.get("/admin/stats", cookies={"antcrew_session": token})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a2_missing_session_rejected(client: AsyncClient):
    """No session cookie → 401."""
    r = await client.get("/admin/stats")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_a3_admin_session_accepted(client: AsyncClient, session: AsyncSession):
    """Admin user session → 200 on /admin/stats."""
    _, token = await _make_admin_session(session)
    r = await client.get("/admin/stats", cookies={"antcrew_session": token})
    assert r.status_code == 200
    d = r.json()
    assert "workspaces" in d
    assert "runs" in d
    assert "revenue" in d


@pytest.mark.asyncio
async def test_a4_stats_counts(client: AsyncClient, session: AsyncSession):
    """Stats reflect actual workspace counts."""
    _, token = await _make_admin_session(session)
    await _make_workspace(session, "Trial WS", "trial-ws")
    paid = Workspace(name="Paid WS", slug="paid-ws", is_trial=False)
    session.add(paid)
    await session.commit()

    r = await client.get("/admin/stats", cookies={"antcrew_session": token})
    d = r.json()
    assert d["workspaces"]["total"] >= 2
    assert d["workspaces"]["trial"] >= 1
    assert d["workspaces"]["paid"] >= 1


@pytest.mark.asyncio
async def test_a5_workspaces_list(client: AsyncClient, session: AsyncSession):
    """GET /admin/workspaces returns list with expected fields."""
    _, token = await _make_admin_session(session)
    await _make_workspace(session, "Test WS", "test-ws")

    r = await client.get("/admin/workspaces", cookies={"antcrew_session": token})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    row = data[0]
    assert "id" in row
    assert "name" in row
    assert "cost_multiplier_override" in row
    assert "multiplier_locked" in row


@pytest.mark.asyncio
async def test_a6_patch_workspace(client: AsyncClient, session: AsyncSession):
    """PATCH /admin/workspaces/{id} updates override, locked, and is_trial."""
    _, token = await _make_admin_session(session)
    ws = await _make_workspace(session, "Patch WS", "patch-ws")

    r = await client.patch(
        f"/admin/workspaces/{ws.id}",
        json={"cost_multiplier_override": 1.5, "multiplier_locked": True, "is_trial": False},
        cookies=_admin_cookies(token),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cost_multiplier_override"] == 1.5
    assert d["multiplier_locked"] is True
    assert d["is_trial"] is False


@pytest.mark.asyncio
async def test_a7_patch_unknown_workspace(client: AsyncClient, session: AsyncSession):
    """PATCH /admin/workspaces/999999 → 404."""
    _, token = await _make_admin_session(session)
    r = await client.patch(
        "/admin/workspaces/999999",
        json={"is_trial": False},
        cookies=_admin_cookies(token),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a8_campaigns_list(client: AsyncClient, session: AsyncSession):
    """GET /admin/campaigns returns list."""
    _, token = await _make_admin_session(session)
    await _make_campaign(session, "Summer promo")

    r = await client.get("/admin/campaigns", cookies={"antcrew_session": token})
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert data[0]["name"] == "Summer promo"


@pytest.mark.asyncio
async def test_a9_create_campaign(client: AsyncClient, session: AsyncSession):
    """POST /admin/campaigns creates a campaign."""
    _, token = await _make_admin_session(session)
    now = _utcnow()

    r = await client.post(
        "/admin/campaigns",
        json={
            "name": "Q3 promo",
            "multiplier": 0.8,
            "starts_at": (now - timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(days=30)).isoformat(),
            "target": "all",
        },
        cookies=_admin_cookies(token),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["name"] == "Q3 promo"
    assert d["multiplier"] == 0.8
    assert d["active"] is True


@pytest.mark.asyncio
async def test_a9b_create_campaign_invalid_dates(client: AsyncClient, session: AsyncSession):
    """POST /admin/campaigns with ends_at <= starts_at → 422."""
    _, token = await _make_admin_session(session)
    now = _utcnow()
    r = await client.post(
        "/admin/campaigns",
        json={
            "name": "Bad dates",
            "multiplier": 0.8,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": now.isoformat(),
            "target": "all",
        },
        cookies=_admin_cookies(token),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_a10_toggle_campaign_active(client: AsyncClient, session: AsyncSession):
    """PATCH /admin/campaigns/{id} toggles active flag."""
    _, token = await _make_admin_session(session)
    camp = await _make_campaign(session, "Toggle test", active=True)

    r = await client.patch(
        f"/admin/campaigns/{camp.id}",
        json={"active": False},
        cookies=_admin_cookies(token),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200
    assert r.json()["active"] is False


@pytest.mark.asyncio
async def test_a11_delete_campaign(client: AsyncClient, session: AsyncSession):
    """DELETE /admin/campaigns/{id} removes the campaign."""
    _, token = await _make_admin_session(session)
    camp = await _make_campaign(session, "To delete")

    r = await client.delete(
        f"/admin/campaigns/{camp.id}",
        cookies=_admin_cookies(token),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 204

    # Confirm it's gone
    r2 = await client.get("/admin/campaigns", cookies={"antcrew_session": token})
    ids = [c["id"] for c in r2.json()]
    assert camp.id not in ids


@pytest.mark.asyncio
async def test_a12_users_list(client: AsyncClient, session: AsyncSession):
    """GET /admin/users returns user list with is_platform_admin field."""
    _, token = await _make_admin_session(session)
    r = await client.get("/admin/users", cookies={"antcrew_session": token})
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert "is_platform_admin" in data[0]


@pytest.mark.asyncio
async def test_a13_toggle_user_admin(client: AsyncClient, session: AsyncSession):
    """PATCH /admin/users/{id}/admin toggles is_platform_admin."""
    admin, token = await _make_admin_session(session)
    target, _ = await _make_admin_session(session, "target@example.com", is_platform_admin=False)

    r = await client.patch(
        f"/admin/users/{target.id}/admin?is_admin=true",
        cookies=_admin_cookies(token),
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200
    assert r.json()["is_platform_admin"] is True

    # Toggle back
    r2 = await client.patch(
        f"/admin/users/{target.id}/admin?is_admin=false",
        cookies=_admin_cookies(token),
        headers=_CSRF_HEADER,
    )
    assert r2.json()["is_platform_admin"] is False


@pytest.mark.asyncio
async def test_a14_make_admin_valid_token(client: AsyncClient, session: AsyncSession, monkeypatch):
    """POST /admin/make-admin with valid token grants admin."""
    monkeypatch.setenv("PLATFORM_ADMIN_TOKEN", _ADMIN_TOKEN)
    user = User(email="future-admin@example.com", password_hash=_sha256("pw"))
    session.add(user)
    await session.commit()
    await session.refresh(user)

    r = await client.post(
        "/admin/make-admin",
        json={"email": "future-admin@example.com", "token": _ADMIN_TOKEN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_platform_admin"] is True


@pytest.mark.asyncio
async def test_a15_make_admin_wrong_token(client: AsyncClient, session: AsyncSession, monkeypatch):
    """POST /admin/make-admin with wrong token → 403."""
    monkeypatch.setenv("PLATFORM_ADMIN_TOKEN", _ADMIN_TOKEN)
    r = await client.post(
        "/admin/make-admin",
        json={"email": "anyone@example.com", "token": "wrong-token"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_a16_make_admin_unknown_email(client: AsyncClient, session: AsyncSession, monkeypatch):
    """POST /admin/make-admin for nonexistent user → 404."""
    monkeypatch.setenv("PLATFORM_ADMIN_TOKEN", _ADMIN_TOKEN)
    r = await client.post(
        "/admin/make-admin",
        json={"email": "nobody@example.com", "token": _ADMIN_TOKEN},
    )
    assert r.status_code == 404


# ===========================================================================
# B — Cost multiplier logic
# ===========================================================================

def test_b1_override_wins_over_everything():
    """multiplier_override beats trial, campaign, and mode default."""
    from app.core.byok import get_cost_multiplier
    result = get_cost_multiplier(
        "managed",
        is_trial=True,
        multiplier_override=2.5,
        multiplier_locked=False,
        campaign_multiplier=0.0,
    )
    assert result == 2.5


def test_b2_campaign_applies_when_not_locked():
    """Active campaign multiplier applies to non-locked workspace."""
    from app.core.byok import get_cost_multiplier
    result = get_cost_multiplier(
        "managed",
        is_trial=False,
        multiplier_override=None,
        multiplier_locked=False,
        campaign_multiplier=0.0,
    )
    assert result == 0.0


def test_b3_campaign_skipped_when_locked():
    """Locked workspace ignores active campaign — uses default instead."""
    from app.core.byok import get_cost_multiplier, MANAGED_COST_MULTIPLIER
    result = get_cost_multiplier(
        "managed",
        is_trial=False,
        multiplier_override=None,
        multiplier_locked=True,
        campaign_multiplier=0.0,
    )
    assert result == MANAGED_COST_MULTIPLIER


def test_b4_trial_multiplier_no_override_no_campaign():
    """Trial workspace without override or campaign uses TRIAL_MULTIPLIER."""
    from app.core.byok import get_cost_multiplier, TRIAL_MULTIPLIER
    result = get_cost_multiplier("managed", is_trial=True)
    assert result == TRIAL_MULTIPLIER


def test_b5_managed_default():
    """Managed workspace with no override/campaign/trial uses ×3.0."""
    from app.core.byok import get_cost_multiplier, MANAGED_COST_MULTIPLIER
    result = get_cost_multiplier("managed", is_trial=False)
    assert result == MANAGED_COST_MULTIPLIER


def test_b5b_byok_default():
    """BYOK workspace with no overrides uses ×0.4."""
    from app.core.byok import get_cost_multiplier, BYOK_SERVICE_MULTIPLIER
    result = get_cost_multiplier("byok", is_trial=False)
    assert result == BYOK_SERVICE_MULTIPLIER


# ===========================================================================
# C — Public promo endpoint and trial registration
# ===========================================================================

@pytest.mark.asyncio
async def test_c1_no_active_promo(client: AsyncClient, session: AsyncSession):
    """GET /public/active-promo → promo_active=false when no campaign."""
    r = await client.get("/public/active-promo")
    assert r.status_code == 200
    d = r.json()
    assert d["promo_active"] is False
    assert d["ends_at"] is None


@pytest.mark.asyncio
async def test_c2_active_promo(client: AsyncClient, session: AsyncSession):
    """GET /public/active-promo → promo_active=true when 0x campaign running."""
    await _make_campaign(session, "Free promo", multiplier=0.0, active=True)
    r = await client.get("/public/active-promo")
    assert r.status_code == 200
    d = r.json()
    assert d["promo_active"] is True
    assert d["multiplier"] == 0.0
    assert d["ends_at"] is not None


@pytest.mark.asyncio
async def test_c3_promo_ignores_nonzero_campaigns(client: AsyncClient, session: AsyncSession):
    """GET /public/active-promo → inactive when only non-zero campaigns exist."""
    await _make_campaign(session, "Discount", multiplier=1.5, active=True)
    r = await client.get("/public/active-promo")
    assert r.status_code == 200
    assert r.json()["promo_active"] is False


@pytest.mark.asyncio
async def test_c4_trial_register_blocked_without_promo(client: AsyncClient, session: AsyncSession):
    """POST /trial/register → 404 when no active 0x campaign."""
    r = await client.post(
        "/trial/register",
        json={"name": "ACME Inc", "email": "acme@example.com"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_c5_trial_register_succeeds_with_promo(client: AsyncClient, session: AsyncSession):
    """POST /trial/register → 201 when active 0x campaign is running."""
    await _make_campaign(session, "Free trial", multiplier=0.0, active=True)

    r = await client.post(
        "/trial/register",
        json={"name": "ACME Inc", "email": "acme2@example.com"},
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert "key" in d
    assert d["trial_credit_usd"] > 0


@pytest.mark.asyncio
async def test_c6_promo_ignored_when_inactive(client: AsyncClient, session: AsyncSession):
    """Inactive campaign → promo_active=false even if dates match."""
    await _make_campaign(session, "Inactive", multiplier=0.0, active=False)
    r = await client.get("/public/active-promo")
    assert r.json()["promo_active"] is False


# ===========================================================================
# D — Startup checks
# ===========================================================================

@pytest.mark.asyncio
async def test_d1_platform_api_key_blocked_in_prod(monkeypatch):
    """PLATFORM_API_KEY set + APP_ENV=prod → RuntimeError at startup."""
    monkeypatch.setenv("PLATFORM_API_KEY", "some-key")
    monkeypatch.setenv("APP_ENV", "prod")

    from app.core.startup import _check_platform_api_key_prod
    with pytest.raises(RuntimeError, match="PLATFORM_API_KEY"):
        await _check_platform_api_key_prod()


@pytest.mark.asyncio
async def test_d2_platform_api_key_ok_in_dev(monkeypatch):
    """PLATFORM_API_KEY set in dev → no error."""
    monkeypatch.setenv("PLATFORM_API_KEY", "dev-key")
    monkeypatch.setenv("APP_ENV", "dev")

    from app.core.startup import _check_platform_api_key_prod
    await _check_platform_api_key_prod()  # must not raise


@pytest.mark.asyncio
async def test_d3_no_platform_api_key_in_prod_ok(monkeypatch):
    """No PLATFORM_API_KEY in prod → no error."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")

    from app.core.startup import _check_platform_api_key_prod
    await _check_platform_api_key_prod()  # must not raise
