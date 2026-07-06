# Changelog — antcrew-platform

## v0.3.0 (2026-07-05)

### Data model
- `Workspace` — new table (`id`, `name`, `slug`); multi-team project isolation
- `HitlReview` — new table tracking HITL review requests and decisions
- `RunTemplate` — new table for saved run configurations
- `WebhookDelivery` — new table for webhook retry tracking (replaces fire-and-forget)
- `Run.created_by` — API key label that triggered the run (nullable, backward-compatible)
- `Run.workspace_id` — optional FK to `Workspace`
- `ApiKey.workspace_id` — optional FK to `Workspace`

### HITL (Human-in-the-Loop)
- `app/core/channel.py` — `PlatformChannel` implements antcrew's `BaseChannel` protocol
  - `send_for_review()` blocks the executor thread on a `concurrent.futures.Future`
  - `resolve_review()` is called by the HTTP handler to unblock the thread
  - Uses `asyncio.wrap_future` + `call_soon_threadsafe` for thread-safe signaling
- `app/api/reviews.py` — `POST /reviews/{review_id}` resolves a pending HITL review
- `app/core/listener.py` — handles `hitl.review_required` bus event → persists `HitlReview` row
- `app/services/runner.py` — injects `PlatformChannel` into `approval_required` agents; calls `run_interactive()` instead of `run()` when HITL agents are present
- `run.html` — HITL review modal triggered by `hitl.review_required` WS event; sends decision to `POST /reviews/:id`

### Run attribution
- `POST /run/` now captures the authenticated API key label and stores it in `Run.created_by`
- New `get_api_key_label()` dependency in `app/core/auth.py`

### Run templates
- `GET /templates/` — list templates (optional `?workspace_id` filter)
- `POST /templates/` — create a template (name, team, request, max_cost_usd)
- `DELETE /templates/{id}` — delete a template
- `index.html` — "Load from template" buttons in the New Run modal

### Workspaces
- `GET /workspaces/`, `POST /workspaces/`, `GET /workspaces/{id}`, `DELETE /workspaces/{id}`
- Slug validation (lowercase alphanumeric + hyphens); 409 on duplicate slug

### Webhooks
- Replaced fire-and-forget `httpx` call with `WebhookDelivery` table insertion
- `app/services/webhook.py` — background retry loop (every 30s, exponential backoff, up to 5 attempts)

### Infrastructure
- `asyncpg>=0.29` added as a runtime and dev dependency
- `tests/conftest.py` — `TEST_DB_URL` env var for PostgreSQL CI (default: SQLite in-memory)
- `.github/workflows/test-postgres.yml` — GitHub Actions CI running 75 tests against PostgreSQL 16

### Tests
- 75 tests total (up from 36 in v0.2.0)
- `tests/test_v2_api.py` — stats, cancel, since_id, search, api-keys, health, created_by
- `tests/test_hitl.py` — HITL review flow, future resolution, error cases
- `tests/test_templates.py` — template CRUD
- `tests/test_workspaces.py` — workspace CRUD

---

## v0.2.0 (2026-07-05)

### Bug fixes
- `listener.py`: removed unused `import time`; replaced deprecated `asyncio.get_event_loop()` with `asyncio.get_running_loop()`
- `runs.py`, `api/runs.py`, `api/tickets.py`: fixed `AsyncSession` import from `sqlalchemy.ext.asyncio` → `sqlmodel.ext.asyncio.session` (restores `.exec()` method)
- `runs.py`: moved `from sqlmodel import select` out of function body to module level
- `run.html`: fixed `run_id` extraction via regex (`/^\/run\/(.+?)\/?$/`) instead of `.split('/').pop()` which breaks on trailing slashes
- `run.html`: WebSocket `onmessage` now appends events to the timeline instead of calling `loadEvents()` on every event (eliminates full-list reload and flash)

### Data model
- `Run` — new `duration_s: Optional[float]` field; computed by listener on `pipeline.end`
- `Run` — status now includes `"cancelled"` as a valid value
- `Ticket` — new `acceptance_criteria: str` and `dependencies: str` (JSON list) fields; populated from antcrew output when present
- `Event` — added composite index `(run_id, timestamp)` for faster `GET /runs/:id/events` queries
- `ApiKey` — new table for multi-key authentication (see Auth section)

