"""Workspace invite and join-request endpoints.

Routes (no prefix — full paths used):
  POST /workspaces/{workspace_id}/invites       — admin sends an email invite
  POST /workspaces/join-request                 — authenticated user requests access
  POST /join-requests/{token}/approve           — admin approves a join request
  POST /join-requests/{token}/reject            — admin rejects a join request
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.core.auth import (
    WorkspaceContext,
    require_role,
    require_verified_session,
    ws_accessible,
)
from app.core.database import get_session
from app.models.run import (
    ApiKey,
    Workspace,
    WorkspaceInvite,
    WorkspaceJoinRequest,
    WorkspaceMembership,
)

router = APIRouter(tags=["invites"])

_VALID_ROLES = frozenset({"admin", "write", "read", "reviewer"})


def _hash_join_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _base_url() -> str:
    return os.environ.get("BASE_URL", "https://app.antcrew.ai").rstrip("/")


# ── Workspace Invites ─────────────────────────────────────────────────────────

class InviteCreate(BaseModel):
    email: str
    role: str = "write"


@router.post("/workspaces/{workspace_id}/invites", status_code=201,
             dependencies=[Depends(require_role("admin")), Depends(require_verified_session())])
async def create_invite(
    workspace_id: int,
    body: InviteCreate,
    session=Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
):
    """Create a workspace invite link and send it by email to the recipient."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")

    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required")
    if body.role not in _VALID_ROLES:
        raise HTTPException(400, f"role must be one of: {', '.join(sorted(_VALID_ROLES))}")

    # Check for an existing pending invite to the same email in this workspace
    existing = (await session.exec(
        select(WorkspaceInvite).where(
            WorkspaceInvite.workspace_id == workspace_id,
            WorkspaceInvite.invitee_email == email,
            WorkspaceInvite.status == "pending",
        )
    )).first()
    if existing:
        raise HTTPException(409, "A pending invite already exists for this email in this workspace")

    import hashlib
    token = secrets.token_urlsafe(32)
    invite = WorkspaceInvite(
        token=None,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        workspace_id=workspace_id,
        invitee_email=email,
        inviter_email=ctx.created_by or "admin",
        role=body.role,
        expires_at=_utcnow() + timedelta(days=7),
    )
    session.add(invite)
    await session.commit()

    invite_url = f"{_base_url()}/accept-invite?token={token}"
    from app.services.email import send_workspace_invite
    asyncio.create_task(send_workspace_invite(
        email, ws.name, invite.inviter_email, body.role, invite_url
    ))

    return {
        "token": token,
        "invite_url": invite_url,
        "expires_at": invite.expires_at.isoformat() + "Z",
        "workspace_id": workspace_id,
        "invitee_email": email,
        "role": body.role,
    }


# ── Join Requests ─────────────────────────────────────────────────────────────

class JoinRequestCreate(BaseModel):
    workspace_slug: str
    requested_role: str = "write"


@router.post("/workspaces/join-request", status_code=201,
             dependencies=[Depends(require_verified_session())])
async def create_join_request(
    body: JoinRequestCreate,
    request: Request,
    session=Depends(get_session),
):
    """Request access to a workspace by slug. Must be logged in. Notifies the workspace admin."""
    from app.api.auth_session import COOKIE_NAME, _resolve_session
    from app.models.run import User

    session_token = request.cookies.get(COOKIE_NAME)
    if not session_token:
        raise HTTPException(401, "You must be logged in to request workspace access")
    result = await _resolve_session(session_token, session)
    if result is None:
        raise HTTPException(401, "Session expired or invalid")
    user_session, api_key = result

    if body.requested_role not in _VALID_ROLES:
        raise HTTPException(400, f"requested_role must be one of: {', '.join(sorted(_VALID_ROLES))}")

    ws = (await session.exec(
        select(Workspace).where(Workspace.slug == body.workspace_slug)
    )).first()
    if not ws:
        raise HTTPException(404, f"Workspace {body.workspace_slug!r} not found")

    # Resolve requester email (prefer User row, fall back to ApiKey.email)
    requester_email: Optional[str] = api_key.email
    if not requester_email and user_session.user_id:
        user = (await session.exec(select(User).where(User.id == user_session.user_id))).first()
        requester_email = user.email if user else None
    if not requester_email:
        raise HTTPException(400, "No email associated with this account")

    # Prevent duplicate pending requests
    existing = (await session.exec(
        select(WorkspaceJoinRequest).where(
            WorkspaceJoinRequest.workspace_id == ws.id,
            WorkspaceJoinRequest.requester_email == requester_email,
            WorkspaceJoinRequest.status == "pending",
        )
    )).first()
    if existing:
        raise HTTPException(409, "A pending join request already exists for this workspace")

    token = secrets.token_urlsafe(32)
    join_req = WorkspaceJoinRequest(
        token=None,
        token_hash=_hash_join_token(token),
        workspace_id=ws.id,
        requester_email=requester_email,
        requested_role=body.requested_role,
    )
    session.add(join_req)
    await session.commit()

    # Notify workspace admin by email
    admin_key = (await session.exec(
        select(ApiKey).where(
            ApiKey.workspace_id == ws.id,
            ApiKey.role == "admin",
            ApiKey.revoked_at == None,  # noqa: E711
        )
    )).first()

    if admin_key and admin_key.email:
        base = _base_url()
        approve_url = f"{base}/join-requests/{token}/approve"
        reject_url = f"{base}/join-requests/{token}/reject"
        from app.services.email import send_join_request as _send_join
        asyncio.create_task(_send_join(
            admin_key.email,
            requester_email,
            ws.name,
            body.requested_role,
            approve_url,
            reject_url,
        ))

    return {
        "token": token,
        "workspace_id": ws.id,
        "workspace_slug": ws.slug,
        "status": "pending",
    }


