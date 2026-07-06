"""Platform Slack Socket Mode — interactive HITL via Slack Block Kit buttons.

When SLACK_BOT_TOKEN + SLACK_APP_TOKEN are configured, HITL reviews are posted
as interactive Block Kit cards to the workspace's Slack channel. Reviewers click
Approve / Reject / Feedback in Slack — no browser required.

Resolution goes through the DB:
  button click → Bolt thread → _resolve_review_sync() → HitlReview.status updated
  → PlatformChannel._poll_db_for_decision() detects change → pipeline resumes.

Global tokens (env vars) — single Socket Mode listener shared across all workspaces:
  SLACK_BOT_TOKEN   — xoxb-…  (Bot Token, chat:write scope)
  SLACK_APP_TOKEN   — xapp-…  (App-Level Token, Socket Mode enabled)
  SLACK_CHANNEL_ID  — default channel when workspace has no slack_channel_id set

Per-workspace channel: set Workspace.slack_channel_id via PATCH /workspaces/{id}/slack.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from typing import Optional

log = logging.getLogger(__name__)

_STARTED = False  # kept for backward compat with maybe_start_from_env
_handlers: dict[str, bool] = {}  # token_prefix → True (one handler per unique bot token)
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the platform's main asyncio event loop.

    Called once at lifespan startup so _resolve_review_sync can submit coroutines
    to the already-running loop instead of spinning up a new one per button click.
    """
    global _main_loop
    _main_loop = loop


# ---------------------------------------------------------------------------
# Token encryption helpers
# ---------------------------------------------------------------------------

def _token_key(bot_token: str) -> str:
    """Short stable key for deduplicating Socket Mode handlers."""
    return bot_token[:24]


