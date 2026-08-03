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


def _wrap(content: str) -> str:
    """Wrap email content in a branded HTML shell compatible with most email clients."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>antcrew</title>
</head>
<body style="margin:0;padding:0;background:#07070f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#07070f;min-height:100vh">
  <tr><td align="center" style="padding:40px 16px">
    <table width="100%" style="max-width:520px;background:#0d0d1a;border:1px solid #1e1e3a;border-radius:16px;overflow:hidden">
      <!-- Header -->
      <tr>
        <td style="padding:28px 32px 20px;border-bottom:1px solid #1e1e3a">
          <span style="font-family:'SF Mono','Cascadia Code','Fira Code',monospace;font-size:13px;font-weight:700;letter-spacing:0.15em;color:#818CF8;text-transform:uppercase">antcrew</span>
        </td>
      </tr>
      <!-- Body -->
      <tr>
        <td style="padding:28px 32px 24px;color:#c7c7d9;font-size:14px;line-height:1.7">
          {content}
        </td>
      </tr>
      <!-- Footer -->
      <tr>
        <td style="padding:16px 32px 24px;border-top:1px solid #1e1e3a">
          <p style="margin:0;font-size:11px;color:#3a3a5c">antcrew platform &middot; Si no esperabas este correo, puedes ignorarlo.</p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def _btn(label: str, url: str, color: str = "#6366f1") -> str:
    return (
        f'<a href="{url}" style="display:inline-block;padding:11px 22px;'
        f'background:{color};color:#fff;text-decoration:none;border-radius:8px;'
        f'font-weight:600;font-size:13px;letter-spacing:.01em">{label}</a>'
    )


async def send_verification_code(to_email: str, code: str) -> None:
    """Send a 6-digit email verification code."""
    subject = "Tu código de verificación — antcrew"
    content = f"""
<p style="margin:0 0 20px;font-size:15px;font-weight:600;color:#e4e4f0">Verifica tu dirección de correo</p>
<p style="margin:0 0 24px;color:#8888a8">Introduce este código en la pantalla de verificación. Expira en <strong style="color:#c7c7d9">15 minutos</strong>.</p>
<div style="text-align:center;margin:0 0 28px">
  <span style="display:inline-block;padding:18px 32px;background:#0a0a18;border:1px solid #2a2a4a;border-radius:12px;font-family:'SF Mono','Cascadia Code',monospace;font-size:36px;font-weight:700;letter-spacing:14px;color:#818CF8">{_e(code)}</span>
</div>
<p style="margin:0;font-size:12px;color:#4a4a6a">Si no creaste una cuenta en antcrew, puedes ignorar este correo.</p>
"""
    await _dispatch(to_email, subject, _wrap(content))


async def send_review_assigned(
    to_email: str,
    assignee_label: str,
    review_id: str,
    agent_name: str,
    run_id: str,
    base_url: str = "",
) -> None:
    """Fire-and-forget: notify an assignee that a new HITL review awaits them."""
    subject = f"Nueva revisión HITL — {_e(agent_name)} · antcrew"
    review_url = f"{base_url}/reviews#{_e(review_id)}"
    content = f"""
<p style="margin:0 0 16px;font-size:15px;font-weight:600;color:#e4e4f0">Tienes una revisión pendiente</p>
<p style="margin:0 0 20px">Hola <strong style="color:#e4e4f0">{_e(assignee_label)}</strong>, se ha asignado una revisión HITL que requiere tu atención.</p>
<table cellpadding="0" cellspacing="0" style="width:100%;background:#0a0a18;border:1px solid #1e1e3a;border-radius:10px;margin:0 0 24px">
  <tr><td style="padding:14px 18px;border-bottom:1px solid #1e1e3a">
    <span style="font-size:11px;color:#5a5a7a;text-transform:uppercase;letter-spacing:.06em">Agente</span>
    <p style="margin:4px 0 0;color:#c7c7d9;font-weight:500">{_e(agent_name)}</p>
  </td></tr>
  <tr><td style="padding:14px 18px">
    <span style="font-size:11px;color:#5a5a7a;text-transform:uppercase;letter-spacing:.06em">Run ID</span>
    <p style="margin:4px 0 0;font-family:monospace;color:#818CF8;font-size:13px">{_e(run_id[:8])}</p>
  </td></tr>
