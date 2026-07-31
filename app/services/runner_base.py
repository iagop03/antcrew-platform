"""Shared infrastructure for runner.py and engine_runner.py.

Extracted to eliminate the cross-module private import:
    engine_runner → runner._mark_workspace_budget_status

Both runners import budget helpers from here. Neither imports from the other.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# Per-workspace asyncio locks: serialise budget check + budget update pairs.
# Prevents two concurrent dispatch() calls from both reading the same total_cost_usd
# before either run has committed its cost (TOCTOU on the budget gate).
# Only effective within a single process — PostgreSQL FOR UPDATE covers multi-process.
_budget_locks: dict[int, asyncio.Lock] = {}


def _get_budget_lock(workspace_id: int) -> asyncio.Lock:
    if workspace_id not in _budget_locks:
        _budget_locks[workspace_id] = asyncio.Lock()
    return _budget_locks[workspace_id]


async def _check_workspace_budget(workspace_id: int) -> None:
    """Raise ValueError if the workspace has exhausted its budget or subscription is suspended.

    Must be called while holding _get_budget_lock(workspace_id) to prevent TOCTOU races.
    Covers both managed (trial and paid) and BYOK workspaces.
    """
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _db_engine
    from app.models.run import Workspace
    from app.services.billing import BLOCKED_STATUSES

    async with AsyncSession(_db_engine, expire_on_commit=False) as sess:
        stmt = select(Workspace).where(Workspace.id == workspace_id).with_for_update()
        ws = (await sess.exec(stmt)).first()

        if ws is None:
            return
        if ws.subscription_status in BLOCKED_STATUSES:
            raise ValueError(
                f"Workspace subscription is '{ws.subscription_status}'. "
                "Please update your billing details to continue running pipelines."
            )
        if ws.max_cost_usd is None:
            return
        if ws.total_cost_usd >= ws.max_cost_usd:
            if getattr(ws, "is_trial", False):
                raise ValueError(
                    f"Trial credit exhausted: ${ws.total_cost_usd:.4f} spent of "
                    f"${ws.max_cost_usd:.2f} trial credit. "
                    "Upgrade to a paid plan from your workspace settings to continue."
                )
            raise ValueError(
                f"Workspace budget exhausted: ${ws.total_cost_usd:.4f} spent of "
                f"${ws.max_cost_usd:.2f} limit. Update the workspace budget to continue."
            )


async def resolve_workspace_llm_config(
    session: Any,
    workspace: Any,
    model: str = "claude",
) -> tuple[Optional[str], Optional[str]]:
    """Return (api_key, base_url) for the workspace's active LLM mode.

    managed → (None, None): platform env key is used by the LLM client.
    byok    → (customer_key, custom_base_url_or_None)
    proxy   → (proxy_token, proxy_url): platform sends token; proxy substitutes real key.

    Both runners call this; the result flows into build_llm(api_key=..., base_url=...).
    The proxy token is decrypted here — the Anthropic key never touches platform memory.
    """
    mode = getattr(workspace, "llm_key_mode", "managed")

    if mode == "byok":
        from app.core.byok import get_workspace_llm_key_for_model
        byok = await get_workspace_llm_key_for_model(session, workspace.id, model)
        if byok:
            return byok.key, byok.base_url
        return None, None

    if mode == "proxy":
        proxy_url = getattr(workspace, "proxy_url", None)
        proxy_token_enc = getattr(workspace, "proxy_token_enc", None)
        if proxy_url and proxy_token_enc:
            from app.core.byok import _decrypt, _provider_for_model
            token = _decrypt(proxy_token_enc)
            # Append provider so the proxy can route to the right upstream key.
            # e.g. base_url = "https://proxy.example.com/anthropic" for Claude models.
            provider = _provider_for_model(model)
            return token, f"{proxy_url.rstrip('/')}/{provider}"
        return None, None

    return None, None  # managed: use platform env key


async def _mark_workspace_budget_status(workspace_id: int) -> None:
    """After a run completes, recompute total spend via SQL SUM and update cached field.

    Acquires _get_budget_lock(workspace_id) so that a concurrent dispatch() cannot
    pass _check_workspace_budget() while this write is in-flight.
    """
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _db_engine
    from app.models.run import Workspace, Run

    try:
        async with _get_budget_lock(workspace_id):
            async with AsyncSession(_db_engine, expire_on_commit=False) as session:
                ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
                if ws is None:
                    return
                costs = await session.exec(
                    select(Run.cost_usd).where(Run.workspace_id == workspace_id)
                )
                total = sum(c or 0.0 for c in costs.all())
                ws.total_cost_usd = total
                if ws.max_cost_usd is not None and total >= ws.max_cost_usd:
                    log.warning(
                        "runner: workspace %d budget exhausted ($%.4f of $%.2f limit)",
                        workspace_id, total, ws.max_cost_usd,
                    )
                session.add(ws)
                await session.commit()
    except Exception as exc:
        log.warning("runner: failed to update budget for workspace %d: %s", workspace_id, exc)