def _encrypt(token: str) -> str:
    """Encrypt a Slack token using Fernet + SLACK_TOKEN_ENCRYPTION_KEY.

    When the key is absent (dev mode), the token is stored as-is with a warning.
    Generate a valid key with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    enc_key = os.environ.get("SLACK_TOKEN_ENCRYPTION_KEY", "")
    if not enc_key:
        log.warning(
            "slack_hitl: SLACK_TOKEN_ENCRYPTION_KEY not set — "
            "storing Slack token in plain text (not suitable for production)"
        )
        return token
    try:
        from cryptography.fernet import Fernet
        return Fernet(enc_key.encode()).encrypt(token.encode()).decode()
    except Exception as exc:
        raise RuntimeError(
            f"Token encryption failed — check SLACK_TOKEN_ENCRYPTION_KEY format: {exc}"
        ) from exc


def _decrypt(token_enc: str) -> str:
    """Decrypt a stored Slack token. Falls back to returning the value as-is
    (handles plain-text dev-mode tokens or already-decrypted values)."""
    enc_key = os.environ.get("SLACK_TOKEN_ENCRYPTION_KEY", "")
    if not enc_key:
        return token_enc
    try:
        from cryptography.fernet import Fernet
        return Fernet(enc_key.encode()).decrypt(token_enc.encode()).decode()
    except Exception:
        return token_enc  # plain text fallback (dev mode or already decrypted)


# ---------------------------------------------------------------------------
# Block Kit
# ---------------------------------------------------------------------------

def _extract_excerpt(artifact_json: str) -> str:
    try:
        data = json.loads(artifact_json)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    title = data.get("title") or data.get("name") or ""
    tickets = data.get("tickets") or data.get("items") or []
    if tickets and isinstance(tickets, list) and isinstance(tickets[0], dict):
        first_title = tickets[0].get("title") or tickets[0].get("name") or ""
        if first_title:
            suffix = f" (+{len(tickets)-1} more)" if len(tickets) > 1 else ""
            return f"{first_title}{suffix}"
    if title:
        return title[:120]
    for v in data.values():
        if isinstance(v, str) and v.strip():
            return v.strip()[:120]
    return ""


def _build_review_blocks(
    agent_name: str,
    review_id: str,
    artifact_excerpt: str,
    options: list[str],
) -> list[dict]:
    """Build Block Kit layout with interactive buttons for each option."""
    elements: list[dict] = []
    for opt in options:
        btn: dict = {
            "type": "button",
            "text": {"type": "plain_text", "text": opt.capitalize()},
            "action_id": f"hitl_{opt}",
            "value": opt,
        }
        if opt == "approve":
            btn["style"] = "primary"
        elif opt == "reject":
            btn["style"] = "danger"
        elements.append(btn)

    base_url = os.environ.get("PLATFORM_BASE_URL", "").rstrip("/")
    review_url = f"{base_url}/reviews" if base_url else "/reviews"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"antcrew — {agent_name} needs review"},
        },
    ]
    if artifact_excerpt:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_{artifact_excerpt[:300]}_"},
        })
    blocks += [
        {
            "type": "actions",
            # review_id embedded in block_id so the Bolt handler can recover it
            "block_id": f"hitl_{review_id}",
            "elements": elements,
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"<{review_url}|Open review dashboard>"}],
        },
    ]
    return blocks


# ---------------------------------------------------------------------------
# Async send (called from listener.py)
# ---------------------------------------------------------------------------

async def send_hitl_to_slack(
    *,
    bot_token: str,
    channel_id: str,
    review_id: str,
    agent_name: str,
    artifact_json: str,
    options: list[str],
) -> None:
    """Send an interactive HITL review card to Slack via chat.postMessage.

    Requires slack-sdk. Falls through silently if the library is missing.
    """
    try:
        from slack_sdk.web.async_client import AsyncWebClient
    except ImportError:
        log.warning("slack_hitl: slack-sdk not installed — pip install 'antcrew[slack]'")
        return

    excerpt = _extract_excerpt(artifact_json)
    blocks = _build_review_blocks(agent_name, review_id, excerpt, options)
    client = AsyncWebClient(token=bot_token)
    try:
        await client.chat_postMessage(
            channel=channel_id,
            blocks=blocks,
            text=f"antcrew — {agent_name} needs review",
        )
        log.info("slack_hitl: sent interactive review %s to %s", review_id, channel_id)
    except Exception as exc:
        log.warning("slack_hitl: chat.postMessage failed: %s", exc)


# ---------------------------------------------------------------------------
# Sync DB resolution (called from the Bolt thread — not in asyncio context)
# ---------------------------------------------------------------------------

def _resolve_review_sync(
    review_id: str,
    decision: str,
    feedback: Optional[str] = None,
    edited: Optional[str] = None,
) -> None:
    """Update HitlReview status in the DB from the synchronous Bolt handler thread.

    When the platform's main loop is available (set via set_main_loop at startup),
    submits the coroutine to that loop via run_coroutine_threadsafe — reuses the
    existing engine connection pool instead of spinning up a fresh engine per click.
    Falls back to a new event loop when called outside the platform (e.g. tests).
    PlatformChannel._poll_db_for_decision picks up the change on the next poll.
    """
    from datetime import datetime, timezone

    async def _run() -> None:
        from app.core.database import engine as _engine
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.models.run import HitlReview

        status_map = {
            "approve": "approved",
            "reject": "rejected",
            "feedback": "feedback",
            "edit": "edited",
        }
        try:
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                row = (await session.exec(
                    select(HitlReview).where(HitlReview.review_id == review_id)
                )).first()
                if row and row.status == "pending":
                    row.status = status_map.get(decision, decision)
                    row.decision = decision
                    if feedback:
                        row.feedback = feedback
                    if edited:
                        row.edited_json = edited
                    row.resolved_at = datetime.now(timezone.utc)
                    session.add(row)
                    await session.commit()
                    log.info("slack_hitl: resolved review %s → %s", review_id, decision)
                elif row:
                    log.debug("slack_hitl: review %s already resolved (%s)", review_id, row.status)
        except Exception as exc:
            log.error("slack_hitl: DB resolution failed for %s: %s", review_id, exc)

    if _main_loop is not None and _main_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_run(), _main_loop)
        try:
            future.result(timeout=10)
        except Exception as exc:
            log.error("slack_hitl: resolution future failed for %s: %s", review_id, exc)
        return

    # Fallback: new event loop (outside platform context or tests)
    async def _run_isolated() -> None:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.models.run import HitlReview
        from datetime import datetime, timezone

        db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./platform.db")
        iso_engine = create_async_engine(db_url, echo=False)
        status_map = {"approve": "approved", "reject": "rejected", "feedback": "feedback", "edit": "edited"}
        try:
            async with AsyncSession(iso_engine, expire_on_commit=False) as session:
                row = (await session.exec(
                    select(HitlReview).where(HitlReview.review_id == review_id)
                )).first()
                if row and row.status == "pending":
                    row.status = status_map.get(decision, decision)
                    row.decision = decision
                    if feedback:
                        row.feedback = feedback
                    if edited:
                        row.edited_json = edited
                    row.resolved_at = datetime.now(timezone.utc)
                    session.add(row)
                    await session.commit()
                    log.info("slack_hitl: resolved review %s → %s (isolated loop)", review_id, decision)
        except Exception as exc:
            log.error("slack_hitl: DB resolution failed for %s: %s", review_id, exc)
        finally:
            await iso_engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_isolated())
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Socket Mode listener (started once at platform startup)
# ---------------------------------------------------------------------------

def start_slack_socket_mode(bot_token: str, app_token: str) -> None:
    """Start a background Bolt Socket Mode listener for a specific bot token.

    Safe to call multiple times — each unique bot token gets at most one handler
    (tracked in _handlers). Supports multi-org deployments where each workspace
    has its own Slack app.

    Required Slack app permissions:
      - Socket Mode enabled (App-Level Token with connections:write scope)
      - Bot Token scopes: chat:write, chat:write.public
      - Interactivity enabled (required for action handlers)
    """
    global _STARTED
    key = _token_key(bot_token)
    if _handlers.get(key):
        log.debug("slack_hitl: Socket Mode already running for token %s…", key[:8])
        return

    try:
        from slack_bolt import App as _BoltApp
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        log.warning(
            "slack_hitl: slack-bolt not installed — Socket Mode disabled. "
            "Install with: pip install 'antcrew[slack]'"
        )
        return

    bolt = _BoltApp(token=bot_token)

    @bolt.action(re.compile(r"^hitl_(approve|reject|feedback|edit)$"))
    def on_hitl_button(ack, body, action):
        ack()
        action_id: str = action.get("action_id", "")
        decision = action_id[5:]  # strip "hitl_"
        block_id: str = action.get("block_id", "")
        if not block_id.startswith("hitl_"):
            log.warning("slack_hitl: unexpected block_id=%r in action", block_id)
            return
        review_id = block_id[5:]  # strip "hitl_"
        log.info("slack_hitl: button click review=%s decision=%s", review_id, decision)
        _resolve_review_sync(review_id, decision)

    def _run() -> None:
        try:
            handler = SocketModeHandler(bolt, app_token)
            handler.start()
        except Exception as exc:
            log.error("slack_hitl: Socket Mode handler crashed: %s", exc)

    thread_name = f"antcrew-slack-bolt-{key[:8]}"
    t = threading.Thread(target=_run, daemon=True, name=thread_name)
    t.start()
    _handlers[key] = True
    _STARTED = True  # backward compat flag
    log.info("slack_hitl: Socket Mode listener started for token %s…", key[:8])


def maybe_start_from_env() -> None:
    """Start Socket Mode listener if SLACK_BOT_TOKEN + SLACK_APP_TOKEN are set.

    Called from app lifespan. No-op when tokens are missing or listener already running.
    """
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")
    if bot_token and app_token:
        start_slack_socket_mode(bot_token, app_token)
    else:
        log.debug(
            "slack_hitl: SLACK_BOT_TOKEN and/or SLACK_APP_TOKEN not set — "
            "interactive Slack HITL disabled"
        )
