"""Workspace, BYOK key, contract schema, and custom-agent models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel, JSON, Column

from app.models._utils import _utcnow


class Workspace(SQLModel, table=True):
    """Isolated project scope for multi-team deployments."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    max_cost_usd: Optional[float] = Field(default=None)
    total_cost_usd: float = Field(default=0.0)  # cached total; updated after each run via SQL SUM
    default_repo_url: Optional[str] = Field(default=None)
    slack_webhook_url: Optional[str] = Field(default=None)   # per-workspace HITL incoming webhook URL
    slack_channel_id: Optional[str] = Field(default=None)    # Slack channel ID for interactive HITL
    slack_bot_token_enc: Optional[str] = Field(default=None) # encrypted xoxb-… (Fernet, key=SLACK_TOKEN_ENCRYPTION_KEY)
    slack_app_token_enc: Optional[str] = Field(default=None) # encrypted xapp-… for Socket Mode
    hitl_default: bool = Field(default=False)
    hitl_timeout_s: Optional[float] = Field(default=None)  # per-workspace HITL timeout (overrides env HITL_TIMEOUT_S)
    stripe_customer_id: Optional[str] = Field(default=None, index=True)  # cus_...
    stripe_subscription_id: Optional[str] = Field(default=None)           # sub_...
    subscription_status: Optional[str] = Field(default=None)              # active | trialing | past_due | canceled | unpaid
    billing_provider: str = Field(default="mor")                          # mor | stripe
    mor_customer_id: Optional[str] = Field(default=None)                  # Lemon Squeezy customer ID
    mor_subscription_id: Optional[str] = Field(default=None)              # Lemon Squeezy subscription ID
    llm_key_mode: str = Field(default="managed")  # managed | byok | proxy
    byok_managed_fallback: bool = Field(default=False)  # fall back to platform key when no BYOK key for a model
    proxy_url: Optional[str] = Field(default=None)          # antcrew-proxy base URL (e.g. https://proxy.example.com)
    proxy_token_enc: Optional[str] = Field(default=None)    # Fernet-encrypted UUID token sent to the proxy
    is_trial: bool = Field(default=True)  # workspace is on the free-trial credit; costs at TRIAL_MULTIPLIER
    owner_user_id: Optional[int] = Field(default=None, index=True)  # user.id of the registering user
    ticket_prefix: str = Field(default="TKT")   # e.g. "PROJ" → ticket display IDs are PROJ-00001
    ticket_counter: int = Field(default=0)       # incremented atomically on each new ticket
    created_at: datetime = Field(default_factory=_utcnow)


class LLMProviderKey(SQLModel, table=True):
    """Per-workspace, per-provider LLM API key for BYOK mode.

    key_enc is Fernet-encrypted with BYOK_ENCRYPTION_KEY, or plaintext in dev mode.
    Unique per (workspace_id, provider) — upsert by deleting and re-inserting.
    """

    __tablename__ = "llm_provider_key"
    __table_args__ = (UniqueConstraint("workspace_id", "provider", name="uq_llm_key_ws_provider"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    provider: str  # anthropic | openai | groq | gemini | ollama | moonshot
    key_enc: str   # Fernet-encrypted or plaintext (dev mode without BYOK_ENCRYPTION_KEY); empty for keyless providers
    base_url: Optional[str] = Field(default=None)  # required for ollama / custom OpenAI-compat endpoints
    created_at: datetime = Field(default_factory=_utcnow)


class WorkspaceContractSchema(SQLModel, table=True):
    """Per-workspace JSON Schema for the custom_fields extension point of an artifact contract.

    Stores a JSON Schema that describes what keys are expected in the PRD.custom_fields
    (or any other extendable contract) for a specific workspace.  Purely informational
    in Phase 1 — operators ignore custom_fields; the schema is used for documentation
    and future prompt-injection.

    contract_name must match an entry in EXTENDABLE_CONTRACTS (e.g. "PRD").
    json_schema is any valid JSON Schema object.
    """

    __tablename__ = "workspace_contract_schema"
    __table_args__ = (
        UniqueConstraint("workspace_id", "contract_name", name="uq_contract_schema_ws_contract"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    contract_name: str  # e.g. "PRD"
    json_schema: dict = Field(default_factory=dict, sa_column=Column(JSON))
    description: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CustomAgentDef(SQLModel, table=True):
    """User-defined agent backed by TemplateAgent — scoped to workspace.

    agent_type is stable (never reused after delete) and matches the ``type``
    field stored on pipeline nodes — e.g. "custom_3".  The system_prompt is
    passed directly to TemplateAgent at runtime via node.agent_cfg.
    """

    __tablename__ = "custom_agent_def"
    __table_args__ = (
        UniqueConstraint("workspace_id", "agent_type", name="uq_custom_agent_ws_type"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    agent_type: str = Field(index=True)   # e.g. "custom_3"
    label: str
    color: str = Field(default="#7c3aed")
    system_prompt: str
    role_description: Optional[str] = Field(default=None)
    phase: str = Field(default="build")
    glyph: str = Field(default="✦")
    created_at: datetime = Field(default_factory=_utcnow)
