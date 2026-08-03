"""Email+password platform authentication — session cookies backed by UserSession rows."""
from __future__ import annotations

import logging
import os
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import select

from app.core.csrf import require_csrf
from app.core.database import get_session

log = logging.getLogger(__name__)

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
    return secrets.token_urlsafe(32)


def _make_verification_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _hash_session_token(raw: str) -> str:
    """SHA-256 of a high-entropy session token. Stored instead of plaintext."""
    import hashlib
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_verification_code(code: str) -> str:
    """HMAC-SHA256 of a low-entropy code using SECRET_KEY.
    Prevents brute-force enumeration even if the DB is read by an attacker.
    """
    import hashlib as _hl
    import hmac as _hmac
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        return _hl.sha256(code.encode()).hexdigest()
    return _hmac.new(secret.encode(), code.encode(), _hl.sha256).hexdigest()


async def _create_session(user_id: Optional[int], api_key_id: Optional[int], session) -> str:
    """Insert a UserSession row and return the raw token.

    Stores token_hash (SHA-256) instead of plaintext — the raw token lives only
    in the HttpOnly cookie and never touches the DB.
    """
    from app.models.run import UserSession

    token = _make_token()
    now = _utcnow()
    user_session = UserSession(
        token=None,                           # plaintext never stored
        token_hash=_hash_session_token(token),
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
    token_hash = _hash_session_token(token)
    user_session = (await session.exec(
        select(UserSession).where(
            UserSession.token_hash == token_hash,
            UserSession.revoked == False,  # noqa: E712
            UserSession.expires_at > now,
        )
    )).first()
    if user_session is None:  # fallback for pre-033 sessions
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
    request: Request,
    response: Response,
    session=Depends(get_session),
):
    """Create a User + admin ApiKey + UserSession; set session cookie.

    Returns the raw API key in the 201 body (only time it is visible — save it).
    Idempotent-on-error: any DB writes are rolled back if a step fails.
    """
    from app.core import rate_limit
    from app.core.csrf import generate as _csrf_gen, set_cookie as _csrf_set
    from app.models.run import User, ApiKey, Workspace

    await rate_limit.check(request, None, None)

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

        # Always create a NEW workspace for this user — never attach to an existing one
        from app.core.byok import TRIAL_CREDIT_USD
        from app.core.promo import get_active_free_promo
        _promo = await get_active_free_promo(session)
        slug_base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0])[:40] or "workspace"
        slug = slug_base
        for _attempt in range(20):
            slug_exists = (await session.exec(
                select(Workspace).where(Workspace.slug == slug)
            )).first()
            if not slug_exists:
                break
            slug = f"{slug_base}-{secrets.token_hex(3)}"
        else:
            slug = f"workspace-{secrets.token_hex(6)}"
        from app.models.admin import PlatformConfig as _PlatformConfig
        _pcfg = await session.get(_PlatformConfig, 1)
        workspace = Workspace(
            name=email,
            slug=slug,
            is_trial=_promo is not None,
            max_cost_usd=TRIAL_CREDIT_USD if _promo is not None else None,
            owner_user_id=user.id,
            base_managed_mult=_pcfg.managed_cost_multiplier if _pcfg else 3.0,
            base_byok_mult=_pcfg.byok_service_multiplier if _pcfg else 0.4,
            base_proxy_mult=_pcfg.proxy_service_multiplier if _pcfg else 0.7,
        )
        session.add(workspace)
        await session.flush()

        # Create admin API key for this user
        raw_key = secrets.token_urlsafe(32)
        from app.core.auth import _hash as _hash_key, _key_prefix

        # Derive a unique label from the email — append random suffix on collision
        label_base = re.sub(r"[^a-z0-9-]", "-", email.split("@")[0])[:48] or "user"
        label = label_base
        for _attempt in range(20):
            exists = (await session.exec(
                select(ApiKey).where(ApiKey.label == label, ApiKey.workspace_id == workspace.id)
            )).first()
            if not exists:
                break
            label = f"{label_base}-{secrets.token_hex(3)}"
        else:
            label = f"user-{secrets.token_hex(6)}"

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
        try:
            await session.flush()
        except Exception:
            # Race condition: another request inserted the same label between check and flush
            await session.rollback()
            api_key.label = f"user-{secrets.token_hex(6)}"
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

    # Send email verification code (fire-and-forget; don't fail registration on SMTP error)
    import asyncio as _asyncio
    from app.models.run import EmailVerification
    from app.services.email import send_verification_code as _send_code

    try:
        code = _make_verification_code()
        verification = EmailVerification(
            user_id=user.id,
            code=None,                                 # plaintext never stored
            code_hash=_hash_verification_code(code),
            expires_at=_utcnow() + timedelta(minutes=15),
        )
        # Use a fresh mini-session so errors don't touch the committed data
        from sqlmodel.ext.asyncio.session import AsyncSession as _AsyncSession
        from app.core.database import engine as _engine
        async with _AsyncSession(_engine, expire_on_commit=False) as vs:
            vs.add(verification)
            await vs.commit()
        _asyncio.create_task(_send_code(email, code))
    except Exception as _exc:
        log.warning("register: could not create verification code for %s: %s", email, _exc)

    csrf_token = _csrf_gen()
    _set_session_cookie(response, token)
    _csrf_set(response, csrf_token, secure=_is_secure())
    return {
        "email": email,
        "workspace_id": workspace.id,
        "role": "admin",
        "api_key": raw_key,
        "email_verified": False,
    }


