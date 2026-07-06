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
# 1. Install antcrew (not yet on PyPI — install from source)
pip install "git+https://github.com/iagop03/antcrew.git"

# 2. Install platform dependencies
pip install -e ".[dev]"

# 3. Run locally (SQLite, no auth)
make run
# → http://localhost:8000

# Apply migrations (PostgreSQL)
DATABASE_URL=postgresql+asyncpg://... make migrate
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///platform.db` | DB connection string |
| `PLATFORM_API_KEY` | — | Master API key (bypasses DB key check) |
| `SLACK_WEBHOOK_URL` | — | Optional Slack webhook for HITL notifications |
| `OPENAI_API_KEY` | — | Required for eval judge scoring |

## API overview

| Method | Path | Description |
|---|---|---|
| `POST` | `/run/` | Start a pipeline run |
| `GET` | `/runs/` | List runs |
| `GET` | `/runs/{id}` | Run detail |
| `POST` | `/runs/{id}/cancel` | Cancel a running pipeline |
| `GET` | `/runs/{id}/events` | SSE event stream |
| `WS` | `/ws/events` | WebSocket live events |
| `GET` | `/reviews/` | List HITL reviews |
| `POST` | `/reviews/{id}` | Submit a review decision |
| `GET` | `/evals/` | List eval runs |
| `POST` | `/evals/` | Trigger an eval |
| `GET` | `/evals/compare` | Compare two evals |
| `GET` | `/tickets/` | List tickets |
| `GET` | `/templates/` | List run templates |
| `GET` | `/workspaces/` | List workspaces |
| `POST` | `/workspaces/{id}/members` | Add API key to workspace |

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
