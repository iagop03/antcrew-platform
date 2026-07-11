"""BYOK (Bring Your Own Key) — per-workspace LLM API key management.

Fernet encryption mirrors the pattern in slack_hitl.py, but uses a separate
env var (BYOK_ENCRYPTION_KEY) so the two secrets are independent.

Cost multipliers (applied in listener.py at pipeline.end):
  MANAGED_COST_MULTIPLIER: client pays raw LLM cost × 3.0 (platform provides the key)
  BYOK_SERVICE_MULTIPLIER: client pays raw LLM cost × 0.4 (service fee only)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

MANAGED_COST_MULTIPLIER: float = 3.0
BYOK_SERVICE_MULTIPLIER: float = 0.4

_VALID_PROVIDERS = frozenset({"anthropic", "openai"})


def _encrypt(key: str) -> str:
    enc_key = os.environ.get("BYOK_ENCRYPTION_KEY", "")
    if not enc_key:
        log.warning("byok: BYOK_ENCRYPTION_KEY not set — storing LLM key in plain text (dev mode)")
        return key
    try:
        from cryptography.fernet import Fernet
        return Fernet(enc_key.encode()).encrypt(key.encode()).decode()
    except Exception as exc:
        raise RuntimeError(f"BYOK key encryption failed: {exc}") from exc


def _decrypt(key_enc: str) -> str:
    enc_key = os.environ.get("BYOK_ENCRYPTION_KEY", "")
    if not enc_key:
        return key_enc  # plain text (dev mode)
    try:
        from cryptography.fernet import Fernet
        return Fernet(enc_key.encode()).decrypt(key_enc.encode()).decode()
    except Exception:
        return key_enc  # plain text fallback for keys stored before encryption was enabled


async def get_workspace_llm_key(
    session,
    workspace_id: int,
    provider: str,
) -> Optional[str]:
    """Return the decrypted LLM API key for a BYOK workspace, or None if not configured."""
    from sqlmodel import select
    from app.models.run import LLMProviderKey

    row = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == workspace_id)
        .where(LLMProviderKey.provider == provider)
    )).first()
    if row is None:
        return None
    return _decrypt(row.key_enc)


def get_cost_multiplier(llm_key_mode: str) -> float:
    """Return the billing multiplier for a workspace's LLM key mode."""
    return BYOK_SERVICE_MULTIPLIER if llm_key_mode == "byok" else MANAGED_COST_MULTIPLIER
