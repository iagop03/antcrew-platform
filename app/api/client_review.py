"""Public client review endpoint — no API key required.

A tokenized URL allows end-clients (consultancy customers) to approve or reject
a HITL checkpoint without a GitHub account or platform API key.

Flow:
  1. When a HitlReview is created (via listener.py or reviews.py), a client_token
     is generated and stored alongside the review.
  2. The operator shares GET /r/{token} with the end-client.
  3. The client sees a plain page with the request summary + artifact context
     and clicks Approve or Reject (with optional feedback).
  4. POST /r/{token}/decision resolves the HitlReview row, unblocking the pipeline.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.channel import resolve_review
from app.core.database import get_session
from app.models.run import HitlReview, HitlAuditEntry, Run

router = APIRouter(prefix="/r", tags=["client-review"])


def _render_artifact(artifact: object, agent_name: str) -> str:
    """Return a readable HTML snippet for the artifact."""
    if not artifact or artifact == {}:
        return "<p>No artifact details available for this review.</p>"

    lines: list[str] = []

    if isinstance(artifact, dict):
        # PRD
        if "title" in artifact and "summary" in artifact:
            lines.append(f"<h3>{artifact['title']}</h3>")
            lines.append(f"<p>{artifact['summary']}</p>")
        # Code artifact (single)
        elif "file_path" in artifact:
            lines.append(f"<p><strong>File:</strong> <code>{artifact['file_path']}</code></p>")
            if artifact.get("description"):
                lines.append(f"<p>{artifact['description']}</p>")
        # Code review
        elif "verdict" in artifact:
            verdict = artifact.get("verdict", "")
            color = "#2d6a4f" if verdict == "approve" else "#c62828"
            lines.append(f"<p><strong>Verdict:</strong> <span style='color:{color};font-weight:bold'>{verdict.upper()}</span></p>")
            if artifact.get("comment"):
                lines.append(f"<blockquote>{artifact['comment']}</blockquote>")
        else:
            for k, v in list(artifact.items())[:6]:
                if v and k not in ("_type",):
                    label = k.replace("_", " ").title()
                    lines.append(f"<p><strong>{label}:</strong> {v}</p>")

    elif isinstance(artifact, list):
        if artifact and isinstance(artifact[0], dict):
            if "file_path" in artifact[0]:
                lines.append(f"<p><strong>{len(artifact)} file(s) generated:</strong></p><ul>")
                for item in artifact[:10]:
                    fp = item.get("file_path", "")
                    desc = item.get("description", "")
                    lines.append(f"<li><code>{fp}</code>{' — ' + desc if desc else ''}</li>")
                lines.append("</ul>")
            elif "id" in artifact[0] and "title" in artifact[0]:
                lines.append(f"<p><strong>{len(artifact)} ticket(s):</strong></p><ul>")
                for item in artifact[:10]:
                    lines.append(f"<li><strong>{item.get('id','')}</strong> {item.get('title','')}</li>")
                lines.append("</ul>")
        else:
            lines.append(f"<p>{len(artifact)} item(s) to review.</p>")

    return "\n".join(lines) if lines else "<p>Review the pipeline output and submit your decision.</p>"


def _render_page(
    review: HitlReview,
    run: Optional[Run],
    token: str,
    *,
    done: bool = False,
    done_decision: str = "",
) -> str:
    artifact_obj: object = {}
    try:
        artifact_obj = json.loads(review.artifact_json or "null") or {}
    except Exception:
        pass

    options: list[str] = ["approve", "reject"]
    try:
        options = json.loads(review.options_json or '["approve","reject"]')
    except Exception:
        pass

    client_options = [o for o in options if o in ("approve", "reject")]
    if not client_options:
        client_options = ["approve", "reject"]

    agent_label = review.agent_name.replace("_", " ").title()
    request_text = run.request[:400] if run else ""

    artifact_html = _render_artifact(artifact_obj, review.agent_name)

    if done:
        color = "#2d6a4f" if done_decision == "approve" else "#c62828"
        label = "Approved" if done_decision == "approve" else "Rejected"
        status_block = f"""
        <div class="status-done" style="border-left:4px solid {color};padding:16px 20px;background:#f8f9fa;border-radius:4px;margin:24px 0">
          <p style="margin:0;font-size:1.1em;color:{color};font-weight:600">✓ Decision recorded: {label}</p>
          <p style="margin:8px 0 0;color:#555">The pipeline will continue with your decision. You may close this tab.</p>
        </div>"""
        action_block = ""
    else:
        buttons = ""
        for opt in client_options:
            if opt == "approve":
                buttons += '<button onclick="submit(\'approve\')" class="btn btn-approve">Approve</button>\n'
            elif opt == "reject":
                buttons += '<button onclick="submit(\'reject\')" class="btn btn-reject">Reject</button>\n'

        action_block = f"""
        <div class="feedback-section">
          <label for="feedback">Feedback (optional):</label>
          <textarea id="feedback" placeholder="Add comments for the development team..." rows="3"></textarea>
        </div>
        <div class="button-row">{buttons}</div>
        <p id="msg" class="msg"></p>"""
        status_block = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Review Request — antcrew</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f5f5f5; color: #1a1a1a; min-height: 100vh;
         display: flex; flex-direction: column; align-items: center; padding: 40px 16px; }}
  .card {{ background: #fff; border-radius: 10px; box-shadow: 0 2px 16px rgba(0,0,0,.09);
           max-width: 640px; width: 100%; padding: 40px; }}
  .logo {{ font-size: .8em; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
           color: #888; margin-bottom: 32px; }}
  h1 {{ font-size: 1.35em; font-weight: 700; margin-bottom: 6px; }}
  .subtitle {{ color: #666; font-size: .93em; margin-bottom: 28px; }}
  .section {{ margin-bottom: 24px; }}
  .section h2 {{ font-size: .78em; font-weight: 700; text-transform: uppercase;
                 letter-spacing: .08em; color: #888; margin-bottom: 10px; }}
  .request-box {{ background: #f8f9fa; border-left: 3px solid #d0d0d0; padding: 12px 16px;
                  border-radius: 4px; font-size: .93em; line-height: 1.5; color: #333; }}
  .artifact-box {{ background: #f8f9fa; padding: 16px; border-radius: 6px; font-size: .92em; line-height: 1.6; }}
  .artifact-box h3 {{ font-size: 1em; font-weight: 700; margin-bottom: 8px; }}
  .artifact-box code {{ background: #e8e8e8; padding: 1px 5px; border-radius: 3px; font-size: .88em; }}
  .artifact-box ul {{ padding-left: 20px; margin: 8px 0; }}
  .artifact-box blockquote {{ border-left: 3px solid #ccc; padding-left: 12px; color: #555; font-style: italic; }}
  .feedback-section label {{ display: block; font-size: .86em; font-weight: 600; margin-bottom: 6px; color: #555; }}
  textarea {{ width: 100%; padding: 10px 12px; border: 1px solid #d0d0d0; border-radius: 6px;
              font-size: .93em; font-family: inherit; resize: vertical; }}
  textarea:focus {{ outline: none; border-color: #666; }}
  .button-row {{ display: flex; gap: 12px; margin-top: 20px; flex-wrap: wrap; }}
  .btn {{ padding: 12px 28px; border: none; border-radius: 6px; font-size: .95em;
          font-weight: 600; cursor: pointer; transition: opacity .15s; }}
  .btn:hover {{ opacity: .85; }}
  .btn:disabled {{ opacity: .5; cursor: not-allowed; }}
  .btn-approve {{ background: #2d6a4f; color: #fff; }}
  .btn-reject  {{ background: #c62828; color: #fff; }}
  .msg {{ margin-top: 14px; font-size: .9em; color: #555; min-height: 1.2em; }}
  .divider {{ border: none; border-top: 1px solid #eee; margin: 24px 0; }}
  footer {{ margin-top: 40px; font-size: .8em; color: #aaa; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #111; color: #eee; }}
    .card {{ background: #1e1e1e; box-shadow: 0 2px 16px rgba(0,0,0,.4); }}
    .request-box, .artifact-box {{ background: #2a2a2a; color: #ddd; }}
    .artifact-box code {{ background: #333; }}
    textarea {{ background: #2a2a2a; border-color: #444; color: #eee; }}
    footer {{ color: #555; }}
  }}
</style>
</head>
<body>
<div class="card">
  <div class="logo">antcrew</div>
  <h1>Pipeline Review Request</h1>
  <p class="subtitle">Agent <strong>{agent_label}</strong> has completed its step and needs your approval to continue.</p>
  <hr class="divider">
  {status_block}
  <div class="section">
    <h2>Original Request</h2>
    <div class="request-box">{request_text or '(no request context available)'}</div>
  </div>
  <div class="section">
    <h2>Output to Review</h2>
    <div class="artifact-box">{artifact_html}</div>
  </div>
  {'<hr class="divider">' + action_block if not done else ''}
</div>
<footer>Powered by antcrew · <a href="https://antcrew-int.fly.dev" style="color:inherit">antcrew platform</a></footer>
<script>
async function submit(decision) {{
  const feedback = document.getElementById('feedback')?.value || '';
  const msg = document.getElementById('msg');
  document.querySelectorAll('.btn').forEach(b => b.disabled = true);
  if (msg) msg.textContent = 'Submitting...';
  try {{
    const r = await fetch('/r/{token}/decision', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{decision, feedback: feedback || null}})
    }});
    if (r.ok) {{
      location.reload();
    }} else {{
      const d = await r.json().catch(() => ({{}}));
      if (msg) msg.textContent = d.detail || 'Error submitting decision.';
      document.querySelectorAll('.btn').forEach(b => b.disabled = false);
    }}
  }} catch(e) {{
    if (msg) msg.textContent = 'Network error. Please try again.';
    document.querySelectorAll('.btn').forEach(b => b.disabled = false);
  }}
}}
</script>
</body>
</html>"""


