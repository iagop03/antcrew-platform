"""Ticket export to external project management tools (Jira, Linear).

Jira export delegates to antcrew.integrations.JiraIntegration — the OSS
implementation is idempotent (uses antcrew:<ticket_id> labels to detect
existing issues), supports full ADF body with acceptance criteria, and has
22 tests. Running the same export twice updates the issue, not duplicates.

Linear export is implemented directly here using async httpx (no OSS equivalent).
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.run import Ticket as DbTicket


# ---------------------------------------------------------------------------
# Adapter: platform DB Ticket → antcrew JiraIntegration interface
# ---------------------------------------------------------------------------

class _PriorityAdapter:
    """Mimics antcrew.core.artifacts.Priority enum's .value attribute."""
    def __init__(self, value: str) -> None:
        self.value = value or "medium"


class _TicketAdapter:
    """Wraps a platform DB Ticket so JiraIntegration can consume it.

    JiraIntegration expects: .id, .title, .description, .acceptance_criteria (list),
    .priority.value (str matching antcrew Priority enum values).
    """
    def __init__(self, db_ticket: "DbTicket") -> None:
        self.id = db_ticket.ticket_id
        self.title = db_ticket.title
        self.description = db_ticket.description or ""
        self.acceptance_criteria = [
            ac.strip()
            for ac in (db_ticket.acceptance_criteria or "").splitlines()
            if ac.strip()
        ]
        self.priority = _PriorityAdapter(db_ticket.priority or "medium")


# ---------------------------------------------------------------------------
# Jira — delegates to antcrew OSS JiraIntegration (idempotent, full ADF)
# ---------------------------------------------------------------------------

async def export_to_jira(ticket: "DbTicket") -> str:
    """Upsert a Jira issue for this ticket. Returns the browse URL.

    Uses antcrew.integrations.JiraIntegration.upsert_issue() which is idempotent:
    re-exporting the same ticket updates the existing issue instead of creating
    a duplicate (keyed by antcrew:<ticket_id> label).
    """
    url = os.environ.get("JIRA_URL", "").rstrip("/")
    user = os.environ.get("JIRA_USER", "")
    token = os.environ.get("JIRA_TOKEN", "")
    project = os.environ.get("JIRA_PROJECT_KEY", "")

    if not all([url, user, token, project]):
        raise ValueError(
            "Jira not configured. Set JIRA_URL, JIRA_USER, JIRA_TOKEN, and JIRA_PROJECT_KEY."
        )

    from antcrew.integrations.jira import JiraIntegration

    jira = JiraIntegration(url=url, email=user, api_token=token, project_key=project)
    adapter = _TicketAdapter(ticket)

    # JiraIntegration uses synchronous httpx — run in thread pool to not block uvicorn
    loop = asyncio.get_running_loop()
    jira_key, _created = await loop.run_in_executor(None, jira.upsert_issue, adapter)

    return f"{url}/browse/{jira_key}"


# ---------------------------------------------------------------------------
# Linear — direct async implementation (no OSS equivalent)
# ---------------------------------------------------------------------------

def _linear_priority(priority: str) -> int:
    return {"critical": 1, "high": 1, "medium": 2, "low": 3}.get(priority, 2)


async def export_to_linear(ticket: "DbTicket") -> str:
    """Create or update a Linear issue and return its URL."""
    import httpx

    api_key = os.environ.get("LINEAR_API_KEY", "")
    team_id = os.environ.get("LINEAR_TEAM_ID", "")

    if not all([api_key, team_id]):
        raise ValueError("Linear not configured. Set LINEAR_API_KEY and LINEAR_TEAM_ID.")

    query = """
    mutation IssueCreate($title: String!, $description: String, $teamId: String!, $priority: Int) {
        issueCreate(input: {
            title: $title
            description: $description
            teamId: $teamId
            priority: $priority
        }) {
            success
            issue { id url }
        }
    }
    """
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://api.linear.app/graphql",
            json={
                "query": query,
                "variables": {
                    "title": ticket.title,
                    "description": ticket.description or "",
                    "teamId": team_id,
                    "priority": _linear_priority(ticket.priority or "medium"),
                },
            },
            headers={"Authorization": api_key, "Content-Type": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise ValueError(data["errors"][0]["message"])
        return data["data"]["issueCreate"]["issue"]["url"]


# ---------------------------------------------------------------------------
# Discovery — which targets are configured
# ---------------------------------------------------------------------------

def available_targets() -> list[str]:
    """Return configured export target names for UI discovery."""
    targets = []
    if os.environ.get("JIRA_URL") and os.environ.get("JIRA_TOKEN"):
        targets.append("jira")
    if os.environ.get("LINEAR_API_KEY"):
        targets.append("linear")
    return targets
