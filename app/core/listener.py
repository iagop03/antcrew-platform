"""Antcrew event bus listener — persists every event to the DB.

Subscribe once at app startup. Every pipeline.start/end, agent.start/end,
hitl.review_required, etc. is written to the events table and used to update
Run/HitlReview rows in real time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from antcrew.core.events import bus
from app.core.database import engine
from app.models.run import Run, Event as DBEvent, HitlReview, WebhookDelivery, Workspace, WebhookConfig, WebhookEvent
from app.services.webhook import notify_new_delivery

if TYPE_CHECKING:
    from antcrew.core.events import Event

log = logging.getLogger(__name__)

_REQUEST_MAX_LEN = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sync_handler(event: "Event") -> None:
    """Fire-and-forget: schedule DB write on the running event loop."""
    try:
        loop = asyncio.get_running_loop()
        asyncio.ensure_future(_persist_event(event), loop=loop)
    except RuntimeError:
        pass  # no running event loop in sync test context


async def _persist_event(event: "Event") -> None:
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            db_event = DBEvent(
                run_id=event.run_id,
                thread_id=event.thread_id,
                event_type=event.type,
                payload=dict(event.payload),
                timestamp=event.timestamp,
            )
            session.add(db_event)

            if event.type == "pipeline.start" and event.run_id:
                stmt = select(Run).where(Run.run_id == event.run_id)
                existing = (await session.exec(stmt)).first()
                if not existing:
                    session.add(Run(
                        run_id=event.run_id,
                        thread_id=event.thread_id or "default",
                        team=event.payload.get("team", "unknown"),
                        request=event.payload.get("request", "")[:_REQUEST_MAX_LEN],
                        status="running",
                    ))

            elif event.type == "pipeline.end" and event.run_id:
                stmt = select(Run).where(Run.run_id == event.run_id)
                run = (await session.exec(stmt)).first()
                if run and run.status != "cancelled":
                    # Don't overwrite a cancelled run — cancel_run already set the final status
                    run.status = "success" if event.payload.get("success") else "error"
                    raw_cost_usd = event.payload.get("cost_usd", 0.0)
                    run.finished_at = _utcnow()
                    if run.created_at:
                        ca = run.created_at.replace(tzinfo=None) if run.created_at.tzinfo else run.created_at
                        run.duration_s = (run.finished_at - ca).total_seconds()

                    # Fetch workspace once — used for both billing multiplier and Stripe reporting
                    ws_row = None
                    if run.workspace_id is not None:
                        ws_row = (await session.exec(
                            select(Workspace).where(Workspace.id == run.workspace_id)
                        )).first()

                    # Apply billing multiplier: trial ×1.0, byok ×0.4, managed ×3.0
                    if ws_row and raw_cost_usd > 0:
                        from app.core.byok import get_cost_multiplier
                        multiplier = get_cost_multiplier(
                            getattr(ws_row, "llm_key_mode", "managed"),
                            is_trial=getattr(ws_row, "is_trial", False),
                        )
                        run.cost_usd = round(raw_cost_usd * multiplier, 6)
                    else:
                        run.cost_usd = raw_cost_usd

                    session.add(run)

                    pipeline_payload = json.dumps({
                        "run_id": event.run_id,
                        "status": run.status,
                        "cost_usd": run.cost_usd,
                        "team": run.team,
                    })

                    # Global fallback webhook (env var)
                    webhook_url = os.environ.get("WEBHOOK_URL")
                    if webhook_url:
                        session.add(WebhookDelivery(
                            run_id=event.run_id,
                            url=webhook_url,
                            payload_json=pipeline_payload,
                        ))
                        notify_new_delivery()

                    # Report metered usage to Stripe (fire-and-forget, Stripe workspaces only)
                    if (ws_row and ws_row.stripe_customer_id and run.cost_usd > 0
                            and getattr(ws_row, "billing_provider", "mor") == "stripe"):
                        from app.services import billing as _billing
                        asyncio.ensure_future(
                            _billing.report_run_cost(
                                run.workspace_id,
                                ws_row.stripe_customer_id,
                                run.cost_usd,
                            )
                        )

                    # Per-workspace registered webhooks — filtered in SQL via webhook_event join
                    if run.workspace_id is not None:
                        from sqlmodel import col as _col
                        hookable = (await session.exec(
                            select(WebhookConfig)
                            .join(WebhookEvent, WebhookEvent.webhook_id == WebhookConfig.id)
                            .where(WebhookConfig.workspace_id == run.workspace_id)
                            .where(WebhookConfig.enabled == True)  # noqa: E712
                            .where(_col(WebhookEvent.event_type).in_(["pipeline.end", "*"]))
                            .distinct()
                        )).all()
                        for wh in hookable:
                            session.add(WebhookDelivery(
                                run_id=event.run_id,
                                url=wh.url,
                                payload_json=pipeline_payload,
                            ))
                        if hookable:
                            notify_new_delivery()

            elif event.type == "hitl.review_required" and event.run_id:
                review_id = event.payload.get("review_id")
                artifact = event.payload.get("artifact", {})
                if review_id:
                    stmt = select(HitlReview).where(HitlReview.review_id == review_id)
                    if not (await session.exec(stmt)).first():
                        session.add(HitlReview(
                            review_id=review_id,
                            run_id=event.run_id,
                            agent_name=event.payload.get("agent_name", ""),
                            artifact_json=json.dumps(artifact),
                            options_json=json.dumps(event.payload.get("options", ["approve", "reject"])),
                            status="pending",
                        ))

                    # Determine notification strategy — check workspace config first.
                    hitl_webhook: Optional[str] = None
                    ws_slack_channel: Optional[str] = None
                    ws_for_hitl = None
                    run_for_ws = (await session.exec(
                        select(Run).where(Run.run_id == event.run_id)
                    )).first()
                    if run_for_ws and run_for_ws.workspace_id is not None:
                        ws_for_hitl = (await session.exec(
                            select(Workspace).where(Workspace.id == run_for_ws.workspace_id)
                        )).first()
                        if ws_for_hitl:
                            if ws_for_hitl.slack_webhook_url:
                                hitl_webhook = ws_for_hitl.slack_webhook_url
                            if ws_for_hitl.slack_channel_id:
                                ws_slack_channel = ws_for_hitl.slack_channel_id

                    if not hitl_webhook:
                        hitl_webhook = os.environ.get("HITL_WEBHOOK_URL")

                    # Interactive Slack (Socket Mode).
                    # Resolution order: per-workspace encrypted tokens → global env vars.
                    from app.core.slack_hitl import (
                        _decrypt as _dec,
                        send_hitl_to_slack as _slack_send,
                        start_slack_socket_mode as _start_sm,
                    )
                    effective_bot = ""
                    effective_app = ""
                    if ws_for_hitl:
                        if ws_for_hitl.slack_bot_token_enc:
                            effective_bot = _dec(ws_for_hitl.slack_bot_token_enc)
                        if ws_for_hitl.slack_app_token_enc:
                            effective_app = _dec(ws_for_hitl.slack_app_token_enc)
                    if not effective_bot:
                        effective_bot = os.environ.get("SLACK_BOT_TOKEN", "")
                    if not effective_app:
                        effective_app = os.environ.get("SLACK_APP_TOKEN", "")
                    effective_channel = ws_slack_channel or os.environ.get("SLACK_CHANNEL_ID", "")

                    if review_id and effective_bot and effective_channel:
                        # Lazily start Socket Mode for per-workspace tokens on first review.
                        if effective_app:
                            _start_sm(effective_bot, effective_app)
                        artifact_json_str = json.dumps(artifact)
                        options_list = event.payload.get("options", ["approve", "reject"])
                        asyncio.ensure_future(
                            _slack_send(
                                bot_token=effective_bot,
                                channel_id=effective_channel,
                                review_id=review_id,
                                agent_name=event.payload.get("agent_name", ""),
                                artifact_json=artifact_json_str,
                                options=options_list,
                            )
                        )

                    # Webhook notification (incoming webhook or HITL_WEBHOOK_URL env).
                    if hitl_webhook and review_id:
                        session.add(WebhookDelivery(
                            run_id=event.run_id,
                            url=hitl_webhook,
                            payload_json=_build_hitl_payload(
                                hitl_webhook,
                                review_id=review_id,
                                run_id=event.run_id,
                                agent_name=event.payload.get("agent_name", ""),
                                artifact=artifact,
                                options=event.payload.get("options", ["approve", "reject"]),
                            ),
                        ))
                        notify_new_delivery()

            await session.commit()
    except Exception as exc:
        log.warning("platform listener: DB write failed: %s", exc)


def _extract_artifact_excerpt(artifact: dict) -> str:
    """Return a short human-readable summary extracted from an artifact dict."""
    if not artifact or not isinstance(artifact, dict):
        return ""
    # PRD / ticket list: prefer title field, then first ticket title
    title = artifact.get("title") or artifact.get("name") or ""
    tickets = artifact.get("tickets") or artifact.get("items") or []
    if tickets and isinstance(tickets, list):
        first = tickets[0]
        if isinstance(first, dict):
            ticket_title = first.get("title") or first.get("name") or ""
            if ticket_title:
                count = len(tickets)
                suffix = f" (+{count - 1} more)" if count > 1 else ""
                return f"{ticket_title}{suffix}"
    if title:
        return title[:120]
    # Fallback: any string value in the top level
    for v in artifact.values():
        if isinstance(v, str) and v.strip():
            return v.strip()[:120]
    return ""


def _build_hitl_payload(
    url: str,
    *,
    review_id: str,
    run_id: str,
    agent_name: str,
    artifact: dict,
    options: list,
) -> str:
    """Return a JSON payload for a HITL webhook delivery.

    When the URL looks like a Slack incoming webhook (contains 'hooks.slack.com')
    the payload is formatted as Slack Block Kit for immediate readability.
    Otherwise a plain JSON object is returned.
    """
    base = os.environ.get("PLATFORM_BASE_URL", "").rstrip("/")
    review_url = f"{base}/reviews" if base else "/reviews"
    excerpt = _extract_artifact_excerpt(artifact)

    if "hooks.slack.com" in url:
        options_text = " · ".join(f"`{o}`" for o in options)
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "antcrew — Human Review Required"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Agent:*\n{agent_name}"},
                    {"type": "mrkdwn", "text": f"*Run:*\n`{run_id[:16]}…`"},
                ],
            },
        ]
        if excerpt:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Artifact:*\n{excerpt}"},
            })
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Options:* {options_text}\n<{review_url}|Open review dashboard →>",
            },
        })
        blocks.append({"type": "divider"})
        return json.dumps({
            "blocks": blocks,
            "text": f"[antcrew] {agent_name} needs review — {review_url}",
        })

    return json.dumps({
        "event": "hitl.review_required",
        "review_id": review_id,
        "run_id": run_id,
        "agent_name": agent_name,
        "artifact_excerpt": excerpt,
        "options": options,
        "review_url": review_url,
    })


def start_listening() -> None:
    """Subscribe the platform listener to the global antcrew bus."""
    bus.subscribe("*", _sync_handler)
    log.info("antcrew-platform: listening to antcrew event bus")


def stop_listening() -> None:
    bus.unsubscribe("*", _sync_handler)
