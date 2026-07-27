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
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

MANAGED_COST_MULTIPLIER: float = 3.0
BYOK_SERVICE_MULTIPLIER: float = 0.4
PROXY_SERVICE_MULTIPLIER: float = 0.7   # proxy: customer holds key; platform charges service fee
TRIAL_MULTIPLIER: float = 1.0  # trial runs at raw cost (no margin); change via env if needed

# Credit granted to new workspaces in trial mode. Configurable at runtime — no redeploy needed.
TRIAL_CREDIT_USD: float = float(os.environ.get("TRIAL_CREDIT_USD", "5.0"))

_VALID_PROVIDERS = frozenset({"anthropic", "openai", "groq", "gemini", "ollama"})

_IS_DEV = os.environ.get("APP_ENV", "production").lower() in ("dev", "development", "local")


@dataclass
class BYOKKey:
    """Decrypted BYOK credentials for a workspace provider."""
    key: Optional[str]      # API key; None for keyless providers (ollama)
    base_url: Optional[str]  # custom endpoint URL; None unless provider needs it


def _provider_for_model(model_str: str) -> str:
    """Infer the BYOK provider name from a model string."""
    s = model_str.strip().lower()
    if s.startswith("gpt") or s.startswith("o1") or s.startswith("o3") or s.startswith("openai:"):
        return "openai"
    if s.startswith("groq:"):
        return "groq"
    if s.startswith("gemini"):
        return "gemini"
    if s.startswith("ollama:"):
        return "ollama"
    return "anthropic"


def _encrypt(key: str) -> str:
    enc_key = os.environ.get("BYOK_ENCRYPTION_KEY", "")
    if not enc_key:
        if not _IS_DEV:
            raise RuntimeError(
                "BYOK_ENCRYPTION_KEY is required in production. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        log.warning("byok: BYOK_ENCRYPTION_KEY not set — storing key as plain text (dev only)")
        return key
    try:
        from cryptography.fernet import Fernet
        return Fernet(enc_key.encode()).encrypt(key.encode()).decode()
    except Exception as exc:
        raise RuntimeError(f"BYOK key encryption failed: {exc}") from exc


def _decrypt(key_enc: str) -> str:
    enc_key = os.environ.get("BYOK_ENCRYPTION_KEY", "")
    if not enc_key:
        if not _IS_DEV:
            raise RuntimeError(
                "BYOK_ENCRYPTION_KEY is required in production — cannot decrypt stored BYOK keys."
            )
        return key_enc  # dev only: key stored as plain text
    try:
        from cryptography.fernet import Fernet
        return Fernet(enc_key.encode()).decrypt(key_enc.encode()).decode()
    except Exception as exc:
        raise RuntimeError(
            "BYOK key decryption failed — the stored value may pre-date encryption. "
            "Re-enter the BYOK key for this workspace to re-encrypt it. "
            f"Detail: {exc}"
        ) from exc


async def get_workspace_llm_key(
    session,
    workspace_id: int,
    provider: str,
) -> Optional[BYOKKey]:
    """Return decrypted BYOK credentials for a workspace provider, or None if not configured."""
    from sqlmodel import select
    from app.models.run import LLMProviderKey

    row = (await session.exec(
        select(LLMProviderKey)
        .where(LLMProviderKey.workspace_id == workspace_id)
        .where(LLMProviderKey.provider == provider)
    )).first()
    if row is None:
        return None
    raw_key = _decrypt(row.key_enc) if row.key_enc else None
    return BYOKKey(key=raw_key or None, base_url=getattr(row, "base_url", None))


async def get_workspace_llm_key_for_model(
    session,
    workspace_id: int,
    model_str: str,
) -> Optional[BYOKKey]:
    """Infer provider from model string and return BYOK credentials, or None."""
    provider = _provider_for_model(model_str)
    return await get_workspace_llm_key(session, workspace_id, provider)


def get_cost_multiplier(llm_key_mode: str, is_trial: bool = False) -> float:
    """Return the billing multiplier for a workspace."""
    if is_trial:
        return TRIAL_MULTIPLIER
    if llm_key_mode == "byok":
        return BYOK_SERVICE_MULTIPLIER
    if llm_key_mode == "proxy":
        return PROXY_SERVICE_MULTIPLIER
    return MANAGED_COST_MULTIPLIER
