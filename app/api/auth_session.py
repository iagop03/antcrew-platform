"""Email+password platform authentication — session cookies backed by UserSession rows."""
from __future__ import annotations

import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import select

from app.core.database import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "antcrew_session"
COOKIE_MAX_AGE = 2592000  # 30 days


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_secure() -> bool:
    """Return True when running in any environment other than dev (enables Secure cookie flag)."""
    return os.environ.get("APP_ENV", "dev").lower() != "dev"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
    )


def _clear_session_cookie(response: Response) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value="",
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
        max_age=0,
    )


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    except Exception:
        return False


def _make_token() -> str:
    return str(uuid.uuid4())


async def _create_session(user_id: Optional[int], api_key_id: Optional[int], session) -> str:
    """Insert a UserSession row and return the raw token."""
    from app.models.run import UserSession

    token = _make_token()
    now = _utcnow()
    user_session = UserSession(
        token=token,
        user_id=user_id,
        api_key_id=api_key_id,
        created_at=now,
        expires_at=now + timedelta(seconds=COOKIE_MAX_AGE),
        revoked=False,
    )
    session.add(user_session)
    await session.commit()
    return token


async def _resolve_session(token: str, session) -> Optional[tuple]:
    """Return (UserSession, ApiKey) or None if the token is invalid/expired/revoked."""
    from app.models.run import UserSession, ApiKey

    now = _utcnow()
    user_session = (await session.exec(
        select(UserSession).where(
            UserSession.token == token,
            UserSession.revoked == False,  # noqa: E712
            UserSession.expires_at > now,
        )
    )).first()

    if user_session is None:
        return None

    if user_session.api_key_id is None:
        return None

    key = (await session.exec(
        select(ApiKey).where(
            ApiKey.id == user_session.api_key_id,
            ApiKey.revoked_at == None,  # noqa: E711
        )
    )).first()

    if key is None:
        return None

    return user_session, key


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class _RegisterRequest(BaseModel):
    email: str
    password: str


class _LoginRequest(BaseModel):
    email: str
    password: str


class _TokenRequest(BaseModel):
    api_key: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
async def register(
    body: _RegisterRequest,
    response: Response,
    session=Depends(get_session),
):
    """Create a User + admin ApiKey + UserSession; set session cookie.

    Returns the raw API key in the 201 body (only time it is visible — save it).
    Idempotent-on-error: any DB writes are rolled back if a step fails.
    """
    from app.models.run import User, ApiKey, Workspace

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required")
    if not body.password or len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    # Check email uniqueness
    existing = (await session.exec(
        select(User).where(User.email == email)
    )).first()
    if existing:
        raise HTTPException(409, "An account with this email already exists")

    try:
        # Create user
        user = User(
            email=email,
            password_hash=_hash_password(body.password),
            created_at=_utcnow(),
        )
        session.add(user)
        await session.flush()  # get user.id without full commit

        # Get or create first workspace
        workspace = (await session.exec(select(Workspace).limit(1))).first()
        if workspace is None:
            from app.core.byok import TRIAL_CREDIT_USD
            slug_base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0])[:40] or "workspace"
            workspace = Workspace(
                name=email,
                slug=slug_base,
                is_trial=True,
                max_cost_usd=TRIAL_CREDIT_USD,
            )
            session.add(workspace)
            await session.flush()

        # Create admin API key for this user
        raw_key = secrets.token_urlsafe(32)
        from app.core.auth import _hash as _hash_key, _key_prefix

        # Derive a unique label from the email
        label_base = re.sub(r"[^a-z0-9-]", "-", email.split("@")[0])[:50] or "user"
        label = label_base
        suffix = 1
        while (await session.exec(select(ApiKey).where(ApiKey.label == label))).first():
            label = f"{label_base}-{suffix}"
            suffix += 1

        api_key = ApiKey(
            label=label,
            key_hash=_hash_key(raw_key),
            key_prefix=_key_prefix(raw_key),
            workspace_id=workspace.id,
            role="admin",
            email=email,
            user_id=user.id,
            created_at=_utcnow(),
        )
        session.add(api_key)
        await session.flush()

        # Create session
        token = _make_token()
        from app.models.run import UserSession

        user_session = UserSession(
            token=token,
            user_id=user.id,
            api_key_id=api_key.id,
            created_at=_utcnow(),
            expires_at=_utcnow() + timedelta(seconds=COOKIE_MAX_AGE),
            revoked=False,
        )
        session.add(user_session)
        await session.commit()

    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(500, f"Registration failed: {exc}") from exc

    _set_session_cookie(response, token)
    return {
        "email": email,
        "workspace_id": workspace.id,
        "role": "admin",
        "api_key": raw_key,
    }


