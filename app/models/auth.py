"""Authentication and membership models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow


class ApiKey(SQLModel, table=True):
    """Platform API key — used in multi-key mode when PLATFORM_API_KEY env is not set."""

    __tablename__ = "api_key"

    __table_args__ = (
        UniqueConstraint("label", "workspace_id", name="uq_api_key_label_workspace"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = Field(index=True)
    key_hash: str  # bcrypt hash (or legacy sha256 until next login)
    key_prefix: Optional[str] = Field(default=None, index=True)  # sha256(raw)[:16] for O(1) lookup
    workspace_id: Optional[int] = Field(default=None)
    role: str = Field(default="write")  # admin | write | read | reviewer
    email: Optional[str] = Field(default=None)  # for HITL assignment notifications
    slack_user_id: Optional[str] = Field(default=None)      # Slack member ID (U…) for DM notifications
    telegram_chat_id: Optional[str] = Field(default=None)   # Telegram chat ID for bot notifications
    user_id: Optional[int] = Field(default=None, index=True)  # FK → user.id (set on register)
    created_at: datetime = Field(default_factory=_utcnow)
    revoked_at: Optional[datetime] = Field(default=None)


class WorkspaceMembership(SQLModel, table=True):
    """Many-to-many between ApiKey and Workspace — allows one key to access multiple workspaces.

    When a key has membership rows, the set of accessible workspace IDs is the union of
    all memberships. The key's own workspace_id remains its primary workspace (used for
    creating new resources). Keys with no memberships fall back to workspace_id scoping.
    """

    __tablename__ = "workspace_membership"

    id: Optional[int] = Field(default=None, primary_key=True)
    api_key_id: int = Field(index=True)
    workspace_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class User(SQLModel, table=True):
    """Platform user with email+password credentials."""

    __tablename__ = "user"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    display_name: Optional[str] = Field(default=None)
    email_verified_at: Optional[datetime] = Field(default=None)
    totp_secret: Optional[str] = Field(default=None)   # base32 TOTP secret; NULL = MFA disabled
    mfa_enabled: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)


class UserSession(SQLModel, table=True):
    """Browser session backed by a UUID4 token stored in an HttpOnly cookie."""

    __tablename__ = "user_session"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: Optional[str] = Field(default=None, unique=True, index=True)  # legacy plaintext — NULL for new sessions
    token_hash: Optional[str] = Field(default=None, unique=True, index=True)  # sha256(raw_token) — primary lookup
    user_id: Optional[int] = Field(default=None, index=True)
    api_key_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    revoked: bool = Field(default=False)


class EmailVerification(SQLModel, table=True):
    """6-digit email verification code; invalidated on resend or successful use."""

    __tablename__ = "email_verification"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    code: Optional[str] = Field(default=None)      # legacy plaintext — NULL for new codes
    code_hash: Optional[str] = Field(default=None, index=True)  # HMAC-SHA256(SECRET_KEY, code)
    expires_at: datetime
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)


class WorkspaceInvite(SQLModel, table=True):
    """Invite link that grants a specific email access to a workspace."""

    __tablename__ = "workspace_invite"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: Optional[str] = Field(default=None, unique=True, index=True)        # legacy plaintext — NULL for new invites
    token_hash: Optional[str] = Field(default=None, unique=True, index=True)   # sha256(raw_token) — primary lookup
    workspace_id: int = Field(index=True)
    invitee_email: str = Field(index=True)
    inviter_email: str
    role: str = Field(default="write")             # admin | write | read | reviewer
    status: str = Field(default="pending")          # pending | accepted | expired | revoked
    expires_at: datetime
    created_at: datetime = Field(default_factory=_utcnow)
    accepted_at: Optional[datetime] = Field(default=None)


class WorkspaceJoinRequest(SQLModel, table=True):
    """Request from an authenticated user to join a workspace they found by slug."""

    __tablename__ = "workspace_join_request"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True)    # for approve/reject one-click URL
    workspace_id: int = Field(index=True)
    requester_email: str = Field(index=True)
    requested_role: str = Field(default="write")
    status: str = Field(default="pending")          # pending | approved | rejected
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = Field(default=None)
