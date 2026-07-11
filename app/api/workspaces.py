"""Workspace CRUD — isolated project scopes for multi-team deployments."""
from __future__ import annotations

import re as _re
from typing import Optional

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlmodel import col, select
from sqlalchemy import select as sa_select  # noqa: F401 — used indirectly
from sqlmodel.ext.asyncio.session import AsyncSession

from sqlalchemy import func, select as sa_select

from app.core.auth import require_api_key, require_role, get_workspace_context, WorkspaceContext, ws_accessible
from app.core.database import get_session
from app.core.security import validate_external_url
from app.models.run import Workspace, Run, HitlReview, WebhookConfig, WebhookEvent, ApiKey, WorkspaceMembership, LLMProviderKey

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)

_REPO_URL_RE = _re.compile(
    r"^(https?://[\w.\-]+/[\w.\-/]+|git@[\w.\-]+:[\w.\-/]+)(\.git)?$"
)


class CreateWorkspace(BaseModel):
    name: str
    slug: str
    max_cost_usd: Optional[float] = None
    default_repo_url: Optional[str] = None
    hitl_default: bool = False

    @field_validator("slug")
    @classmethod
    def slug_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if not _re.match(r"^[a-z0-9-]+$", v):
            raise ValueError("slug must be lowercase alphanumeric with hyphens only")
        return v

    @field_validator("default_repo_url")
    @classmethod
    def repo_url_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _REPO_URL_RE.match(v):
            raise ValueError("default_repo_url must be an HTTPS or SSH git URL")
        return v


class UpdateBudget(BaseModel):
    max_cost_usd: Optional[float] = None  # None removes the limit


class UpdateDefaultRepo(BaseModel):
    default_repo_url: Optional[str] = None  # None clears the default

    @field_validator("default_repo_url")
    @classmethod
    def repo_url_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _REPO_URL_RE.match(v):
            raise ValueError("default_repo_url must be an HTTPS or SSH git URL")
        return v


class UpdateHitlDefault(BaseModel):
    hitl_default: bool


class UpdateHitlTimeout(BaseModel):
    hitl_timeout_s: Optional[float] = None  # None = use global HITL_TIMEOUT_S env var


class UpdateSlack(BaseModel):
    slack_webhook_url: Optional[str] = None  # None clears the incoming webhook URL
    slack_channel_id: Optional[str] = None   # Slack channel ID for interactive HITL


class UpdateSlackTokens(BaseModel):
    bot_token: str             # xoxb-… required
    app_token: Optional[str] = None  # xapp-… optional, enables Socket Mode

    @field_validator("bot_token")
    @classmethod
    def bot_token_format(cls, v: str) -> str:
        if not v.startswith("xoxb-"):
            raise ValueError("bot_token must start with xoxb-")
        return v

    @field_validator("app_token")
    @classmethod
    def app_token_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("xapp-"):
            raise ValueError("app_token must start with xapp-")
        return v


class CreateWebhookConfig(BaseModel):
    url: str
    events: list[str] = ["pipeline.end"]
    label: Optional[str] = None

    @field_validator("url")
    @classmethod
    def url_must_be_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("url must start with https://")
        return v

    @field_validator("events")
    @classmethod
    def events_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("events must contain at least one event type")
        return v