@router.post("/login")
async def login(
    body: _LoginRequest,
    response: Response,
    session=Depends(get_session),
):
    """Validate email+password; issue a new session cookie."""
    from app.models.run import User, ApiKey

    email = body.email.strip().lower()

    user = (await session.exec(
        select(User).where(User.email == email)
    )).first()

    if user is None or not _verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")

    # Find the api key linked to this user
    api_key = (await session.exec(
        select(ApiKey).where(
            ApiKey.user_id == user.id,
            ApiKey.revoked_at == None,  # noqa: E711
        )
    )).first()

    if api_key is None:
        raise HTTPException(401, "No active API key for this account")

    token = await _create_session(user.id, api_key.id, session)
    _set_session_cookie(response, token)

    return {
        "email": email,
        "workspace_id": api_key.workspace_id,
        "role": api_key.role,
    }


@router.post("/token")
async def exchange_api_key_for_session(
    body: _TokenRequest,
    response: Response,
    session=Depends(get_session),
):
    """Exchange an existing API key for a session cookie.

    For users who already have an API key and want browser-cookie-based access.
    Creates a UserSession with user_id=None and api_key_id=matched_key.id.
    """
    import hashlib

    from app.models.run import ApiKey

    raw_key = body.api_key.strip()
    if not raw_key:
        raise HTTPException(400, "api_key is required")

    # Fast-path lookup by prefix
    prefix = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
    candidates = (await session.exec(
        select(ApiKey).where(
            ApiKey.key_prefix == prefix,
            ApiKey.revoked_at == None,  # noqa: E711
        )
    )).all()

    matched: Optional[ApiKey] = None
    for k in candidates:
        if _verify_password_like_bcrypt_or_sha256(raw_key, k.key_hash):
            matched = k
            break

    # Slow path: legacy keys without prefix
    if matched is None:
        legacy = (await session.exec(
            select(ApiKey).where(
                ApiKey.key_prefix == None,  # noqa: E711
                ApiKey.revoked_at == None,  # noqa: E711
            )
        )).all()
        for k in legacy:
            if _verify_password_like_bcrypt_or_sha256(raw_key, k.key_hash):
                matched = k
                break

    if matched is None:
        raise HTTPException(401, "Invalid API key")

    token = await _create_session(matched.user_id, matched.id, session)
    _set_session_cookie(response, token)

    return {
        "workspace_id": matched.workspace_id,
        "role": matched.role,
    }


def _verify_password_like_bcrypt_or_sha256(raw_key: str, stored_hash: str) -> bool:
    """Verify bcrypt or legacy sha256 API key hash."""
    import hashlib
    import hmac

    if stored_hash.startswith(("$2b$", "$2a$", "$2y$")):
        return _verify_password(raw_key, stored_hash)
    return hmac.compare_digest(stored_hash, hashlib.sha256(raw_key.encode()).hexdigest())


@router.delete("/token", status_code=204)
async def revoke_session(
    request: Request,
    response: Response,
    session=Depends(get_session),
):
    """Revoke the current session and clear the cookie."""
    from app.models.run import UserSession

    token = request.cookies.get(COOKIE_NAME)
    if token:
        now = _utcnow()
        user_session = (await session.exec(
            select(UserSession).where(
                UserSession.token == token,
                UserSession.revoked == False,  # noqa: E712
                UserSession.expires_at > now,
            )
        )).first()
        if user_session:
            user_session.revoked = True
            session.add(user_session)
            await session.commit()

    _clear_session_cookie(response)
    return None


@router.get("/me")
async def me(
    request: Request,
    session=Depends(get_session),
):
    """Return current session info from cookie, or 401 if no valid session."""
    from app.models.run import User

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")

    result = await _resolve_session(token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")

    user_session, api_key = result

    # Resolve email: from User row (register/login path) or ApiKey.email (token exchange path)
    email: Optional[str] = None
    if user_session.user_id is not None:
        user = (await session.exec(
            select(User).where(User.id == user_session.user_id)
        )).first()
        email = user.email if user else api_key.email
    else:
        email = api_key.email

    return {
        "email": email,
        "workspace_id": api_key.workspace_id,
        "role": api_key.role,
        "session_expires_at": user_session.expires_at.isoformat() + "Z",
    }
