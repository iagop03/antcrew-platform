# antcrew-platform

Backend platform for orchestrating [antcrew](https://github.com/iagop03/antcrew) multi-agent pipelines. Provides run management, HITL review queues, evaluations, ticket tracking, webhooks, and multi-workspace API key auth — all exposed via a FastAPI REST + WebSocket API and a built-in dashboard.

## Features

- **Pipeline runs** — trigger antcrew teams via REST, stream events over WebSocket or SSE, cancel in-flight runs
- **HITL review queue** — agents pause for human approval; reviewers can approve, reject, edit artifacts, or send feedback
- **Evaluations** — scored pipeline runs with a judge LLM; compare two evals; recurring schedules
- **Tickets** — kanban board of tickets produced by pipeline runs; export to Jira / Linear
- **Templates** — save and reuse run configurations; the antcrew CLI can pull and execute them
- **Webhooks** — per-workspace outbound webhooks on pipeline events
- **Multi-workspace memberships** — one API key can access multiple workspaces
- **Dark-theme dashboard** — Alpine.js SPA at `/` with live WebSocket updates

## Stack

| Layer | Tech |
|---|---|
| API | FastAPI + Pydantic v2 |
| ORM | SQLModel (async) |
| DB | SQLite (dev) · PostgreSQL (prod, via asyncpg) |
| Migrations | Alembic |
| Auth | `X-Api-Key` header or `PLATFORM_API_KEY` env var |
| Frontend | Alpine.js + Tailwind CDN + custom dark CSS |

## Quick start

```bash
# 1. Install antcrew + engine
pip install antcrew antcrew-engine

# Or from source (development)
pip install "git+https://github.com/iagop03/antcrew.git"
pip install "git+https://github.com/iagop03/antcrew-engine.git"

# 2. Install platform dependencies
pip install -e ".[dev]"

# 3. Configure your LLM key
export ANTHROPIC_API_KEY=sk-ant-...

# 4. Run locally (SQLite, no auth)
uvicorn app.main:app --reload
# → http://localhost:8000

# Or via Makefile:
make run

# Apply migrations (PostgreSQL)
DATABASE_URL=postgresql+asyncpg://... make migrate
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///platform.db` | DB connection string |
| `PLATFORM_API_KEY` | — | Master API key (bypasses DB key check when set) |
| `ANTHROPIC_API_KEY` | — | Required for engine runs with the default Claude model |
| `OPENAI_API_KEY` | — | Required for OpenAI models and eval judge scoring |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook for HITL review notifications |
| `SLACK_BOT_TOKEN` | — | Slack bot token for interactive HITL (Socket Mode) |
| `SLACK_APP_TOKEN` | — | Slack app-level token (`xapp-…`) for Socket Mode |
| `SLACK_CHANNEL_ID` | — | Slack channel to post HITL review requests |
| `HITL_TIMEOUT_S` | `3600` | Seconds before a HITL review auto-times-out |
| `WEBHOOK_URL` | — | Global fallback webhook fired on every `pipeline.end` |
| `PLATFORM_BASE_URL` | — | Public base URL used to build review links in webhooks |
| `ANTCREW_WORKERS` | `4` | Number of background engine worker threads |

## API overview

| Method | Path | Description |
|---|---|---|
### Team runner (antcrew Layer 1)

| Method | Path | Description |
|---|---|---|
| `POST` | `/run/` | Start a pipeline run (DevTeam, ResearchTeam, etc.) |
| `GET` | `/runs/` | List runs |
| `GET` | `/runs/{id}` | Run detail + artifact listing |
| `POST` | `/runs/{id}/cancel` | Cancel a running pipeline |
| `GET` | `/runs/{id}/events` | SSE event stream |
| `WS` | `/ws/events` | WebSocket live events |

### Engine runner (antcrew-engine Layer 2)

| Method | Path | Description |
|---|---|---|
| `POST` | `/engine/run` | Start a capability-driven engine run |
| `GET` | `/engine/runs/{id}` | Engine run status + artifact list |
| `GET` | `/engine/runs/{id}/artifacts/{path}` | Serve individual artifact file |
| `GET` | `/engine/runs/{id}/zip` | Download all artifacts as ZIP |

### HITL, reviews, evals, tickets

| Method | Path | Description |
|---|---|---|
| `GET` | `/reviews/` | List HITL reviews |
| `POST` | `/reviews/{id}` | Submit approve / reject / edit decision |
| `GET` | `/evals/` | List eval runs |
| `POST` | `/evals/` | Trigger an eval |
| `GET` | `/evals/compare` | Compare two evals |
| `GET` | `/tickets/` | List tickets |

### Configuration

| Method | Path | Description |
|---|---|---|
| `GET` | `/templates/` | List run templates |
| `GET` | `/workspaces/` | List workspaces |
| `POST` | `/workspaces/{id}/members` | Add API key to workspace |
| `GET` | `/api-keys/` | List API keys |

Full interactive docs at `/docs` (Swagger UI).

## Makefile targets

```bash
make run              # Start dev server
make migrate          # Apply pending Alembic migrations
make migration NAME=x # Generate a new migration
make check-migrations # CI gate — exits 1 if pending migrations exist
make test             # Run test suite
make coverage         # Run tests with coverage report
```

## Dashboard

The built-in SPA is served at `/` and covers:

- **Runs** (`/`) — table with stats, new run modal, live status updates
- **Run detail** (`/run/{id}`) — event timeline, tickets sidebar, HITL review modal
- **Reviews** (`/reviews`) — review queue with artifact rendering (PRD, tickets, code, code review)
- **Evals** (`/evals`) — eval table, compare modal, recurring schedule management
- **Tickets** (`/tickets`) — kanban board with Jira/Linear export
- **Webhooks** (`/webhooks`) — per-workspace webhook URL management

API key is stored in `localStorage` and sent as `X-Api-Key` on every request. In open mode (no key configured) auth is skipped.

## Related

- [antcrew](https://github.com/iagop03/antcrew) — the OSS multi-agent framework this platform orchestrates
