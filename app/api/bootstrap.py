"""Bootstrap and trial self-registration endpoints."""
from __future__ import annotations

import os
import re
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.core.auth import _hash, _key_prefix
from app.core.byok import TRIAL_CREDIT_USD
from app.core.database import get_session
from app.models.run import ApiKey, Workspace

router = APIRouter()

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


class _BootstrapRequest(BaseModel):
    ws_name: str
    ws_slug: str
    admin_label: str


@router.post("/onboard/bootstrap", status_code=201, tags=["onboard"])
async def onboard_bootstrap(
    body: _BootstrapRequest,
    session=Depends(get_session),
):
    """Create the first workspace + admin key when the system is empty.

    No authentication required — but only succeeds when there are zero
    existing workspaces. Once the system has data, use admin credentials
    via the standard /workspaces/ and /api-keys/ endpoints.
    """
    from sqlalchemy import func

    ws_count = (await session.exec(
        select(func.count()).select_from(Workspace)
    )).one()
    if ws_count > 0:
        raise HTTPException(
            403,
            "System already has workspaces. "
            "Use admin credentials via /workspaces/ and /api-keys/.",
        )

    from sqlalchemy.exc import IntegrityError as _IntegrityError
    ws = Workspace(
        name=body.ws_name.strip(),
        slug=body.ws_slug.strip(),
        is_trial=True,
        max_cost_usd=TRIAL_CREDIT_USD,
    )
    session.add(ws)
    await session.flush()  # get ws.id without committing; rolled back if api_key insert fails

    raw = secrets.token_urlsafe(32)
    key = ApiKey(
        label=body.admin_label.strip(),
        key_hash=_hash(raw),
        key_prefix=_key_prefix(raw),
        workspace_id=ws.id,
        role="admin",
    )
    session.add(key)
    try:
        await session.commit()
    except _IntegrityError:
        await session.rollback()
        raise HTTPException(
            409,
            f"API key label {body.admin_label.strip()!r} is already taken — choose a different label",
        )
    await session.refresh(ws)

    return {
        "workspace_id": ws.id,
        "workspace_name": ws.name,
        "workspace_slug": ws.slug,
        "admin_label": key.label,
        "key": raw,
    }


# ---------------------------------------------------------------------------
# Public trial registration
# ---------------------------------------------------------------------------

# Simple in-memory tracker: ip -> list[timestamp] for 24-hour window
_trial_ip_log: dict[str, list[float]] = {}
_TRIAL_MAX_PER_IP: int = int(os.environ.get("TRIAL_MAX_PER_IP", "5"))
_TRIAL_WINDOW_S: float = 86400.0  # 24 hours


class _TrialRequest(BaseModel):
    name: str   # workspace / company name
    email: str  # used as API key label and contact


@router.post("/trial/register", status_code=201, tags=["trial"])
async def trial_register(
    body: _TrialRequest,
    request: Request,
    session=Depends(get_session),
):
    """Public self-service trial registration.

    Creates a workspace + admin API key with TRIAL_CREDIT_USD of free credit.
    Rate-limited to TRIAL_MAX_PER_IP (default 5) registrations per IP per 24 h.
    """
    # ── IP rate limit ────────────────────────────────────────────────────────
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    timestamps = _trial_ip_log.setdefault(ip, [])
    timestamps[:] = [t for t in timestamps if t > now - _TRIAL_WINDOW_S]
    if _TRIAL_MAX_PER_IP > 0 and len(timestamps) >= _TRIAL_MAX_PER_IP:
        raise HTTPException(
            429,
            "Too many trial registrations from this IP. Try again in 24 hours.",
            headers={"Retry-After": "86400"},
        )

    # ── Validate inputs ──────────────────────────────────────────────────────
    name = body.name.strip()
    email = body.email.strip().lower()
    if not name or not email or "@" not in email:
        raise HTTPException(400, "name and a valid email are required")

    # ── Generate unique slug ─────────────────────────────────────────────────
    base_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "trial"
    slug = base_slug
    suffix = 1
    while (await session.exec(select(Workspace).where(Workspace.slug == slug))).first():
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    # ── Create workspace ─────────────────────────────────────────────────────
    ws = Workspace(
        name=name,
        slug=slug,
        is_trial=True,
        max_cost_usd=TRIAL_CREDIT_USD,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    # ── Create admin API key ─────────────────────────────────────────────────
    raw = secrets.token_urlsafe(32)
    base_label = re.sub(r"[^a-z0-9-]", "-", email.split("@")[0])[:48] or "user"
    label = base_label
    for _attempt in range(20):
        if not (await session.exec(select(ApiKey).where(ApiKey.label == label))).first():
            break
        label = f"{base_label}-{secrets.token_hex(3)}"
    else:
        label = f"user-{secrets.token_hex(6)}"

    key = ApiKey(
        label=label,
        key_hash=_hash(raw),
        key_prefix=_key_prefix(raw),
        workspace_id=ws.id,
        role="admin",
        email=email,
    )
    session.add(key)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        key.label = f"user-{secrets.token_hex(6)}"
        session.add(key)
        await session.commit()

    timestamps.append(now)

    return {
        "workspace_id": ws.id,
        "workspace_name": ws.name,
        "workspace_slug": ws.slug,
        "trial_credit_usd": TRIAL_CREDIT_USD,
        "admin_label": label,
        "key": raw,
    }