</table>
{_btn("Ver revisión", review_url)}
"""
    await _dispatch(to_email, subject, _wrap(content))


async def send_workspace_invite(
    to_email: str,
    workspace_name: str,
    inviter_email: str,
    role: str,
    invite_url: str,
) -> None:
    """Send a workspace invitation email with an accept button."""
    subject = f"Invitación a {_e(workspace_name)} — antcrew"
    role_color = {"admin": "#ef4444", "write": "#3b82f6", "reviewer": "#f59e0b", "read": "#6b7280"}.get(role, "#818CF8")
    content = f"""
<p style="margin:0 0 16px;font-size:15px;font-weight:600;color:#e4e4f0">Te han invitado a un workspace</p>
<p style="margin:0 0 20px"><strong style="color:#e4e4f0">{_e(inviter_email)}</strong> te ha invitado a unirte a <strong style="color:#e4e4f0">{_e(workspace_name)}</strong>.</p>
<p style="margin:0 0 24px">Tu rol: <span style="display:inline-block;padding:2px 10px;background:{role_color}22;color:{role_color};border-radius:99px;font-size:12px;font-weight:600;font-family:monospace">{_e(role)}</span></p>
{_btn("Aceptar invitación", _e(invite_url))}
<p style="margin:16px 0 0;font-size:12px;color:#4a4a6a">Este enlace expira en 7 días.</p>
"""
    await _dispatch(to_email, subject, _wrap(content))


async def send_join_request(
    to_email: str,
    requester_email: str,
    workspace_name: str,
    requested_role: str,
    approve_url: str,
    reject_url: str,
) -> None:
    """Notify a workspace admin of a new join request with approve/reject buttons."""
    subject = f"Solicitud de acceso a {_e(workspace_name)} — antcrew"
    content = f"""
<p style="margin:0 0 16px;font-size:15px;font-weight:600;color:#e4e4f0">Nueva solicitud de acceso</p>
<p style="margin:0 0 20px"><strong style="color:#e4e4f0">{_e(requester_email)}</strong> quiere unirse a <strong style="color:#e4e4f0">{_e(workspace_name)}</strong> con rol <span style="font-family:monospace;color:#818CF8">{_e(requested_role)}</span>.</p>
<table cellpadding="0" cellspacing="0"><tr>
  <td style="padding-right:10px">{_btn("Aprobar", _e(approve_url), "#16a34a")}</td>
  <td>{_btn("Rechazar", _e(reject_url), "#dc2626")}</td>
</tr></table>
"""
    await _dispatch(to_email, subject, _wrap(content))


async def send_join_approved(to_email: str, workspace_name: str, base_url: str) -> None:
    """Notify the requester that their join request was approved."""
    subject = f"Acceso aprobado a {_e(workspace_name)} — antcrew"
    dashboard_url = f"{base_url}/dashboard"
    content = f"""
<p style="margin:0 0 16px;font-size:15px;font-weight:600;color:#e4e4f0">Tu solicitud ha sido aprobada</p>
<p style="margin:0 0 24px">Ya tienes acceso al workspace <strong style="color:#e4e4f0">{_e(workspace_name)}</strong>. Puedes empezar ahora.</p>
{_btn("Ir al dashboard", _e(dashboard_url))}
"""
    await _dispatch(to_email, subject, _wrap(content))


async def send_join_rejected(to_email: str, workspace_name: str) -> None:
    """Notify the requester that their join request was rejected."""
    subject = f"Solicitud rechazada — {_e(workspace_name)} · antcrew"
    content = f"""
<p style="margin:0 0 16px;font-size:15px;font-weight:600;color:#e4e4f0">Solicitud no aprobada</p>
<p style="margin:0 0 16px">Tu solicitud de acceso al workspace <strong style="color:#e4e4f0">{_e(workspace_name)}</strong> no ha sido aprobada.</p>
<p style="margin:0;font-size:12px;color:#5a5a7a">Si crees que es un error, contacta con el administrador del workspace.</p>
"""
    await _dispatch(to_email, subject, _wrap(content))