class WorkspacePublic(BaseModel):
    """Workspace response that never exposes encrypted token fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    max_cost_usd: Optional[float] = None
    budget_exceeded: bool
    total_cost_usd: float
    default_repo_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    slack_channel_id: Optional[str] = None
    slack_bot_configured: bool = False
    slack_app_configured: bool = False
    hitl_default: bool
    hitl_timeout_s: Optional[float] = None
    stripe_customer_id: Optional[str] = None
    subscription_status: Optional[str] = None
    billing_provider: str = "mor"
    llm_key_mode: str = "managed"
    byok_providers: list[str] = []
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _from_workspace(cls, data: object) -> object:
        if hasattr(data, "slack_bot_token_enc"):
            return {
                "id": data.id,
                "name": data.name,
                "slug": data.slug,
                "max_cost_usd": data.max_cost_usd,
                "budget_exceeded": (
                    data.max_cost_usd is not None
                    and data.total_cost_usd >= data.max_cost_usd
                ),
                "total_cost_usd": data.total_cost_usd,
                "default_repo_url": data.default_repo_url,
                "slack_webhook_url": data.slack_webhook_url,
                "slack_channel_id": data.slack_channel_id,
                "slack_bot_configured": bool(data.slack_bot_token_enc),
                "slack_app_configured": bool(data.slack_app_token_enc),
                "hitl_default": data.hitl_default,
                "hitl_timeout_s": data.hitl_timeout_s,
                "stripe_customer_id": getattr(data, "stripe_customer_id", None),
                "subscription_status": getattr(data, "subscription_status", None),
                "billing_provider": getattr(data, "billing_provider", "mor"),
                "llm_key_mode": getattr(data, "llm_key_mode", "managed"),
                "byok_providers": getattr(data, "_byok_providers", []),
                "created_at": data.created_at,
            }
        return data


class WebhookConfigOut(BaseModel):
    """API response shape for WebhookConfig — includes events as a proper list."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    url: str
    events: list[str]
    label: Optional[str] = None
    enabled: bool
    created_at: datetime


@router.get("/", response_model=list[WorkspacePublic])
async def list_workspaces(session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(Workspace))
    return list(result.all())


@router.post("/", status_code=201, response_model=WorkspacePublic,
             dependencies=[Depends(require_role("admin"))])
