# antcrew-platform

[![PyPI](https://img.shields.io/pypi/v/antcrew-platform)](https://pypi.org/project/antcrew-platform/)
[![Python](https://img.shields.io/pypi/pyversions/antcrew-platform)](https://pypi.org/project/antcrew-platform/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/iagop03/antcrew-platform/actions/workflows/deploy.yml/badge.svg)](https://github.com/iagop03/antcrew-platform/actions)

**The production backend for [antcrew](https://github.com/iagop03/antcrew) multi-agent pipelines.**

antcrew-platform turns the antcrew CLI into a multi-tenant server: REST API, WebSocket event stream, HITL review queue, evaluations, cost tracking, webhook delivery, and a built-in dashboard — deployed in one Docker container.

---

## What it adds over the CLI

| | `antcrew run` (CLI) | `antcrew-platform` |
|---|---|---|
| Execution | Local process | Background thread pool, survives restarts |
| HITL | Terminal prompts | REST/Slack/web review queue; any reviewer, any device |
| Observability | stdout | `/runs`, WebSocket stream, cost roll-up per workspace |
| Teams | One at a time | Multi-workspace, multi-key, concurrent runs |
| Evaluations | Manual | Scheduled, scored, regression-tested |
| Model diff | None | `POST /run/compare` — same request, two models, typed diff |
| PR automation | `antcrew publish` | GitHub PR with auto-posted explainability comment |

---

## Features

### Pipeline orchestration
- **Team runs** (`POST /run/`) — trigger any antcrew team (DevTeam, FullStackTeam, ResearchTeam, ContentTeam, MinimalPipeline)
- **Engine runs** (`POST /engine/run`) — autonomous goal-directed `EngineLoop`; no fixed pipeline
- **Cancellation** — `POST /runs/{id}/cancel` stops in-flight runs cleanly
- **Real-time stream** — WebSocket (`/ws/events`) and SSE (`/runs/{id}/events`) with per-agent tokens

### Artifacts
- **`GET /runs/{id}/artifacts`** — typed output for team runs (code, tests, devops, docs) and engine runs (MemoryStore artifacts embedded in state; FilesystemStore served from disk)
- **`GET /runs/{id}/artifacts.zip`** — ZIP download of all artifacts
- **Ticket upsert** — PM tickets are persisted as `Ticket` rows; kanban + Jira/Linear export

### Model diff (Ola 3)
- **`POST /run/compare`** — run the same request against two LLM backends in parallel, diff the typed outputs (code files, tickets, PRD), compare cost and latency
- Supports both **team runs** (`team: "DevTeam"`, `request: "..."`) and **engine runs** (`team: "engine"`, `goal: "..."`)
- `GET /run/compare/{id}` returns `code_files`, `tickets`, `doc_files`, `test_files` diffs with `only_in_a / only_in_b / shared` and a `summary.winner` field

```bash
# Which model writes more tests for the same spec?
curl -X POST /run/compare \
  -d '{"team":"DevTeam","request":"Build JWT auth","model_a":"claude","model_b":"gpt-4o"}'

# Same, but with the autonomous engine
curl -X POST /run/compare \
  -d '{"team":"engine","goal":"Build a REST API","model_a":"claude","model_b":"gpt-4o"}'
```

### Evaluations & regression (Ola 3)
- **`POST /evals/`** — scored pipeline run; deterministic metrics (ticket count, code file count, review verdict) without an LLM judge
- **`POST /evals/regression`** — replay a list of historical run IDs with current prompts; detects quality regression before merging a prompt change ("CI for your own agents")
- **`GET /evals/regression/{id}`** — aggregate pass rate + `regression_rate` per batch
- **`GET /evals/compare`** — score delta between two evals (regression / improved flags)
- **Recurring schedules** — `EvalSchedule` rows fire evals on a cron-like interval

```bash
# Did my prompt change regress quality vs last week's runs?
curl -X POST /evals/regression \
  -d '{"run_ids":["abc123","def456","ghi789"]}'

# Poll until done
curl /evals/regression/{regression_id}
# → {"regression_rate": 0.33, "passed": 2, "failed": 1, ...}
```

### Engine progress (Ola 3)
- **`GET /engine/runs/{id}/progress`** — condition satisfaction status for autonomous engine runs
- Shows which goal conditions are `satisfied / pending / not_reached` + capability execution history (name, duration, cost, produced artifacts)

```json
{
  "run_id": "...",
  "status": "running",
  "conditions": {
    "requirements_exists": "satisfied",
    "architecture_exists": "satisfied",
    "implementation_exists": "pending",
    "tests_pass": "pending"
  },
  "capabilities_executed": [
    {"name": "Architect", "duration_s": 14.2, "cost_usd": 0.04, "produced": ["architecture"]},
    {"name": "TaskPlanner", "duration_s": 8.1, "cost_usd": 0.02, "produced": ["task_graph"]}
  ]
}
```

### HITL review queue
- Agents pause mid-pipeline and emit `hitl.review_required` — human approves / rejects / edits the artifact / sends feedback
- Reviewers can act via **REST** (`POST /reviews/{id}/resolve`), **Slack** (interactive button), or a **public web link** (no login needed)
- Per-workspace HITL timeout; assignee routing; audit log
- Engine runs support HITL via `hitl_after: ["architect", "task_planner"]` in `POST /engine/run`

### GitHub PR automation
- `pipeline.end` triggers `GitHubIntegration` → branch, commit, PR
- Auto-posts an explainability comment on the PR summarizing what was built, tickets resolved, files changed, and review verdict

### Multi-workspace auth
- API key header (`X-Api-Key`) or env var `PLATFORM_API_KEY`
- Per-key roles: `admin | write | read | reviewer`
- One key can access multiple workspaces (membership table)
- BYOK mode: workspaces supply their own LLM API keys (Anthropic, OpenAI, Groq, etc.)

### Cost management
- Per-run `cost_usd` + `duration_s`; per-workspace `total_cost_usd`
- Workspace `max_cost_usd` budget cap — engine runs abort when exceeded
- Trial multiplier, Stripe / Lemon Squeezy billing hooks

### Webhooks
- Per-workspace outbound webhooks on any event type (`pipeline.end`, `hitl.review_required`, `*`)
- Delivery with exponential-backoff retry; `WebhookDelivery` audit trail

---

## Quick start

### Docker (recommended)

```bash
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  ghcr.io/iagop03/antcrew-platform:latest
```

### From source

```bash
git clone https://github.com/iagop03/antcrew-platform.git
cd antcrew-platform
pip install -e ".[dev]"

export ANTHROPIC_API_KEY=sk-ant-...

uvicorn app.main:app --reload
# → http://localhost:8000
# → http://localhost:8000/docs  (Swagger UI)
```

### First run

```bash
# Trigger a pipeline
curl -X POST http://localhost:8000/run/ \
  -H "Content-Type: application/json" \
  -d '{"team":"DevTeam","request":"Build a user authentication module"}'

# → {"run_id": "abc123", "status": "running"}

# Poll status
curl http://localhost:8000/runs/abc123

# Stream events (WebSocket)
wscat -c ws://localhost:8000/ws/events

# Get artifacts
curl http://localhost:8000/runs/abc123/artifacts
```

---

## API reference

### Team runner (Layer 1 — LangGraph)

| Method | Path | Description |
|---|---|---|
| `POST` | `/run/` | Start a pipeline run |
| `GET` | `/runs/` | List runs (filter by status, team, workspace) |
| `GET` | `/runs/{id}` | Run detail + cost + duration |
| `GET` | `/runs/{id}/artifacts` | Code, test, devops, doc artifacts |
| `GET` | `/runs/{id}/artifacts.zip` | ZIP download of all artifacts |
| `GET` | `/runs/{id}/tickets` | Tickets produced by this run |
| `GET` | `/runs/{id}/events` | SSE event stream |
| `POST` | `/runs/{id}/cancel` | Cancel in-flight run |
| `WS` | `/ws/events` | WebSocket live event stream (all runs) |

### Engine runner (Layer 2 — EngineLoop)

| Method | Path | Description |
|---|---|---|
| `POST` | `/engine/run` | Start a goal-driven engine run |
| `POST` | `/engine/run/{id}/cancel` | Cancel in-flight engine run |
| `GET` | `/engine/runs/{id}/progress` | Condition satisfaction + capability history |
| `GET` | `/engine/capabilities` | List available engine capabilities |

### Model diff

| Method | Path | Description |
|---|---|---|
| `POST` | `/run/compare` | Compare same request on two LLM backends |
| `GET` | `/run/compare/{id}` | Get diff result (code_files, tickets, cost, latency) |
| `GET` | `/run/compare` | List recent comparisons |

### Evaluations

| Method | Path | Description |
|---|---|---|
| `POST` | `/evals/` | Trigger an eval run |
| `GET` | `/evals/` | List evals (filter by status, team, regression_id) |
| `GET` | `/evals/{id}` | Eval detail + report |
| `POST` | `/evals/report` | Upload a pre-computed eval report (local CI) |
| `GET` | `/evals/compare` | Score delta between two evals |
| `POST` | `/evals/regression` | Replay historical runs to detect prompt regression |
| `GET` | `/evals/regression/{id}` | Aggregate regression status + pass rate |

### HITL reviews

| Method | Path | Description |
|---|---|---|
| `POST` | `/reviews/` | Create a review request (called by agents) |
| `GET` | `/reviews/` | List reviews (filter by status, mine) |
| `GET` | `/reviews/{id}` | Review detail + artifact |
| `POST` | `/reviews/{id}/resolve` | Submit approve / reject / edit decision |
| `GET` | `/reviews/token/{token}` | Public review link (no auth) |

### Tickets

| Method | Path | Description |
|---|---|---|
| `GET` | `/tickets/` | List tickets (filter by run, status) |
| `GET` | `/tickets/{id}` | Ticket detail |
| `PATCH` | `/tickets/{id}/status` | Move ticket (open → in_progress → done) |
| `POST` | `/tickets/export/jira` | Push tickets to Jira |
| `POST` | `/tickets/export/linear` | Push tickets to Linear |

### Workspaces, auth, templates

| Method | Path | Description |
|---|---|---|
| `GET` | `/workspaces/` | List workspaces |
| `POST` | `/workspaces/` | Create workspace |
| `GET` | `/workspaces/{id}/budget` | Budget usage |
| `POST` | `/workspaces/{id}/members` | Add API key to workspace |
| `GET` | `/api-keys/` | List API keys |
| `POST` | `/api-keys/` | Create API key |
| `DELETE` | `/api-keys/{label}` | Revoke API key |
| `GET` | `/templates/` | List run templates |
| `POST` | `/templates/` | Save a run template |
| `GET` | `/templates/{id}` | Load template |

### Webhooks

| Method | Path | Description |
|---|---|---|
| `GET` | `/webhooks/` | List webhook configs |
| `POST` | `/webhooks/` | Register a webhook URL + event filter |
| `DELETE` | `/webhooks/{id}` | Remove webhook |
| `GET` | `/webhooks/deliveries` | Delivery audit log with retry status |

Full interactive docs at `/docs` (Swagger UI) and `/redoc`.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///platform.db` | DB connection string |
| `PLATFORM_API_KEY` | — | Master API key (bypasses DB key check when set) |
| `ANTHROPIC_API_KEY` | — | Default Claude model |
| `OPENAI_API_KEY` | — | OpenAI models and eval judge scoring |
| `SLACK_BOT_TOKEN` | — | Slack bot for interactive HITL (Socket Mode) |
| `SLACK_APP_TOKEN` | — | `xapp-…` for Slack Socket Mode |
| `SLACK_CHANNEL_ID` | — | Default Slack channel for HITL requests |
| `SLACK_TOKEN_ENCRYPTION_KEY` | — | Fernet key for encrypting per-workspace Slack tokens |
| `HITL_TIMEOUT_S` | `3600` | Seconds before a HITL review auto-times-out |
| `GITHUB_TOKEN` | — | GitHub PR integration |
| `WEBHOOK_URL` | — | Global fallback webhook fired on every `pipeline.end` |
| `PLATFORM_BASE_URL` | — | Public base URL for review links in webhooks |
| `ANTCREW_WORKERS` | `4` | Background engine worker threads |
| `BYOK_ENCRYPTION_KEY` | — | Fernet key for encrypting per-workspace LLM API keys |

---

## Stack

| Layer | Tech |
|---|---|
| API | FastAPI + Pydantic v2 |
| ORM | SQLModel (async) |
| DB | SQLite (dev) · PostgreSQL (prod, via `asyncpg`) |
| Migrations | Alembic (21 revisions) |
| Auth | `X-Api-Key` header or `PLATFORM_API_KEY` env var |
| Frontend | Alpine.js SPA with live WebSocket updates |
| CI/CD | GitHub Actions → Docker → Fly.io |

---

## Dashboard

Built-in SPA served at `/`:

- **Runs** — table with stats, new run modal, live status updates
- **Run detail** — event timeline, agent chips, HITL review modal, artifact browser
- **Reviews** — review queue with rendered PRD, tickets, code, and code review
- **Evals** — eval table, compare modal, regression batch view, recurring schedules
- **Tickets** — kanban board (open → in_progress → done) with Jira/Linear export
- **Webhooks** — per-workspace webhook URL management

API key is stored in `localStorage` and sent as `X-Api-Key` on every request. In open mode (no key configured) auth is skipped.

---

## Makefile

```bash
make run              # Start dev server (SQLite, hot-reload)
make migrate          # Apply pending Alembic migrations
make migration NAME=x # Generate a new migration
make check-migrations # CI gate — exits 1 if pending migrations exist
make test             # Run test suite (339 tests, no live API calls)
make coverage         # Run tests with coverage report
```

---

## Deploy to Fly.io

```bash
fly apps create antcrew-platform
fly secrets set ANTHROPIC_API_KEY=sk-ant-... DATABASE_URL=postgresql+asyncpg://...
fly deploy
```

The provided `fly.toml` builds a Docker image from `Dockerfile`, runs Alembic migrations on release, and exposes port 8000.

---

## Related

- [antcrew](https://github.com/iagop03/antcrew) — the multi-agent framework this platform orchestrates (Layer 1: LangGraph pipeline)
- [antcrew-engine](https://github.com/iagop03/antcrew-engine) — the autonomous EngineLoop (Layer 2: goal-directed, no fixed pipeline)
