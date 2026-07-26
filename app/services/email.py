"""Async SMTP email notifications.

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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as _e
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


async def _dispatch(to_email: str, subject: str, body: str) -> None:
    if not _HOST:
        return
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _send_sync, to_email, subject, body)
        log.debug("email: sent %r to %s", subject, to_email)
    except Exception as exc:
        log.warning("email: failed to send %r to %s: %s", subject, to_email, exc)


async def send_review_assigned(
    to_email: str,
    assignee_label: str,
    review_id: str,
    agent_name: str,
    run_id: str,
    base_url: str = "",
) -> None:
    """Fire-and-forget: notify an assignee that a new HITL review awaits them."""
    subject = f"[antcrew] Nueva revisión HITL asignada — {_e(agent_name)}"
    review_url = f"{base_url}/reviews#{_e(review_id)}"
    body = f"""
<p>Hola <strong>{_e(assignee_label)}</strong>,</p>
<p>Se ha creado una revisión HITL que requiere tu atención:</p>
<ul>
  <li><strong>Agente:</strong> {_e(agent_name)}</li>
  <li><strong>Run:</strong> {_e(run_id)}</li>
</ul>
<p><a href="{review_url}">Ver revisión</a></p>
<p style="color:#888;font-size:12px">antcrew platform</p>
"""
    await _dispatch(to_email, subject, body)


async def send_verification_code(to_email: str, code: str) -> None:
    """Send a 6-digit email verification code."""
    subject = "[antcrew] Verifica tu dirección de correo"
    body = f"""
<p>Tu código de verificación es:</p>
<p style="font-size:32px;font-weight:bold;letter-spacing:8px;color:#1a1a1a">{_e(code)}</p>
<p style="color:#666">Este código expira en <strong>15 minutos</strong>.</p>
<p style="color:#888;font-size:12px">Si no creaste una cuenta en antcrew, ignora este correo.</p>
"""
    await _dispatch(to_email, subject, body)


async def send_workspace_invite(
    to_email: str,
    workspace_name: str,
    inviter_email: str,
    role: str,
    invite_url: str,
) -> None:
    """Send a workspace invitation email with an accept button."""
    subject = f"[antcrew] Invitación al workspace {_e(workspace_name)}"
    body = f"""
<p><strong>{_e(inviter_email)}</strong> te ha invitado a unirte al workspace
<strong>{_e(workspace_name)}</strong> con el rol <strong>{_e(role)}</strong>.</p>
<p>
  <a href="{_e(invite_url)}"
     style="display:inline-block;padding:12px 24px;background:#0f172a;color:#fff;
            text-decoration:none;border-radius:6px;font-weight:600">
    Aceptar invitación
  </a>
</p>
<p style="color:#888;font-size:12px">
  Este enlace expira en 7 días. Si no esperabas esta invitación, ignora este correo.
</p>
"""
    await _dispatch(to_email, subject, body)


async def send_join_request(
    to_email: str,
    requester_email: str,
    workspace_name: str,
    requested_role: str,
    approve_url: str,
    reject_url: str,
) -> None:
    """Notify a workspace admin of a new join request with approve/reject buttons."""
    subject = f"[antcrew] Solicitud de acceso a {_e(workspace_name)}"
    body = f"""
<p><strong>{_e(requester_email)}</strong> ha solicitado acceso al workspace
<strong>{_e(workspace_name)}</strong> con el rol <strong>{_e(requested_role)}</strong>.</p>
<p>
  <a href="{_e(approve_url)}"
     style="display:inline-block;padding:12px 24px;background:#16a34a;color:#fff;
            text-decoration:none;border-radius:6px;font-weight:600;margin-right:12px">
    Aprobar
  </a>
  <a href="{_e(reject_url)}"
     style="display:inline-block;padding:12px 24px;background:#dc2626;color:#fff;
            text-decoration:none;border-radius:6px;font-weight:600">
    Rechazar
  </a>
</p>
<p style="color:#888;font-size:12px">antcrew platform</p>
"""
    await _dispatch(to_email, subject, body)


async def send_join_approved(to_email: str, workspace_name: str, base_url: str) -> None:
    """Notify the requester that their join request was approved."""
    subject = f"[antcrew] Acceso aprobado a {_e(workspace_name)}"
    dashboard_url = f"{base_url}/dashboard"
    body = f"""
<p>Tu solicitud de acceso al workspace <strong>{_e(workspace_name)}</strong>
ha sido aprobada.</p>
<p>
  <a href="{_e(dashboard_url)}"
     style="display:inline-block;padding:12px 24px;background:#0f172a;color:#fff;
            text-decoration:none;border-radius:6px;font-weight:600">
    Ir al dashboard
  </a>
</p>
<p style="color:#888;font-size:12px">antcrew platform</p>
"""
    await _dispatch(to_email, subject, body)


async def send_join_rejected(to_email: str, workspace_name: str) -> None:
    """Notify the requester that their join request was rejected."""
    subject = f"[antcrew] Solicitud rechazada — {_e(workspace_name)}"
    body = f"""
<p>Tu solicitud de acceso al workspace <strong>{_e(workspace_name)}</strong>
ha sido rechazada por el administrador.</p>
<p style="color:#888;font-size:12px">
  Si crees que esto es un error, contacta con el administrador del workspace.
</p>
<p style="color:#888;font-size:12px">antcrew platform</p>
"""
    await _dispatch(to_email, subject, body)
