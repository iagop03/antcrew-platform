"""Tests for MFA/TOTP — GET+POST /auth/mfa/* and login challenge flow."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
import pyotp
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import User, ApiKey, UserSession, Workspace

_CSRF = "test-csrf-token-for-mfa-tests"
_CSRF_COOKIE = {"antcrew_session": "", "csrf_token": _CSRF}  # session filled per-test
_CSRF_HEADER = {"X-CSRF-Token": _CSRF}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def _make_user_session(session: AsyncSession, email: str = "mfa-test@example.com") -> tuple:
    """Create User + Workspace + ApiKey + UserSession.
    Returns (user, raw_session_token).
    Session uses plaintext `token` field (pre-033 fallback) so _resolve_session finds it.
    """
    ws = Workspace(name=f"ws-{email}", slug=f"ws-{email.split('@')[0]}")
    session.add(ws)
    await session.flush()

    user = User(
        email=email,
        password_hash=_sha256("test-password"),  # not bcrypt — tests don't verify passwords
    )
    session.add(user)
    await session.flush()

    key = ApiKey(
        label=f"key-{email}",
        key_hash=_sha256(f"key-{email}"),
        workspace_id=ws.id,
        role="admin",
        user_id=user.id,
        email=email,
    )
    session.add(key)
    await session.flush()

    raw_token = secrets.token_urlsafe(32)
    user_session = UserSession(
        token=raw_token,                          # plaintext — uses pre-033 fallback lookup
        user_id=user.id,
        api_key_id=key.id,
        expires_at=_utcnow() + timedelta(days=1),
        revoked=False,
    )
    session.add(user_session)
    await session.commit()
    await session.refresh(user)
    return user, raw_token


# ---------------------------------------------------------------------------
# GET /auth/mfa/setup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mfa_setup_returns_secret_and_uri(client, session: AsyncSession):
    """Authenticated request → returns TOTP secret and provisioning URI."""
    user, token = await _make_user_session(session, "setup@example.com")

    r = await client.get("/auth/mfa/setup", cookies={"antcrew_session": token})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "secret" in d
    assert "provisioning_uri" in d
    assert "mfa_enabled" in d
    assert d["mfa_enabled"] is False
    # provisioning URI must be an otpauth:// URL
    assert d["provisioning_uri"].startswith("otpauth://totp/")
    # secret must be a valid base32 string (pyotp can build a TOTP from it)
    totp = pyotp.TOTP(d["secret"])
    assert len(totp.now()) == 6


@pytest.mark.asyncio
async def test_mfa_setup_requires_auth(client):
    """No session cookie → 401."""
    r = await client.get("/auth/mfa/setup")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/mfa/enable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mfa_enable_with_valid_code(client, session: AsyncSession):
    """Valid secret + matching TOTP code → mfa_enabled=True."""
    user, token = await _make_user_session(session, "enable@example.com")

    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()

    r = await client.post(
        "/auth/mfa/enable",
        json={"secret": secret, "code": code},
        cookies={"antcrew_session": token, "csrf_token": _CSRF},
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200, r.text
    assert r.json()["mfa_enabled"] is True

    # Verify the secret was persisted
    await session.refresh(user)
    assert user.mfa_enabled is True
    assert user.totp_secret == secret


@pytest.mark.asyncio
async def test_mfa_enable_rejects_wrong_code(client, session: AsyncSession):
    """Wrong TOTP code → 400."""
    user, token = await _make_user_session(session, "enable-bad@example.com")

    secret = pyotp.random_base32()

    r = await client.post(
        "/auth/mfa/enable",
        json={"secret": secret, "code": "000000"},
        cookies={"antcrew_session": token, "csrf_token": _CSRF},
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_mfa_enable_requires_auth(client):
    """No session → 401."""
    secret = pyotp.random_base32()
    r = await client.post(
        "/auth/mfa/enable",
        json={"secret": secret, "code": "123456"},
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/mfa/disable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mfa_disable_with_valid_code(client, session: AsyncSession):
    """User with MFA enabled + valid code → mfa_enabled=False."""
    user, token = await _make_user_session(session, "disable@example.com")

    # Enable MFA directly in DB
    secret = pyotp.random_base32()
    user.totp_secret = secret
    user.mfa_enabled = True
    session.add(user)
    await session.commit()

    code = pyotp.TOTP(secret).now()

    r = await client.post(
        "/auth/mfa/disable",
        json={"code": code},
        cookies={"antcrew_session": token, "csrf_token": _CSRF},
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200, r.text
    assert r.json()["mfa_enabled"] is False

    await session.refresh(user)
    assert user.mfa_enabled is False
    assert user.totp_secret is None


@pytest.mark.asyncio
async def test_mfa_disable_rejects_wrong_code(client, session: AsyncSession):
    """Wrong TOTP code → 400."""
    user, token = await _make_user_session(session, "disable-bad@example.com")

    secret = pyotp.random_base32()
    user.totp_secret = secret
    user.mfa_enabled = True
    session.add(user)
    await session.commit()

    r = await client.post(
        "/auth/mfa/disable",
        json={"code": "000000"},
        cookies={"antcrew_session": token, "csrf_token": _CSRF},
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_mfa_disable_noop_when_not_enabled(client, session: AsyncSession):
    """Disabling MFA when not enabled → graceful 200 with mfa_enabled=False."""
    user, token = await _make_user_session(session, "disable-noop@example.com")

    r = await client.post(
        "/auth/mfa/disable",
        json={"code": "123456"},
        cookies={"antcrew_session": token, "csrf_token": _CSRF},
        headers=_CSRF_HEADER,
    )
    assert r.status_code == 200
    assert r.json()["mfa_enabled"] is False


# ---------------------------------------------------------------------------
# POST /auth/login — MFA gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_returns_mfa_required_when_enabled(client, session: AsyncSession):
    """Login with MFA-enabled user → mfa_required=True + mfa_token, no session cookie."""
    import bcrypt
    from app.models.run import Workspace

    ws = Workspace(name="mfa-login-ws", slug="mfa-login-ws")
    session.add(ws)
    await session.flush()

    raw_password = "correct-horse-battery"
    user = User(
        email="mfa-login@example.com",
        password_hash=bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt(rounds=4)).decode(),
        mfa_enabled=True,
        totp_secret=pyotp.random_base32(),
    )
    session.add(user)
    await session.flush()

    key = ApiKey(
        label="mfa-login-key",
        key_hash=_sha256("mfa-login-key-raw"),
        workspace_id=ws.id,
        role="admin",
        user_id=user.id,
    )
    session.add(key)
    await session.commit()

    r = await client.post("/auth/login", json={
        "email": "mfa-login@example.com",
        "password": raw_password,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("mfa_required") is True
    assert "mfa_token" in d
    # No session cookie should be set
    assert "antcrew_session" not in r.cookies


# ---------------------------------------------------------------------------
# POST /auth/mfa/challenge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mfa_challenge_issues_session(client, session: AsyncSession):
    """Valid mfa_token + correct TOTP code → session cookie issued."""
    from app.api.auth_session import _sign_mfa_token

    user, _ = await _make_user_session(session, "challenge@example.com")

    secret = pyotp.random_base32()
    user.totp_secret = secret
    user.mfa_enabled = True
    session.add(user)
    await session.commit()

    mfa_token = _sign_mfa_token(user.id)
    code = pyotp.TOTP(secret).now()

    r = await client.post("/auth/mfa/challenge", json={
        "mfa_token": mfa_token,
        "code": code,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert "workspace_id" in d
    assert "antcrew_session" in r.cookies


@pytest.mark.asyncio
async def test_mfa_challenge_rejects_wrong_code(client, session: AsyncSession):
    """Wrong TOTP code → 400."""
    from app.api.auth_session import _sign_mfa_token

    user, _ = await _make_user_session(session, "challenge-bad@example.com")

    secret = pyotp.random_base32()
    user.totp_secret = secret
    user.mfa_enabled = True
    session.add(user)
    await session.commit()

    mfa_token = _sign_mfa_token(user.id)

    r = await client.post("/auth/mfa/challenge", json={
        "mfa_token": mfa_token,
        "code": "000000",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_mfa_challenge_rejects_invalid_token(client):
    """Tampered mfa_token → 401."""
    r = await client.post("/auth/mfa/challenge", json={
        "mfa_token": "not-a-valid-token",
        "code": "123456",
    })
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_mfa_sign_verify_token_roundtrip():
    """_sign_mfa_token and _verify_mfa_token are inverses for a valid user_id."""
    from app.api.auth_session import _sign_mfa_token, _verify_mfa_token

    token = _sign_mfa_token(42)
    assert _verify_mfa_token(token) == 42


@pytest.mark.asyncio
async def test_mfa_verify_token_rejects_tampered():
    """Tampered token (changed user_id) → None."""
    from app.api.auth_session import _sign_mfa_token, _verify_mfa_token
    import base64

    token = _sign_mfa_token(42)
    # Decode, change user_id to 99, re-encode without valid signature
    raw = base64.urlsafe_b64decode(token.encode()).decode()
    parts = raw.split(":")
    parts[0] = "99"
    tampered = base64.urlsafe_b64encode(":".join(parts).encode()).decode()
    assert _verify_mfa_token(tampered) is None
