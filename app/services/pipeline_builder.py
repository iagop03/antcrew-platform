"""
Build a runnable antcrew team from a visual pipeline definition (JSON).

Definition schema per node:
  {
    "id":           str,   # unique graph key (also used as flow edge ref)
    "type":         str,   # AGENT_REGISTRY key; defaults to id if omitted
    "label":        str,
    "model":        str,   # LLM model string — each node can use a different model
    "hitl":         bool,  # whether this agent requires human approval
    "channel_type": str,   # "platform" | "slack" | "telegram" (default: "platform")
    "x": int, "y": int
  }

Edges:
  {"from": str, "to": str, "condition": str | null}
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def build_team_from_definition(
    definition: dict | str,
    *,
    default_model: str = "claude",
    byok_api_key: Optional[str] = None,
    byok_base_url: Optional[str] = None,
) -> tuple[dict, Any]:
    """Return (agents_dict, Supervisor) ready to pass into team.run().

    Each node gets its own LLM instance built from node['model'] (falling back
    to default_model). Channel assignment and approval_required are set by the
    caller after this returns (runner.py wires per-node channels).

    Raises ValueError for unknown agent types or empty edge lists.
    """
    from antcrew.agents.registry import instantiate_agent
    from antcrew.core.supervisor import Supervisor
    from antcrew.config import build_llm

    if isinstance(definition, str):
        definition = json.loads(definition)

    nodes: list[dict] = definition.get("nodes", [])
    edges: list[dict] = definition.get("edges", [])

    if not nodes:
        raise ValueError("Pipeline has no nodes")
    if not edges:
        raise ValueError("Pipeline has no edges")

    agents: dict = {}
    for node in nodes:
        node_id: str = node["id"]
        agent_type: str = node.get("type") or node_id

        # Build a dedicated LLM for this node
        node_model = node.get("model") or default_model
        node_llm = build_llm(node_model, api_key=byok_api_key, base_url=byok_base_url)

        agent = instantiate_agent(agent_type, node_llm)
        if agent is None:
            raise ValueError(f"Unknown agent type: {agent_type!r}")

        agents[node_id] = agent
        log.debug("pipeline_node id=%s type=%s model=%s", node_id, agent_type, node_model)

    flow: list = []
    for edge in edges:
        cond = edge.get("condition") or None
        if cond:
            flow.append((edge["from"], edge["to"], cond))
        else:
            flow.append((edge["from"], edge["to"]))

    return agents, Supervisor(flow=flow)