### API
- `GET /runs/stats` — aggregate counts (total, running, success, error, cancelled), total cost, avg duration
- `POST /runs/:id/cancel` — marks a running run as cancelled in the DB; returns 409 if not running
- `GET /runs/?since_id=<int>` — cursor-based pagination; stable under live inserts
- `PATCH /tickets/:id/status` — now accepts a Pydantic `StatusUpdate` model with enum validation instead of raw `dict`
- `GET /tickets/?search=<str>` — server-side search filter by title, description, PRD
- `POST /api-keys/` — create a new API key (label + raw key returned once)
- `GET /api-keys/` — list active (non-revoked) keys by label
- `DELETE /api-keys/:label` — revoke a key
- `GET /health` — now checks DB connectivity; returns 503 with `{"db": false}` if unreachable

### Auth
- Multi-key mode: when `PLATFORM_API_KEY` env is not set, auth checks the `api_key` DB table (sha256-hashed). If no keys exist → open mode
- Single-key mode (`PLATFORM_API_KEY` set) unchanged — no DB hit on auth
- WebSocket `/ws/events` now checks auth via `api_key` query param when auth is enabled
- Bootstrap: `POST /api-keys/` is accessible in open mode to create the first key

### WebSocket
- `asyncio.Queue(maxsize=100)` — prevents unbounded memory growth on slow clients; excess events are dropped with a debug log
- Ping/keepalive: server sends `{"type":"ping"}` every 30 seconds to detect dead connections
- Auth: `?api_key=<key>` query param respected; connection closed with code 4001 if invalid

### Runner
- `ANTCREW_WORKERS` env var (default `4`) — configures `ThreadPoolExecutor` max workers
- `ANTCREW_DISPATCH_TIMEOUT` env var (default `10`) — timeout waiting for `pipeline.start` event
- `_store_result` retries up to 3 times with exponential backoff on DB failure
- `runner.shutdown()` — graceful executor shutdown on app teardown (`cancel_futures=True`)
- `asyncio.get_event_loop()` → `asyncio.get_running_loop()` in dispatch

### Listener
- Request truncated to 2000 chars before storing in `Run.request`
- `WEBHOOK_URL` env var — if set, sends a POST to that URL on every `pipeline.end` with `{run_id, status, cost_usd, team}`
- `duration_s` computed and stored on `pipeline.end`
- `from sqlmodel import select` moved to module-level import

### Logging
- Structured JSON logs by default (`LOG_FORMAT=json`). Set `LOG_FORMAT=text` for human-readable output
- `LOG_LEVEL` env var (default `INFO`)

### Frontend
- `index.html`: loading skeletons on runs table and stats; error banner with retry on fetch failure; WS reconnect with exponential backoff; WS connection indicator in nav; duration column; stats fetched from `GET /runs/stats` (server-side, not client-calculated)
- `run.html`: loading skeletons for run header, events, tickets; error state for 404/network failures; WS append instead of reload; WS reconnect with backoff; Cancel button for running runs
- `tickets.html`: search input (filters by title, description, PRD, ticket_id); loading skeletons; error state with retry; acceptance_criteria shown in detail modal; move error feedback

### Infrastructure
- `.dockerignore` — excludes `tests/`, `.git/`, `*.db`, `__pycache__`, `.env` from Docker build context
- `CORS_ORIGINS` env var — comma-separated allowed origins (default `*`)

---

## v0.1.0 (2026-06-27)

Initial release.

- FastAPI app with SQLModel + aiosqlite (SQLite backend)
- `POST /run/` (202 Accepted), `GET /run/teams`
- `GET /runs/`, `GET /runs/:id`, `GET /runs/:id/state`, `GET /runs/:id/tickets`, `GET /runs/:id/events`
- `GET /tickets/`, `PATCH /tickets/:id/status`
- `WS /ws/events` — real-time event stream
- `X-Api-Key` auth (single-key env var mode)
- Alpine.js + Tailwind CDN dashboard: runs table, run detail, tickets kanban
- Dockerfile + docker-compose.yml
- 36 tests (pytest-asyncio)
