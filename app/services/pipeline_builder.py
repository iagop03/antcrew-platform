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
    "x": int, "y": int,
    "agent_cfg": {         # optional; required when type is NOT in AGENT_REGISTRY
      "system_prompt": str,       # required if agent_cfg present
      "role_description": str     # optional
    }
  }

  Parallel node (type == "parallel"):
  {
    "id":      str,                    # unique graph key
    "type":    "parallel",
    "label":   str,
    "model":   str,                    # default model for members that omit it
    "members": [                       # at least one member required
      {"type": str, "model": str},     # model is optional; falls back to group model
      ...
    ]
  }

  Members must be registered agent types (custom agent_cfg is not supported
  inside parallel groups in the current version).

  When instantiate_agent() returns None (type not in AGENT_REGISTRY), the builder
  checks for agent_cfg.system_prompt and instantiates a TemplateAgent instead of
  raising ValueError.  This enables user-defined custom agents created via
  POST /pipelines/custom-agents to run without any Python code changes.
  Existing pipelines without agent_cfg are unaffected — the field is optional.

Edges:
  {"from": str, "to": str, "condition": str | null, "is_else": bool | null}

  is_else marks an edge as an alternative / else branch (visual-only; ignored by the
  builder — it only reads "from", "to", and "condition" to construct the flow).
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
    from antcrew import Supervisor, build_llm
    from antcrew.agents.registry import instantiate_agent

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

        # Build a dedicated LLM for this node (or as the default for parallel members)
        node_model = node.get("model") or default_model
        _kw: dict = {}
        if byok_api_key is not None:
            _kw["api_key"] = byok_api_key
        if byok_base_url is not None:
            _kw["base_url"] = byok_base_url
        node_llm = build_llm(node_model, **_kw)

        # ── Parallel group node ───────────────────────────────────────────
        if agent_type == "parallel":
            from antcrew.core.supervisor import ParallelGroup

            members = node.get("members") or []
            if not members:
                raise ValueError(
                    f"Parallel node {node_id!r} has no members. "
                    "Add at least one entry to 'members'."
                )
            member_agents = []
            for member in members:
                member_type = member.get("type")
                if not member_type:
                    raise ValueError(
                        f"Parallel node {node_id!r}: each member must have a 'type'."
                    )
                member_model = member.get("model") or node_model
                member_llm = build_llm(member_model, **_kw)
                member_agent = instantiate_agent(member_type, member_llm)
                if member_agent is None:
                    raise ValueError(
                        f"Unknown agent type in parallel node {node_id!r}: {member_type!r}. "
                        "Custom agent_cfg is not supported inside parallel groups."
                    )
                member_agents.append(member_agent)
            agents[node_id] = ParallelGroup(*member_agents, name=node_id)
            log.debug(
                "pipeline_node id=%s type=parallel members=%s model=%s",
                node_id, [m.get("type") for m in members], node_model,
            )
            continue

        # ── Regular node ─────────────────────────────────────────────────
        agent = instantiate_agent(agent_type, node_llm)
        if agent is None:
            cfg = node.get("agent_cfg") or {}
            system_prompt = cfg.get("system_prompt", "").strip()
            if not system_prompt:
                raise ValueError(
                    f"Unknown agent type: {agent_type!r}. "
                    "Add agent_cfg.system_prompt to the node to use a custom TemplateAgent."
                )
            from antcrew.agents.template_agent import TemplateAgent
            agent = TemplateAgent(
                {
                    "name": node_id,
                    "system_prompt": system_prompt,
                    "role_description": cfg.get("role_description", ""),
                },
                node_llm,
            )

        # When node_id differs from agent_type (duplicate nodes), align agent.name
        # so current_agent in TeamState matches the agents dict key for HITL lookup.
        if node_id != agent_type:
            agent.name = node_id

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
