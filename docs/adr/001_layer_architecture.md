# ADR 001 — Layer 1 (runner.py) vs Layer 2 (engine_runner.py)

**Status:** Accepted  
**Date:** 2026-07-31

---

## Context

antcrew-platform exposes two distinct execution paths for AI-driven work:

| | Layer 1 | Layer 2 |
|---|---|---|
| Module | `app/services/runner.py` | `app/services/engine_runner.py` |
| Entry point | `runner.dispatch()` | `engine_runner.dispatch_engine()` |
| Underlying package | `antcrew` (LangGraph teams) | `antcrew-engine` (EngineLoop) |
| Abstraction | Named team + request string | Goal + tech + acceptance conditions |
| Agent topology | Fixed per team YAML | Dynamic; spawns capabilities as needed |
| State persistence | LangGraph checkpointer | FilesystemStore + `.antcrew/` dir |
| Resume support | Via LangGraph thread_id | Via `resume=True` + `output_dir` |

---

## Decision

Use **Layer 1** when:
- The agent topology is well-known and fixed (e.g. Jardineria: `PM → Dev → QA`).
- The task maps cleanly onto a named team defined in a `.yaml` file.
- You want LangGraph's native human-in-the-loop and interrupt primitives.
- The output is conversational or incremental (streaming state updates).

Use **Layer 2** when:
- The task is expressed as a goal with verifiable acceptance conditions.
- The agent must self-plan which capabilities to invoke (CodeWriter, SecurityAuditor, etc.).
- The run may be long-lived and must be resumable mid-execution.
- You need the convergence loop: the engine retries until all conditions are satisfied or `max_iter` is reached.

---

## Rationale

Both paths share the same infrastructure (event bus, budget gate, BYOK, HITL, WebSocket stream). The difference is purely in *who decides what to do next*: in Layer 1 the YAML team graph is the plan; in Layer 2 the EngineLoop's planner builds the plan at runtime from the goal.

The double-runner pattern is intentional, not technical debt. They serve different customer use cases:
- Font Jardineria (confirmed production) runs on Layer 1 with a fixed team.
- Security audit, multi-file code generation, and agentic research runs on Layer 2.

Merging them would sacrifice the simplicity of Layer 1 or the flexibility of Layer 2.

---

## Consequences

- Route handlers in `app/api/` must choose the right dispatcher. Currently `POST /run` → Layer 1; `POST /engine/run` → Layer 2.
- Feature additions (budget gate, BYOK, HITL timeout) must be applied to **both** `runner.py` and `engine_runner.py`. Use `runner_base.py` for shared logic.
- Tests that exercise the full run lifecycle need two variants if they test behaviour common to both layers.
