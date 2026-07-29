"""Proxy mode — per-workspace antcrew-proxy token management.

Proxy mode lets a customer run their own antcrew-proxy instance that holds
their real Anthropic API key. The platform generates a UUID token, stores it
encrypted (same Fernet key as BYOK), and sends it to the proxy as the api_key
on every LLM call. The proxy validates the token and substitutes the real key.
The platform never sees the customer's Anthropic key.

Endpoints:
  POST   /workspaces/{id}/proxy/generate  — generate new token (returned once)
  POST   /workspaces/{id}/proxy/activate  — switch llm_key_mode to "proxy"
  GET    /workspaces/{id}/proxy           — status (configured?, proxy_url)
  DELETE /workspaces/{id}/proxy           — revoke token + reset to managed
"""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, require_role, WorkspaceContext, ws_accessible
from app.core.database import get_session
from app.core.exceptions import WorkspaceNotFoundError
from app.models.run import Workspace

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)


class ProxyConfigRequest(BaseModel):
    proxy_url: str

    @field_validator("proxy_url")
    @classmethod
    def url_valid(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith("https://"):
            raise ValueError("proxy_url must use HTTPS")
        return v


class ProxyStatusOut(BaseModel):
    configured: bool
    proxy_url: Optional[str]
    active: bool  # True when llm_key_mode == "proxy"


class ProxyTokenOut(BaseModel):
    token: str  # plaintext — shown once, then gone
    proxy_url: str
    docker_command: str


async def _get_ws(session: AsyncSession, workspace_id: int, ctx: WorkspaceContext) -> Workspace:
    if not ws_accessible(workspace_id, ctx):
        raise HTTPException(403, "This workspace is not accessible with the current API key")
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise WorkspaceNotFoundError(workspace_id)
    return ws


@router.get("/{workspace_id}/proxy", response_model=ProxyStatusOut)
async def get_proxy_status(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> ProxyStatusOut:
    """Return current proxy configuration status for the workspace."""
    ws = await _get_ws(session, workspace_id, ctx)
    return ProxyStatusOut(
        configured=bool(ws.proxy_url and ws.proxy_token_enc),
        proxy_url=ws.proxy_url,
        active=ws.llm_key_mode == "proxy",
    )


@router.post("/{workspace_id}/proxy/generate", response_model=ProxyTokenOut, status_code=201)
async def generate_proxy_token(
    workspace_id: int,
    body: ProxyConfigRequest,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> ProxyTokenOut:
    """Generate a new proxy token and store it encrypted.

    The plaintext token is returned exactly once in this response.
    It is never stored in plaintext — configure your antcrew-proxy with it
    as the PROXY_TOKEN environment variable before it disappears.

    Calling this again rotates the token (old token stops working immediately).
    """
    from app.core.security import validate_external_url
    from app.core.byok import _encrypt

    ws = await _get_ws(session, workspace_id, ctx)

    validate_external_url(body.proxy_url, allow_http=False)

    token = secrets.token_urlsafe(32)
    ws.proxy_url = body.proxy_url
    ws.proxy_token_enc = _encrypt(token)
    session.add(ws)
    await session.commit()

    docker_cmd = (
        f"docker run -d --name antcrew-proxy \\\n"
        f"  -p 8080:8080 \\\n"
        f"  -e PROXY_TOKEN={token} \\\n"
        f"  -e ANTHROPIC_API_KEY=sk-ant-...  `# set only the keys you use` \\\n"
        f"  -e OPENAI_API_KEY=sk-proj-... \\\n"
        f"  -e GROQ_API_KEY=gsk_... \\\n"
        f"  -e MOONSHOT_API_KEY=sk-... \\\n"
        f"  ghcr.io/iagop03/antcrew-proxy:latest"
    )
    return ProxyTokenOut(token=token, proxy_url=body.proxy_url, docker_command=docker_cmd)


@router.post("/{workspace_id}/proxy/activate", status_code=200)
async def activate_proxy_mode(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> dict:
    """Switch the workspace to proxy LLM mode.

    Requires a proxy token to be generated first (POST /proxy/generate).
    """
    ws = await _get_ws(session, workspace_id, ctx)

    if not (ws.proxy_url and ws.proxy_token_enc):
        raise HTTPException(
            422,
            "No proxy token configured. Generate one first via "
            "POST /workspaces/{id}/proxy/generate."
        )

    ws.llm_key_mode = "proxy"
    session.add(ws)
    await session.commit()
    return {"workspace_id": workspace_id, "llm_key_mode": "proxy"}


@router.delete("/{workspace_id}/proxy", status_code=200)
async def revoke_proxy(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(require_role("admin")),
) -> dict:
    """Revoke the proxy token and reset the workspace to managed mode.

    After this call the proxy will reject all requests from the platform —
    make sure to stop the proxy container or rotate its PROXY_TOKEN accordingly.
    """
    ws = await _get_ws(session, workspace_id, ctx)

    ws.proxy_url = None
    ws.proxy_token_enc = None
    if ws.llm_key_mode == "proxy":
        ws.llm_key_mode = "managed"
    session.add(ws)
    await session.commit()
    return {"workspace_id": workspace_id, "llm_key_mode": ws.llm_key_mode, "proxy_revoked": True}
