"""
Build a runnable antcrew team from a visual pipeline definition (JSON).

Definition schema:
  {
    "nodes": [
      {"id": str,   # unique key used in flow edges
       "type": str, # AGENT_REGISTRY key (defaults to id if omitted)
       "label": str,
       "model": str,
       "x": int, "y": int}
    ],
    "edges": [
      {"from": str, "to": str, "condition": str | null}
    ]
  }
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def build_team_from_definition(
    definition: dict | str,
    llm: Any,
) -> tuple[dict, Any]:
    """Return (agents_dict, Supervisor) ready to pass into team.run().

    Raises ValueError for unknown agent types or empty edge lists.
    """
    from antcrew.agents.registry import instantiate_agent
    from antcrew.core.supervisor import Supervisor

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
        agent = instantiate_agent(agent_type, llm)
        if agent is None:
            raise ValueError(f"Unknown agent type: {agent_type!r}")
        agents[node_id] = agent
        log.debug("pipeline_node id=%s type=%s", node_id, agent_type)

    flow: list = []
    for edge in edges:
        cond = edge.get("condition") or None
        if cond:
            flow.append((edge["from"], edge["to"], cond))
        else:
            flow.append((edge["from"], edge["to"]))

    return agents, Supervisor(flow=flow)