async def create_workspace(body: CreateWorkspace, session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(Workspace).where(Workspace.slug == body.slug))
    if result.first():
        raise HTTPException(409, f"Workspace with slug {body.slug!r} already exists")
    ws = Workspace(
        name=body.name,
        slug=body.slug,
        max_cost_usd=body.max_cost_usd,
        default_repo_url=body.default_repo_url,
        hitl_default=body.hitl_default,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.get("/{workspace_id}", response_model=WorkspacePublic)
async def get_workspace(workspace_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    return ws


@router.patch("/{workspace_id}/budget", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_budget(
    workspace_id: int,
    body: UpdateBudget,
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the spending limit for a workspace.

    Pass ``max_cost_usd: null`` to remove the limit.
    Once the limit is reached, POST /run/ will return 422 for this workspace.
    """
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    ws.max_cost_usd = body.max_cost_usd
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.get("/{workspace_id}/spend")
async def workspace_spend(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return total spend and budget status for a workspace."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")

    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        run_count = (await session.execute(
            sa_select(func.count()).select_from(Run).where(Run.workspace_id == workspace_id)
        )).scalar() or 0

    total_spend = round(ws.total_cost_usd, 6)
    budget = ws.max_cost_usd
    exhausted = budget is not None and total_spend >= budget
    return {
        "workspace_id": workspace_id,
        "slug": ws.slug,
        "total_spend_usd": total_spend,
        "budget_usd": budget,
        "remaining_usd": round(budget - total_spend, 6) if budget is not None else None,
        "exhausted": exhausted,
        "run_count": run_count,
    }


@router.patch("/{workspace_id}/repo", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_default_repo(
    workspace_id: int,
    body: UpdateDefaultRepo,
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the default repo URL for a workspace.

    When set, POST /run/ requests that omit ``repo_url`` will automatically
    clone this repository and inject its contents as context.
    Pass ``default_repo_url: null`` to clear the default.
    """
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    ws.default_repo_url = body.default_repo_url
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}/hitl", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_hitl_default(
    workspace_id: int,
    body: UpdateHitlDefault,
    session: AsyncSession = Depends(get_session),
):
    """Enable or disable HITL by default for all runs in this workspace.

    When ``hitl_default: true``, POST /run/ requests that don't explicitly set
    ``hitl: false`` will automatically pause for human review at every agent.
    """
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    ws.hitl_default = body.hitl_default
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.patch("/{workspace_id}/hitl-timeout", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_hitl_timeout(
    workspace_id: int,
    body: UpdateHitlTimeout,
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the per-workspace HITL review timeout.

    When set, overrides the global ``HITL_TIMEOUT_S`` env var for all runs in
    this workspace. Pass ``hitl_timeout_s: null`` to fall back to the global default.
    """
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    if body.hitl_timeout_s is not None and body.hitl_timeout_s <= 0:
        raise HTTPException(422, "hitl_timeout_s must be positive")
    ws.hitl_timeout_s = body.hitl_timeout_s
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@router.get("/{workspace_id}/reviews", response_model=list[HitlReview])
async def workspace_reviews(
    workspace_id: int,
    status: str = Query("pending", description="Filter by status"),
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
):
    """List HITL reviews for all runs in a workspace.

    Useful for reviewer dashboards that need pending reviews across all runs
    in a workspace without knowing individual run IDs.
    """
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    if not result.first():
        raise HTTPException(404, f"Workspace {workspace_id} not found")

    stmt = (
        select(HitlReview)
        .join(Run, Run.run_id == HitlReview.run_id)
        .where(Run.workspace_id == workspace_id)
        .where(HitlReview.status == status)
        .order_by(HitlReview.created_at.desc())  # type: ignore[union-attr]
        .limit(limit)
    )
    reviews_result = await session.exec(stmt)
    return list(reviews_result.all())


@router.patch("/{workspace_id}/slack", response_model=WorkspacePublic,
              dependencies=[Depends(require_role("admin"))])
async def set_slack_webhook(
    workspace_id: int,
    body: UpdateSlack,
    session: AsyncSession = Depends(get_session),
):
    """Set or clear the per-workspace Slack webhook URL for HITL notifications.

    When set, HITL review notifications are sent to this URL (Slack incoming webhook format)
    instead of the global HITL_WEBHOOK_URL env var. Pass ``slack_webhook_url: null`` to clear.
    """
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    ws.slack_webhook_url = body.slack_webhook_url
    if body.slack_channel_id is not None:
        ws.slack_channel_id = body.slack_channel_id
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


async def _hooks_with_events(
    session: AsyncSession, workspace_id: int
) -> list[WebhookConfigOut]:
    """Load WebhookConfig rows and join their event types from webhook_event."""
    hooks = (await session.exec(
        select(WebhookConfig).where(WebhookConfig.workspace_id == workspace_id)
    )).all()
    if not hooks:
        return []
    hook_ids = [h.id for h in hooks if h.id is not None]
    ev_rows = (await session.exec(
        select(WebhookEvent).where(col(WebhookEvent.webhook_id).in_(hook_ids))
    )).all()
    events_by_hook: dict[int, list[str]] = {}
    for ev in ev_rows:
        events_by_hook.setdefault(ev.webhook_id, []).append(ev.event_type)
    return [
        WebhookConfigOut(
            id=h.id,  # type: ignore[arg-type]
            workspace_id=h.workspace_id,
            url=h.url,
            events=events_by_hook.get(h.id, []),  # type: ignore[arg-type]
            label=h.label,
            enabled=h.enabled,
            created_at=h.created_at,
        )
        for h in hooks
    ]


@router.patch("/{workspace_id}/slack-tokens",
              dependencies=[Depends(require_role("admin"))])
async def set_slack_tokens(
    workspace_id: int,
    body: UpdateSlackTokens,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Store per-workspace Slack bot and (optionally) app tokens, encrypted at rest.

    Tokens are encrypted with Fernet using SLACK_TOKEN_ENCRYPTION_KEY env var.
    When the key is absent, tokens are stored in plain text (dev mode).

    Generate an encryption key:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    These tokens override the global SLACK_BOT_TOKEN / SLACK_APP_TOKEN env vars
    for HITL notifications sent to this workspace's Slack channel.
    """
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    from app.core.slack_hitl import _encrypt
    ws.slack_bot_token_enc = _encrypt(body.bot_token)
    if body.app_token is not None:
        ws.slack_app_token_enc = _encrypt(body.app_token)
    session.add(ws)
    await session.commit()
    return {
        "workspace_id": workspace_id,
        "slack_bot_configured": True,
        "slack_app_configured": ws.slack_app_token_enc is not None,
    }


@router.delete("/{workspace_id}/slack-tokens", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def clear_slack_tokens(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove per-workspace Slack tokens, reverting to global env-var tokens."""
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    ws.slack_bot_token_enc = None
    ws.slack_app_token_enc = None
    session.add(ws)
    await session.commit()


@router.get("/{workspace_id}/webhooks", response_model=list[WebhookConfigOut])
async def list_webhook_configs(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
):
    """List registered webhooks for a workspace, including their subscribed event types."""
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    if not result.first():
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    return await _hooks_with_events(session, workspace_id)


@router.post("/{workspace_id}/webhooks", status_code=201, response_model=WebhookConfigOut,
             dependencies=[Depends(require_role("admin"))])
async def create_webhook_config(
    workspace_id: int,
    body: CreateWebhookConfig,
    session: AsyncSession = Depends(get_session),
):
    """Register a webhook URL for a workspace.

    The webhook fires on the event types listed in ``events`` (default: ``pipeline.end``).
    Each registered URL receives a ``WebhookDelivery`` row and is retried up to 5 times
    on failure with exponential backoff.
    """
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    if not result.first():
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    try:
        validate_external_url(body.url, allow_http=True)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid webhook URL: {exc}")
    hook = WebhookConfig(workspace_id=workspace_id, url=body.url, label=body.label)
    session.add(hook)
    await session.flush()  # populate hook.id before creating WebhookEvent rows
    for event_type in body.events:
        session.add(WebhookEvent(webhook_id=hook.id, event_type=event_type))
    await session.commit()
    await session.refresh(hook)
    return WebhookConfigOut(
        id=hook.id,  # type: ignore[arg-type]
        workspace_id=hook.workspace_id,
        url=hook.url,
        events=body.events,
        label=hook.label,
        enabled=hook.enabled,
        created_at=hook.created_at,
    )


@router.delete("/{workspace_id}/webhooks/{webhook_id}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def delete_webhook_config(
    workspace_id: int,
    webhook_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Remove a registered webhook and its event subscriptions."""
    result = await session.exec(
        select(WebhookConfig)
        .where(WebhookConfig.id == webhook_id)
        .where(WebhookConfig.workspace_id == workspace_id)
    )
    hook = result.first()
    if not hook:
        raise HTTPException(404, f"Webhook {webhook_id} not found in workspace {workspace_id}")
    # Delete event subscriptions first
    ev_rows = (await session.exec(
        select(WebhookEvent).where(WebhookEvent.webhook_id == webhook_id)
    )).all()
    for ev in ev_rows:
        await session.delete(ev)
    await session.delete(hook)
    await session.commit()


@router.delete("/{workspace_id}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def delete_workspace(workspace_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.exec(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    await session.delete(ws)
    await session.commit()


# ---------------------------------------------------------------------------
# BYOK — per-workspace LLM API key management
# ---------------------------------------------------------------------------

class SetLLMModeRequest(BaseModel):
    mode: str  # "managed" | "byok"

    @field_validator("mode")
    @classmethod
    def mode_valid(cls, v: str) -> str:
        if v not in ("managed", "byok"):
            raise ValueError("mode must be 'managed' or 'byok'")
        return v


_BYOK_PROVIDERS = frozenset({"anthropic", "openai", "groq", "gemini", "ollama"})
_KEYLESS_PROVIDERS = frozenset({"ollama"})


class StoreLLMKeyRequest(BaseModel):
    provider: str
    api_key: str = ""
    base_url: Optional[str] = None
    confirm_overwrite: bool = False

    @field_validator("provider")
    @classmethod
    def provider_valid(cls, v: str) -> str:
        if v not in _BYOK_PROVIDERS:
            raise ValueError(f"provider must be one of: {', '.join(sorted(_BYOK_PROVIDERS))}")
        return v

    @field_validator("api_key")
    @classmethod
    def key_valid(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def key_required_unless_keyless(self) -> "StoreLLMKeyRequest":
        if self.provider not in _KEYLESS_PROVIDERS and not self.api_key:
            raise ValueError(f"api_key is required for provider '{self.provider}'")
        return self


class LLMKeyOut(BaseModel):
    provider: str
    configured: bool = True
    base_url: Optional[str] = None
    created_at: datetime


@router.patch("/{workspace_id}/llm-mode", response_model=WorkspacePublic)
async def set_llm_mode(
    workspace_id: int,
    body: SetLLMModeRequest,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> WorkspacePublic:
    """Switch a workspace between managed (platform key) and byok (customer key) modes.

    Cannot switch to 'byok' unless at least one LLM key is already stored.
    Switching back to 'managed' is always allowed (stored keys are preserved).
    """
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")

    if body.mode == "byok":
        existing = (await session.exec(
            select(LLMProviderKey).where(LLMProviderKey.workspace_id == workspace_id).limit(1)
        )).first()
        if not existing:
            raise HTTPException(
                422,
                "Cannot switch to BYOK mode: no LLM keys configured. "
                "Store at least one key via POST /workspaces/{id}/llm-keys first."
            )

    ws.llm_key_mode = body.mode
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return WorkspacePublic.model_validate(ws)


@router.get("/{workspace_id}/llm-keys", response_model=list[LLMKeyOut])
async def list_llm_keys(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> list[LLMKeyOut]:
    """List configured LLM providers for a workspace. Never returns plaintext keys."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    rows = (await session.exec(
        select(LLMProviderKey).where(LLMProviderKey.workspace_id == workspace_id)
    )).all()
    return [LLMKeyOut(provider=r.provider, base_url=getattr(r, "base_url", None), created_at=r.created_at) for r in rows]


@router.post("/{workspace_id}/llm-keys", status_code=201)
async def store_llm_key(
    workspace_id: int,
    body: StoreLLMKeyRequest,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> dict:
    """Store or rotate a BYOK LLM API key for a workspace.

    The key is encrypted at rest using Fernet (BYOK_ENCRYPTION_KEY env var).
    Overwriting an existing key for the same provider requires confirm_overwrite=true.
    """
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    if not (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first():
        raise HTTPException(404, f"Workspace {workspace_id} not found")

    existing = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == workspace_id)
        .where(LLMProviderKey.provider == body.provider)
    )).first()

    if existing and not body.confirm_overwrite:
        raise HTTPException(
            409,
            f"A key for provider '{body.provider}' already exists. "
            "Set confirm_overwrite=true to replace it."
        )

    from app.core.byok import _encrypt
    encrypted = _encrypt(body.api_key) if body.api_key else ""

    if existing:
        existing.key_enc = encrypted
        existing.base_url = body.base_url
        from app.models.run import _utcnow
        existing.created_at = _utcnow()
        session.add(existing)
    else:
        session.add(LLMProviderKey(
            workspace_id=workspace_id,
            provider=body.provider,
            key_enc=encrypted,
            base_url=body.base_url,
        ))

    await session.commit()
    return {"workspace_id": workspace_id, "provider": body.provider, "configured": True}


@router.delete("/{workspace_id}/llm-keys/{provider}", status_code=204)
async def delete_llm_key(
    workspace_id: int,
    provider: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> None:
    """Remove a BYOK key. If it was the last key, resets llm_key_mode to 'managed'."""
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    if provider not in _BYOK_PROVIDERS:
        raise HTTPException(422, f"provider must be one of: {', '.join(sorted(_BYOK_PROVIDERS))}")

    row = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == workspace_id)
        .where(LLMProviderKey.provider == provider)
    )).first()
    if not row:
        raise HTTPException(404, f"No key for provider '{provider}' in workspace {workspace_id}")

    await session.delete(row)

    # If no keys remain, revert the workspace to managed mode
    remaining = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == workspace_id)
    )).first()
    if not remaining:
        ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
        if ws and ws.llm_key_mode == "byok":
            ws.llm_key_mode = "managed"
            session.add(ws)

    await session.commit()


# ---------------------------------------------------------------------------
# P3.2 — Workspace membership (multi-workspace per key)
# ---------------------------------------------------------------------------

class MembershipCreate(BaseModel):
    api_key_id: int


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    api_key_id: int
    workspace_id: int
    key_label: Optional[str] = None
    created_at: datetime


@router.get("/{workspace_id}/members", response_model=list[MembershipOut],
            dependencies=[Depends(require_role("admin"))])
async def list_members(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[MembershipOut]:
    """List API keys that have membership access to this workspace."""
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    memberships = (await session.exec(
        select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace_id)
    )).all()
    # Attach key labels for readability
    key_ids = [m.api_key_id for m in memberships]
    keys: dict[int, str] = {}
    if key_ids:
        key_rows = (await session.exec(
            select(ApiKey).where(col(ApiKey.id).in_(key_ids))
        )).all()
        keys = {k.id: k.label for k in key_rows if k.id is not None}
    return [
        MembershipOut(
            id=m.id,  # type: ignore[arg-type]
            api_key_id=m.api_key_id,
            workspace_id=m.workspace_id,
            key_label=keys.get(m.api_key_id),
            created_at=m.created_at,
        )
        for m in memberships
    ]


@router.post("/{workspace_id}/members", status_code=201, response_model=MembershipOut,
             dependencies=[Depends(require_role("admin"))])
async def add_member(
    workspace_id: int,
    body: MembershipCreate,
    session: AsyncSession = Depends(get_session),
) -> MembershipOut:
    """Grant an API key access to a workspace (multi-workspace membership).

    Unlike the key's primary workspace_id, memberships let one key read across
    multiple workspaces without changing the key's primary scope for writes.
    """
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    key = (await session.exec(select(ApiKey).where(ApiKey.id == body.api_key_id))).first()
    if not key:
        raise HTTPException(404, f"ApiKey {body.api_key_id} not found")
    existing = (await session.exec(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.api_key_id == body.api_key_id)
        .where(WorkspaceMembership.workspace_id == workspace_id)
    )).first()
    if existing:
        return MembershipOut(
            id=existing.id,  # type: ignore[arg-type]
            api_key_id=existing.api_key_id,
            workspace_id=existing.workspace_id,
            key_label=key.label,
            created_at=existing.created_at,
        )
    m = WorkspaceMembership(api_key_id=body.api_key_id, workspace_id=workspace_id)
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return MembershipOut(
        id=m.id,  # type: ignore[arg-type]
        api_key_id=m.api_key_id,
        workspace_id=m.workspace_id,
        key_label=key.label,
        created_at=m.created_at,
    )


@router.delete("/{workspace_id}/members/{api_key_id}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def remove_member(
    workspace_id: int,
    api_key_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke a key's multi-workspace membership for this workspace."""
    m = (await session.exec(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.api_key_id == api_key_id)
        .where(WorkspaceMembership.workspace_id == workspace_id)
    )).first()
    if not m:
        raise HTTPException(404, "Membership not found")
    await session.delete(m)
    await session.commit()