@router.get("/{token}", response_class=HTMLResponse, include_in_schema=False)
async def client_review_page(
    token: str,
    session: AsyncSession = Depends(get_session),
):
    """Public review page — no authentication required. Token is a URL-safe UUID."""
    review = (await session.exec(
        select(HitlReview).where(HitlReview.client_token == token)
    )).first()
    if not review:
        raise HTTPException(404, "Review not found or link has expired.")

    run = (await session.exec(select(Run).where(Run.run_id == review.run_id))).first()

    done = review.status != "pending"
    return HTMLResponse(_render_page(review, run, token, done=done, done_decision=review.decision or ""))


class ClientDecision(BaseModel):
    decision: str  # "approve" | "reject"
    feedback: Optional[str] = None


@router.post("/{token}/decision")
async def submit_client_decision(
    token: str,
    body: ClientDecision,
    session: AsyncSession = Depends(get_session),
):
    """Submit a decision from the public client review page. No API key required."""
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be 'approve' or 'reject'")

    review = (await session.exec(
        select(HitlReview).where(HitlReview.client_token == token)
    )).first()
    if not review:
        raise HTTPException(404, "Review not found or link has expired.")
    if review.status != "pending":
        raise HTTPException(409, f"Review already resolved (status: {review.status!r})")

    decision_payload = {"decision": body.decision, "edited": None, "feedback": body.feedback}
    resolve_review(review.review_id, decision_payload)

    _STATUS_MAP = {"approve": "approved", "reject": "rejected"}
    review.status = _STATUS_MAP[body.decision]
    review.decision = body.decision
    review.feedback = body.feedback
    review.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(review)

    session.add(HitlAuditEntry(
        review_id=review.review_id,
        actor_label="client",
        action=_STATUS_MAP[body.decision],
        note=body.feedback,
    ))

    await session.commit()
    return {"ok": True, "decision": body.decision}
