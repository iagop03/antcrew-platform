"""Telegram Bot notifications for HITL review assignments.

Configuration via environment variables:
  TELEGRAM_BOT_TOKEN  — Bot token from @BotFather (required to enable)

Per-reviewer chat ID is stored in ApiKey.telegram_chat_id.
Reviewers must start a conversation with the bot first and share their chat ID
(or use /start to get it — the bot can echo it back).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_TOKEN: Optional[str] = os.environ.get("TELEGRAM_BOT_TOKEN") or None
_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


async def send_review_assigned(
    chat_id: str,
    review_id: str,
    agent_name: str,
    run_id: str,
    base_url: str = "",
) -> None:
    """Fire-and-forget: send a Telegram message when a HITL review requires attention."""
    if not _TOKEN:
        return
    review_url = f"{base_url}/reviews#{review_id}"
    text = (
        f"*antcrew* — nueva revisión HITL\n\n"
        f"*Agente:* {agent_name}\n"
        f"*Run:* `{run_id[:8]}`\n\n"
        f"[Ver revisión]({review_url})"
    )
    try:
        import urllib.request
        import json as _json

        url = _API_BASE.format(token=_TOKEN)
        payload = _json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }).encode()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: urllib.request.urlopen(
                urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                ),
                timeout=10,
            ),
        )
        log.debug("telegram: sent review notification to chat %s", chat_id)
    except Exception as exc:
        log.warning("telegram: failed to notify chat %s for review %s: %s", chat_id, review_id, exc)
