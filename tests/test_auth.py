"""Tests for API key auth."""
from __future__ import annotations



async def test_health_always_open(client):
    """Health endpoint never requires auth."""
    r = await client.get("/health")
    assert r.status_code == 200


async def test_no_key_required_when_env_not_set(client, monkeypatch):
    """When PLATFORM_API_KEY is unset, all endpoints are open."""
    monkeypatch.delenv("PLATFORM_API_KEY", raising=False)
    r = await client.get("/runs/")
    assert r.status_code == 200


async def test_valid_key_accepted(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.get("/runs/", headers={"X-Api-Key": "test-secret"})
    assert r.status_code == 200


async def test_wrong_key_rejected(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.get("/runs/", headers={"X-Api-Key": "wrong"})
    assert r.status_code == 401


async def test_missing_key_rejected(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.get("/runs/")
    assert r.status_code == 401


async def test_tickets_protected(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.get("/tickets/")
    assert r.status_code == 401


async def test_run_endpoint_protected(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_API_KEY", "test-secret")
    r = await client.post("/run/", json={"team": "DevTeam", "request": "x"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Security: CSRF protection on PATCH /auth/profile
# ---------------------------------------------------------------------------

async def test_profile_csrf_required_when_session_cookie_present(client, session):
    """PATCH /auth/profile must return 403 when a session cookie is present but no CSRF token."""
    import hashlib
    import secrets
    from app.models.run import User, ApiKey, UserSession
    from datetime import datetime, timedelta, timezone

    # Create a minimal user + session directly in the DB
    user = User(
        email="csrf-test@example.com",
        password_hash="$2b$12$" + "a" * 53,  # dummy valid bcrypt hash format
        display_name="CSRF Test",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    api_key = ApiKey(label="csrf-key", key_hash=hashlib.sha256(b"csrf-key").hexdigest())
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    session_token = secrets.token_urlsafe(32)
    user_session = UserSession(
        token=session_token,
        user_id=user.id,
        api_key_id=api_key.id,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        revoked=False,
    )
    session.add(user_session)
    await session.commit()

    # Request with session cookie but without CSRF header → 403
    r = await client.patch(
        "/auth/profile",
        json={"display_name": "New Name"},
        cookies={"antcrew_session": session_token},
    )
    assert r.status_code == 403, f"Expected 403 (CSRF missing), got {r.status_code}"


async def test_profile_csrf_skipped_for_api_key_requests(client):
    """API-key authenticated requests to PATCH /auth/profile skip CSRF (not a browser session)."""
    # Without session cookie, require_csrf is a no-op → endpoint falls through to 401 (not authenticated)
    r = await client.patch("/auth/profile", json={"display_name": "x"})
    assert r.status_code == 401
