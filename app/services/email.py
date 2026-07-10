"""Async SMTP email notifications for HITL review assignments.

Configuration via environment variables:
  SMTP_HOST      — SMTP server hostname (required to enable)
  SMTP_PORT      — port (default: 587)
  SMTP_USER      — login username
  SMTP_PASSWORD  — login password
  SMTP_FROM      — From address (defaults to SMTP_USER)
  SMTP_TLS       — "true" (default) to use STARTTLS, "ssl" for SMTPS, "none" to skip
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

log = logging.getLogger(__name__)

_HOST: Optional[str] = os.environ.get("SMTP_HOST") or None
_PORT: int = int(os.environ.get("SMTP_PORT", "587"))
_USER: Optional[str] = os.environ.get("SMTP_USER") or None
_PASSWORD: Optional[str] = os.environ.get("SMTP_PASSWORD") or None
_FROM: str = os.environ.get("SMTP_FROM") or _USER or "noreply@antcrew"
_TLS: str = os.environ.get("SMTP_TLS", "true").lower()


def _send_sync(to: str, subject: str, body_html: str) -> None:
    assert _HOST
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _FROM
    msg["To"] = to
    msg.attach(MIMEText(body_html, "html"))

    if _TLS == "ssl":
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(_HOST, _PORT, context=ctx) as smtp:
            if _USER and _PASSWORD:
                smtp.login(_USER, _PASSWORD)
            smtp.sendmail(_FROM, [to], msg.as_string())
    else:
        with smtplib.SMTP(_HOST, _PORT) as smtp:
            if _TLS != "none":
                smtp.starttls(context=ssl.create_default_context())
            if _USER and _PASSWORD:
                smtp.login(_USER, _PASSWORD)
            smtp.sendmail(_FROM, [to], msg.as_string())


async def send_review_assigned(
    to_email: str,
    assignee_label: str,
    review_id: str,
    agent_name: str,
    run_id: str,
    base_url: str = "",
) -> None:
    """Fire-and-forget: notify an assignee that a new HITL review awaits them."""
    if not _HOST:
        return
    subject = f"[antcrew] Nueva revisión HITL asignada — {agent_name}"
    review_url = f"{base_url}/reviews#{review_id}"
    body = f"""
<p>Hola <strong>{assignee_label}</strong>,</p>
<p>Se ha creado una revisión HITL que requiere tu atención:</p>
<ul>
  <li><strong>Agente:</strong> {agent_name}</li>
  <li><strong>Run:</strong> {run_id}</li>
</ul>
<p><a href="{review_url}">Ver revisión</a></p>
<p style="color:#888;font-size:12px">antcrew platform</p>
"""
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _send_sync, to_email, subject, body)
        log.debug("email: sent review notification to %s", to_email)
    except Exception as exc:
        log.warning("email: failed to notify %s for review %s: %s", to_email, review_id, exc)
