"""BYOK — per-workspace LLM API key management."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator, model_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, require_role, get_workspace_context, WorkspaceContext, ws_accessible
from app.core.database import get_session
from app.models.run import Workspace, LLMProviderKey
from app.api.workspaces import WorkspacePublic

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)

_BYOK_PROVIDERS = frozenset({"anthropic", "openai", "groq", "gemini", "ollama"})
_KEYLESS_PROVIDERS = frozenset({"ollama"})


class SetLLMModeRequest(BaseModel):
    mode: str  # "managed" | "byok"

    @field_validator("mode")
    @classmethod
    def mode_valid(cls, v: str) -> str:
        if v not in ("managed", "byok"):
            raise ValueError("mode must be 'managed' or 'byok'")
        return v


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
    return [
        LLMKeyOut(provider=r.provider, base_url=getattr(r, "base_url", None), created_at=r.created_at)
        for r in rows
    ]


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

    remaining = (await session.exec(
        select(LLMProviderKey).where(LLMProviderKey.workspace_id == workspace_id)
    )).first()
    if not remaining:
        ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
        if ws and ws.llm_key_mode == "byok":
            ws.llm_key_mode = "managed"
            session.add(ws)

    await session.commit()