@router.post("/join-requests/{token}/approve",
             dependencies=[Depends(require_role("admin"))])
async def approve_join_request(
    token: str,
    session=Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
):
    """Approve a join request. Creates a WorkspaceMembership for the requester's API key."""
    now = _utcnow()
    join_req = (await session.exec(
        select(WorkspaceJoinRequest).where(
            WorkspaceJoinRequest.token_hash == _hash_join_token(token),
            WorkspaceJoinRequest.status == "pending",
        )
    )).first()
    if join_req is None:
        # Fallback for legacy rows stored with plaintext token.
        join_req = (await session.exec(
            select(WorkspaceJoinRequest).where(
                WorkspaceJoinRequest.token == token,
                WorkspaceJoinRequest.status == "pending",
            )
        )).first()
    if not join_req:
        raise HTTPException(404, "Join request not found or already resolved")
    if not ws_accessible(join_req.workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")

    # Grant access: find requester's API key by email and add workspace membership
    requester_key = (await session.exec(
        select(ApiKey).where(
            ApiKey.email == join_req.requester_email,
            ApiKey.revoked_at == None,  # noqa: E711
        )
    )).first()

    if requester_key:
        existing_membership = (await session.exec(
            select(WorkspaceMembership).where(
                WorkspaceMembership.api_key_id == requester_key.id,
                WorkspaceMembership.workspace_id == join_req.workspace_id,
            )
        )).first()
        if not existing_membership:
            session.add(WorkspaceMembership(
                api_key_id=requester_key.id,
                workspace_id=join_req.workspace_id,
            ))

    join_req.status = "approved"
    join_req.resolved_at = now
    session.add(join_req)

    ws = (await session.exec(select(Workspace).where(Workspace.id == join_req.workspace_id))).first()
    await session.commit()

    if ws:
        from app.services.email import send_join_approved
        asyncio.create_task(send_join_approved(
            join_req.requester_email, ws.name, _base_url()
        ))

    return {
        "status": "approved",
        "workspace_id": join_req.workspace_id,
        "requester_email": join_req.requester_email,
    }


@router.post("/join-requests/{token}/reject",
             dependencies=[Depends(require_role("admin"))])
async def reject_join_request(
    token: str,
    session=Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
):
    """Reject a join request and notify the requester."""
    now = _utcnow()
    join_req = (await session.exec(
        select(WorkspaceJoinRequest).where(
            WorkspaceJoinRequest.token_hash == _hash_join_token(token),
            WorkspaceJoinRequest.status == "pending",
        )
    )).first()
    if join_req is None:
        join_req = (await session.exec(
            select(WorkspaceJoinRequest).where(
                WorkspaceJoinRequest.token == token,
                WorkspaceJoinRequest.status == "pending",
            )
        )).first()
    if not join_req:
        raise HTTPException(404, "Join request not found or already resolved")
    if not ws_accessible(join_req.workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")

    join_req.status = "rejected"
    join_req.resolved_at = now
    session.add(join_req)

    ws = (await session.exec(select(Workspace).where(Workspace.id == join_req.workspace_id))).first()
    await session.commit()

    if ws:
        from app.services.email import send_join_rejected
        asyncio.create_task(send_join_rejected(join_req.requester_email, ws.name))

    return {
        "status": "rejected",
        "workspace_id": join_req.workspace_id,
        "requester_email": join_req.requester_email,
    }