@router.post("/login")
async def login(
    body: _LoginRequest,
    request: Request,
    response: Response,
    session=Depends(get_session),
):
    """Validate email+password; issue a new session cookie."""
    from app.core import rate_limit
    from app.core.csrf import generate as _csrf_gen, set_cookie as _csrf_set
    from app.models.run import User, ApiKey

    await rate_limit.check(request, None, None)

    email = body.email.strip().lower()

    user = (await session.exec(
        select(User).where(User.email == email)
    )).first()

    if user is None or not _verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")

    # MFA gate: if user has TOTP enabled, issue a short-lived challenge token instead of a session.
    if user.mfa_enabled and user.totp_secret:
        mfa_token = _sign_mfa_token(user.id)
        return {"mfa_required": True, "mfa_token": mfa_token}

    # Find the api key linked to this user
    api_key = (await session.exec(
        select(ApiKey).where(
            ApiKey.user_id == user.id,
            ApiKey.revoked_at == None,  # noqa: E711
        )
    )).first()

    if api_key is None:
        # User has no active API key (e.g. they revoked it from settings).
        # Recover: find or create workspace, then create a new admin API key.
        from app.models.run import Workspace
        from app.core.auth import _hash as _hash_key, _key_prefix
        from app.core.byok import TRIAL_CREDIT_USD

        workspace = (await session.exec(
            select(Workspace).where(Workspace.owner_user_id == user.id)
        )).first()

        if workspace is None:
            # No workspace at all — create one (e.g. registration was incomplete)
            from app.core.promo import get_active_free_promo
            _promo2 = await get_active_free_promo(session)
            slug_base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0])[:40] or "workspace"
            slug = slug_base
            for _attempt in range(20):
                slug_exists = (await session.exec(
                    select(Workspace).where(Workspace.slug == slug)
                )).first()
                if not slug_exists:
                    break
                slug = f"{slug_base}-{secrets.token_hex(3)}"
            else:
                slug = f"workspace-{secrets.token_hex(6)}"
            from app.models.admin import PlatformConfig as _PlatformConfig
            _pcfg2 = await session.get(_PlatformConfig, 1)
            workspace = Workspace(
                name=email,
                slug=slug,
                is_trial=_promo2 is not None,
                max_cost_usd=TRIAL_CREDIT_USD if _promo2 is not None else None,
                owner_user_id=user.id,
                base_managed_mult=_pcfg2.managed_cost_multiplier if _pcfg2 else 3.0,
                base_byok_mult=_pcfg2.byok_service_multiplier if _pcfg2 else 0.4,
                base_proxy_mult=_pcfg2.proxy_service_multiplier if _pcfg2 else 0.7,
            )
            session.add(workspace)
            await session.flush()

        raw_key = secrets.token_urlsafe(32)
        label_base = re.sub(r"[^a-z0-9-]", "-", email.split("@")[0])[:48] or "user"
        label = label_base
        for _attempt in range(20):
            exists = (await session.exec(
                select(ApiKey).where(ApiKey.label == label, ApiKey.workspace_id == workspace.id)
            )).first()
            if not exists:
                break
            label = f"{label_base}-{secrets.token_hex(3)}"
        else:
            label = f"user-{secrets.token_hex(6)}"
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

    token = await _create_session(user.id, api_key.id, session)
    csrf_token = _csrf_gen()
    _set_session_cookie(response, token)
    _csrf_set(response, csrf_token, secure=_is_secure())

    return {
        "email": email,
        "workspace_id": api_key.workspace_id,
        "role": api_key.role,
        "email_verified": user.email_verified_at is not None,
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

    from app.core.csrf import generate as _csrf_gen, set_cookie as _csrf_set
    token = await _create_session(matched.user_id, matched.id, session)
    csrf_token = _csrf_gen()
    _set_session_cookie(response, token)
    _csrf_set(response, csrf_token, secure=_is_secure())

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
        token_hash = _hash_session_token(token)
        user_session = (await session.exec(
            select(UserSession).where(
                UserSession.token_hash == token_hash,
                UserSession.revoked == False,  # noqa: E712
                UserSession.expires_at > now,
            )
        )).first()
        if user_session is None:  # fallback for pre-033 sessions
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

    from app.core.csrf import clear_cookie as _csrf_clear
    _clear_session_cookie(response)
    _csrf_clear(response, secure=_is_secure())
    return None


@router.post("/verify-email", status_code=200)
async def verify_email(
    request: Request,
    session=Depends(get_session),
):
    """Verify the 6-digit code sent after registration. Body: {code: string}."""
    from app.models.run import User, EmailVerification

    body = await request.json()
    code = str(body.get("code", "")).strip()
    if not code:
        raise HTTPException(400, "code is required")

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    result = await _resolve_session(token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, api_key = result
    if user_session.user_id is None:
        raise HTTPException(400, "Session not linked to a user account")

    now = _utcnow()
    verification = (await session.exec(
        select(EmailVerification).where(
            EmailVerification.user_id == user_session.user_id,
            EmailVerification.used == False,  # noqa: E712
            EmailVerification.expires_at > now,
        )
    )).first()

    # Compare by hash (new) or plaintext fallback (pre-033 rows)
    code_matches = (
        (verification.code_hash is not None and verification.code_hash == _hash_verification_code(code))
        or (verification.code is not None and verification.code == code)
    )
    if verification is None or not code_matches:
        raise HTTPException(400, "Invalid or expired verification code")

    verification.used = True
    session.add(verification)

    user = (await session.exec(select(User).where(User.id == user_session.user_id))).first()
    if user:
        user.email_verified_at = now
        session.add(user)

    await session.commit()
    return {"verified": True}


@router.post("/resend-code", status_code=200)
async def resend_verification_code(
    request: Request,
    session=Depends(get_session),
):
    """Resend the email verification code. Invalidates any previous pending codes."""
    import asyncio as _asyncio
    from app.models.run import User, EmailVerification
    from app.services.email import send_verification_code as _send_code

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    result = await _resolve_session(token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, api_key = result
    if user_session.user_id is None:
        raise HTTPException(400, "Session not linked to a user account")

    user = (await session.exec(select(User).where(User.id == user_session.user_id))).first()
    if user is None:
        raise HTTPException(404, "User not found")
    if user.email_verified_at is not None:
        return {"message": "Email already verified"}

    # Invalidate previous pending codes
    old_codes = (await session.exec(
        select(EmailVerification).where(
            EmailVerification.user_id == user.id,
            EmailVerification.used == False,  # noqa: E712
        )
    )).all()
    for old in old_codes:
        old.used = True
        session.add(old)

    code = _make_verification_code()
    verification = EmailVerification(
        user_id=user.id,
        code=None,
        code_hash=_hash_verification_code(code),
        expires_at=_utcnow() + timedelta(minutes=15),
    )
    session.add(verification)
    await session.commit()

    _asyncio.create_task(_send_code(user.email, code))
    return {"message": "Verification code sent"}


# ── MFA / TOTP ───────────────────────────────────────────────────────────────

def _totp_issuer() -> str:
    return os.environ.get("APP_NAME", "AntCrew")


def _sign_mfa_token(user_id: int) -> str:
    """Return HMAC-SHA256 token encoding user_id + expiry (5 min). No DB write needed."""
    import hashlib
    import hmac
    import time

    exp = int(time.time()) + 300  # 5 min
    payload = f"{user_id}:{exp}"
    secret = os.environ.get("SECRET_KEY", "")
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    import base64
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_mfa_token(token: str) -> Optional[int]:
    """Verify HMAC-signed MFA challenge token. Returns user_id or None if invalid/expired."""
    import base64
    import hashlib
    import hmac
    import time

    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        user_id_str, exp_str, sig = raw.rsplit(":", 2)
        payload = f"{user_id_str}:{exp_str}"
        secret = os.environ.get("SECRET_KEY", "")
        expected_sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        if int(time.time()) > int(exp_str):
            return None
        return int(user_id_str)
    except Exception:
        return None


@router.get("/mfa/setup")
async def mfa_setup(
    request: Request,
    session=Depends(get_session),
):
    """Generate a TOTP secret and provisioning URI for MFA setup.

    Returns a provisioning URI compatible with Google Authenticator, Authy, etc.
    The secret is NOT yet stored — call POST /auth/mfa/enable with the first valid
    code to activate MFA.
    """
    import pyotp

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    result = await _resolve_session(token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, api_key = result
    if user_session.user_id is None:
        raise HTTPException(400, "MFA requires a user account (session-cookie login)")

    from app.models.run import User
    user = (await session.exec(select(User).where(User.id == user_session.user_id))).first()
    if user is None:
        raise HTTPException(404, "User not found")

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=user.email, issuer_name=_totp_issuer())

    import base64, io
    try:
        import qrcode
        import qrcode.image.svg as _qr_svg
        qr_img = qrcode.make(uri, image_factory=_qr_svg.SvgPathImage, box_size=6, border=2)
        buf = io.BytesIO()
        qr_img.save(buf)
        qr_svg_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        qr_svg_b64 = None

    return {"secret": secret, "provisioning_uri": uri, "mfa_enabled": user.mfa_enabled, "qr_svg_b64": qr_svg_b64}


class _MfaEnableRequest(BaseModel):
    secret: str   # base32 secret from GET /auth/mfa/setup
    code: str     # 6-digit TOTP code to verify before enabling


@router.post("/mfa/enable", status_code=200)
async def mfa_enable(
    body: _MfaEnableRequest,
    request: Request,
    session=Depends(get_session),
    _csrf: None = Depends(require_csrf),
):
    """Activate MFA for the authenticated user after verifying the first TOTP code.

    Stores the secret and sets mfa_enabled=True. All subsequent logins will require
    a valid TOTP code.
    """
    import pyotp

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    result = await _resolve_session(token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, _ = result
    if user_session.user_id is None:
        raise HTTPException(400, "MFA requires a user account")

    if not body.secret or not body.code:
        raise HTTPException(400, "secret and code are required")

    totp = pyotp.TOTP(body.secret)
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(400, "Invalid TOTP code — check your authenticator app and try again")

    from app.models.run import User
    user = (await session.exec(select(User).where(User.id == user_session.user_id))).first()
    if user is None:
        raise HTTPException(404, "User not found")

    user.totp_secret = body.secret
    user.mfa_enabled = True
    session.add(user)
    await session.commit()
    return {"mfa_enabled": True}


class _MfaDisableRequest(BaseModel):
    code: str     # current TOTP code to confirm identity before disabling


@router.post("/mfa/disable", status_code=200)
async def mfa_disable(
    body: _MfaDisableRequest,
    request: Request,
    session=Depends(get_session),
    _csrf: None = Depends(require_csrf),
):
    """Disable MFA after verifying the current TOTP code."""
    import pyotp

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    result = await _resolve_session(token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, _ = result
    if user_session.user_id is None:
        raise HTTPException(400, "MFA requires a user account")

    from app.models.run import User
    user = (await session.exec(select(User).where(User.id == user_session.user_id))).first()
    if user is None:
        raise HTTPException(404, "User not found")
    if not user.mfa_enabled or not user.totp_secret:
        return {"mfa_enabled": False, "message": "MFA was not enabled"}

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(400, "Invalid TOTP code")

    user.totp_secret = None
    user.mfa_enabled = False
    session.add(user)
    await session.commit()
    return {"mfa_enabled": False}


class _MfaChallengeRequest(BaseModel):
    mfa_token: str   # short-lived token from POST /auth/login when mfa_required=true
    code: str        # 6-digit TOTP code


@router.post("/mfa/challenge")
async def mfa_challenge(
    body: _MfaChallengeRequest,
    response: Response,
    session=Depends(get_session),
):
    """Complete the MFA challenge after a login that returned mfa_required=true.

    Validates the TOTP code and issues a full session cookie on success.
    """
    import pyotp
    from app.core.csrf import generate as _csrf_gen, set_cookie as _csrf_set
    from app.models.run import User, ApiKey

    user_id = _verify_mfa_token(body.mfa_token)
    if user_id is None:
        raise HTTPException(401, "MFA token is invalid or expired — please log in again")

    user = (await session.exec(select(User).where(User.id == user_id))).first()
    if user is None or not user.mfa_enabled or not user.totp_secret:
        raise HTTPException(400, "MFA is not configured for this account")

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.code.strip(), valid_window=1):
        raise HTTPException(400, "Invalid TOTP code")

    api_key = (await session.exec(
        select(ApiKey).where(
            ApiKey.user_id == user.id,
            ApiKey.revoked_at == None,  # noqa: E711
        )
    )).first()
    if api_key is None:
        raise HTTPException(401, "No active API key found for this account")

    token = await _create_session(user.id, api_key.id, session)
    csrf_token = _csrf_gen()
    _set_session_cookie(response, token)
    _csrf_set(response, csrf_token, secure=_is_secure())
    return {
        "email": user.email,
        "workspace_id": api_key.workspace_id,
        "role": api_key.role,
        "email_verified": user.email_verified_at is not None,
    }


@router.post("/accept-invite", status_code=200)
async def accept_invite(
    request: Request,
    session=Depends(get_session),
):
    """Accept a workspace invite token. Body: {token: string}. Requires active session."""
    from app.models.run import WorkspaceInvite, WorkspaceMembership, Workspace

    body = await request.json()
    invite_token = str(body.get("token", "")).strip()
    if not invite_token:
        raise HTTPException(400, "token is required")

    session_token = request.cookies.get(COOKIE_NAME)
    if not session_token:
        raise HTTPException(401, "You must be logged in to accept an invite")
    result = await _resolve_session(session_token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, api_key = result

    import hashlib
    now = _utcnow()
    token_hash = hashlib.sha256(invite_token.encode()).hexdigest()
    invite = (await session.exec(
        select(WorkspaceInvite).where(
            WorkspaceInvite.token_hash == token_hash,
            WorkspaceInvite.status == "pending",
            WorkspaceInvite.expires_at > now,
        )
    )).first()
    if invite is None:  # fallback for legacy plaintext tokens
        invite = (await session.exec(
            select(WorkspaceInvite).where(
                WorkspaceInvite.token == invite_token,
                WorkspaceInvite.status == "pending",
                WorkspaceInvite.expires_at > now,
            )
        )).first()

    if invite is None:
        raise HTTPException(404, "Invite not found or expired")

    # Add workspace membership (idempotent)
    existing = (await session.exec(
        select(WorkspaceMembership).where(
            WorkspaceMembership.api_key_id == api_key.id,
            WorkspaceMembership.workspace_id == invite.workspace_id,
        )
    )).first()
    if not existing:
        session.add(WorkspaceMembership(
            api_key_id=api_key.id,
            workspace_id=invite.workspace_id,
        ))

    # Upgrade role if the invite grants higher privileges
    _role_order = {"read": 0, "reviewer": 1, "write": 2, "admin": 3}
    if _role_order.get(invite.role, 0) > _role_order.get(api_key.role, 0):
        api_key.role = invite.role
        session.add(api_key)

    invite.status = "accepted"
    invite.accepted_at = now
    session.add(invite)
    await session.commit()

    ws = (await session.exec(select(Workspace).where(Workspace.id == invite.workspace_id))).first()
    return {
        "workspace_id": invite.workspace_id,
        "workspace_name": ws.name if ws else None,
        "role": invite.role,
    }


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

    # Resolve email and verification status from User row
    email: Optional[str] = None
    email_verified: bool = True  # API-key-only sessions are treated as verified
    if user_session.user_id is not None:
        user = (await session.exec(
            select(User).where(User.id == user_session.user_id)
        )).first()
        if user:
            email = user.email
            email_verified = user.email_verified_at is not None
        else:
            email = api_key.email
    else:
        email = api_key.email

    display_name: Optional[str] = None
    mfa_enabled: bool = False
    if user_session.user_id is not None and user:
        display_name = user.display_name
        mfa_enabled = bool(user.mfa_enabled)

    return {
        "email": email,
        "display_name": display_name,
        "workspace_id": api_key.workspace_id,
        "role": api_key.role,
        "email_verified": email_verified,
        "mfa_enabled": mfa_enabled,
        "session_expires_at": user_session.expires_at.isoformat() + "Z",
    }


@router.patch("/profile", status_code=200)
async def update_profile(
    request: Request,
    session=Depends(get_session),
    _csrf: None = Depends(require_csrf),
):
    """Update display_name and/or password for the authenticated user."""
    from app.models.run import User

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    result = await _resolve_session(token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, _ = result
    if user_session.user_id is None:
        raise HTTPException(400, "Session not linked to a user account")

    body = await request.json()
    user = (await session.exec(select(User).where(User.id == user_session.user_id))).first()
    if user is None:
        raise HTTPException(404, "User not found")

    changed = False
    if "display_name" in body:
        name = (body["display_name"] or "").strip()
        user.display_name = name or None
        changed = True

    if "new_password" in body:
        current_pw = body.get("current_password", "")
        new_pw = body.get("new_password", "")
        if not _verify_password(current_pw, user.password_hash):
            raise HTTPException(400, "Current password is incorrect")
        if len(new_pw) < 8:
            raise HTTPException(400, "New password must be at least 8 characters")
        user.password_hash = _hash_password(new_pw)
        changed = True
        # Revoke all other active sessions so a stolen cookie can't persist
        # after the owner changes their password.
        from app.models.run import UserSession
        other_sessions = (await session.exec(
            select(UserSession).where(
                UserSession.user_id == user.id,
                UserSession.token != token,
                UserSession.revoked == False,  # noqa: E712
            )
        )).all()
        for s in other_sessions:
            s.revoked = True
            session.add(s)

    if changed:
        session.add(user)
        await session.commit()

    return {"display_name": user.display_name, "email": user.email}


_VALID_USE_CASES = frozenset({
    "customer_support", "internal_automation", "software_dev", "data_analysis", "other",
})
_VALID_TEAM_SIZES = frozenset({"solo", "2-5", "6-15", "15+"})


@router.patch("/profile")
async def update_profile(
    request: Request,
    session=Depends(get_session),
):
    """Save onboarding profile data (use_case, team_size) for the current user.

    Accepts session cookie (set at registration) so it can be called from the
    onboarding wizard without requiring a separate login step.
    """
    from app.models.run import User

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    result = await _resolve_session(token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, _api_key = result
    if user_session.user_id is None:
        raise HTTPException(400, "Session not linked to a user account")

    body = await request.json()
    user = (await session.exec(select(User).where(User.id == user_session.user_id))).first()
    if user is None:
        raise HTTPException(404, "User not found")

    use_case = body.get("use_case")
    team_size = body.get("team_size")

    if use_case is not None:
        if use_case not in _VALID_USE_CASES:
            raise HTTPException(422, f"use_case must be one of {sorted(_VALID_USE_CASES)}")
        user.use_case = use_case
    if team_size is not None:
        if team_size not in _VALID_TEAM_SIZES:
            raise HTTPException(422, f"team_size must be one of {sorted(_VALID_TEAM_SIZES)}")
        user.team_size = team_size

    session.add(user)
    await session.commit()
    return {"ok": True}
