# Changelog — antcrew-platform

## v0.4.6 (2026-07-26)

### Changed
- **Onboard page migrated to multi-tenant flow** — `onboard.html` now calls `POST /auth/register` (email + password) instead of `POST /onboard/bootstrap`. Each user who registers gets their own isolated workspace. Added "¿Ya tienes cuenta? Inicia sesión" link to `/login`. The `/onboard/bootstrap` endpoint remains available for ops/system-level initialization via API.

---

## v0.4.5 (2026-07-26)

### Changed
- **API key labels are now unique per workspace, not globally** — `UNIQUE(label)` constraint on `api_key` replaced with `UNIQUE(label, workspace_id)`. Two workspaces can now have keys (e.g. reviewer keys) with the same label without conflict. Migration 025 handles the schema change; `POST /api-keys/` 409 check updated to scope by workspace.

---

## v0.4.4 (2026-07-26)

### Security
- **Fail-open authorization fixed in `reviews.py` and `tickets.py`** — 6 occurrences of `if run and not ws_accessible(...)` changed to `if run is None or not ws_accessible(...)`. Previously, when a `Run` row was missing (orphaned data, deleted run), the condition evaluated to `False` and access was silently granted instead of denied. Fail-closed: a missing run now always raises 403.
- **BYOK keys no longer stored in plaintext in production** — `_encrypt()` now raises `RuntimeError` if `BYOK_ENCRYPTION_KEY` is unset outside dev mode, instead of silently writing the API key to the database as plaintext. `_decrypt()` also raises in non-dev mode on missing key, and on decryption failure (removing the silent plaintext fallback that exposed pre-encryption keys). Dev mode (`APP_ENV=dev`) retains the plaintext path with a log warning.

---

## v0.4.3 (2026-07-26)

### Security
- **Stored XSS on public client review page (`/r/{token}`)** — `html.escape()` applied to every user-controlled value interpolated into the HTML response in `client_review.py`: artifact title, summary, description, comment, file_path, verdict, generic k/v pairs, ticket id/title, agent_name, and run.request. The page is deliberately unauthenticated (shared with external clients) making XSS especially dangerous — an attacker could steal the client token or forge the approval UI. Defined `_e = html.escape` as an explicit shorthand to make future additions hard to miss.
- **CSRF coverage completed** — 9 previously unprotected routers now have `dependencies=_csrf`: pipeline, runs, tickets, templates, evals, eval_schedules, compare, contract_schemas. Notably `POST /run/compare` (double-spend vector) and `POST /runs/upload` are now protected.

---

## v0.4.2 (2026-07-26)

### Security
- **#16 CRITICAL — workspace isolation on register**: `POST /auth/register` now always creates a fresh workspace per user; previously any registrant was attached to the first existing workspace with admin role
- **#3 — role fallback hardened**: invalid role value in DB now falls back to `"read"` instead of `"write"` in both `_authenticate()` and `_session_context()`
- **CSRF double-submit cookie**: new `app/core/csrf.py`; `csrf_token` cookie (non-HttpOnly, `SameSite=Strict`) set alongside session on login/register/token-exchange and cleared on logout; `require_csrf` dependency wired to high-impact routers (api_keys, workspaces, billing, engine, reviews, pipelines, client_review, workspaces_members); `apiFetch()` in app.js reads cookie and sends `X-CSRF-Token` header on all state-changing requests
- **Rate limiting on `/auth/login` and `/auth/register`**: both endpoints now enforce the existing `rate_limit.check()` sliding window (IP-based), blocking brute-force and trial-credit farming

### Fixed
- **#1 — duplicate `serve` command** removed (audit finding; canonical impl in `cli/serve_cmd.py`)
- **Workspace slug uniqueness on register**: random hex suffix loop prevents `IntegrityError` on concurrent registrations with the same email prefix

### Added
- **`owner_user_id`** column on `Workspace` model + Alembic migration `024_workspace_owner` + SQLite inline migration
- **Per-reviewer HITL notifications**: email (SMTP), Slack DM (`conversations.open` + `chat.postMessage`), Telegram Bot API; reviewer `ApiKey` rows carry `slack_user_id` / `telegram_chat_id` fields; Alembic migration `023_apikey_notifications`
- **User auth tables**: Alembic migration `022_user_auth` (`user`, `user_session`, `api_key.user_id`)
- **`WebFetchTool`** available as a platform-side tool via antcrew library upgrade (SSRF-guarded)

### CI
- **CHANGELOG version check** on every push/PR in all three repos

---

## v0.4.1 (2026-07-24)

### Added
- **`POST /engine/runs/{id}/publish`** — push engine run artifacts to GitHub and open a PR with a TraceLog explainability comment; reads `code_artifacts`, `test_artifacts`, and `doc_artifacts` from `Run.state`; derives condition satisfaction from `conditions_satisfied` / `conditions_expected`; accepts `github_token` in request body or `GITHUB_TOKEN` env var
- **Exception hierarchy (`app/core/exceptions.py`)** — 9 domain exception classes (`RunNotFoundError`, `RunNotAccessibleError`, `RunNotRunningError`, `StateNotAvailableError`, `ReviewNotFoundError`, `WorkspaceNotFoundError`, `BudgetExceededError`, `InvalidTeamError`, `CompareNotFoundError`); all subclass `HTTPException` for transparent FastAPI handling; replace inline string-based error raises across `runs.py`, `engine.py`, `compare.py`, `reviews.py`
- **`py.typed` marker** declared in `antcrew-engine` — mypy/pyright now recognize type information when antcrew-platform imports from `antcrew_engine.*` directly (PEP 561)

### Tests
- 7 new tests for `POST /engine/runs/{id}/publish` (404 run not found, 422 non-engine run, 409 not complete, 422 missing token, 404 no artifacts, 200 success, 502 GitHub error)
- 14 new tests for `GitHubIntegration.create_engine_pr()` and `_build_engine_summary_comment()` in antcrew repo

---

## v0.4.0 (2026-07-20)

### Ola 1 — Client attribution + PR explainability

**Client label and per-workspace budget cap**
- `Run.client_label` — optional cost-center tag per run; accepted by `POST /run/` and `POST /engine/run`
- `GET /runs/?client_label=<str>` — filter runs by cost-center tag
- `Workspace.max_cost_usd` — hard budget cap; runs that would exceed it are rejected with 402
- `GET /workspaces/{id}/budget` — returns `total_cost_usd`, `max_cost_usd`, `remaining_usd`, `pct_used`

**GitHub PR explainability comment**
- Every PR created by `GitHubIntegration` auto-posts a structured comment: tickets resolved, code files changed, review verdict, PRD title, per-agent cost breakdown
- Comment is edited (not re-created) on re-run; idempotent

**Slack connectivity smoke-test**
- `POST /slack/test` — verify the Slack bot token can post a message without triggering a full run

---

### Ola 2 — Client reviewer role + HITL analytics

**Client reviewer role**
- New API key role: `reviewer` — read access to runs, tickets, and artifacts + HITL review actions; no write, no admin
- `GET /reviews/mine` — list reviews assigned to the authenticated reviewer
- `GET /reviews/token/{token}` — public review link via one-time token (no API key needed) for external stakeholders
- Per-review Slack DM routing based on `assignee` field in the review request

**HITL analytics**
- `GET /reviews/analytics` — aggregate metrics: pending count, overdue count, median and p95 resolution time, per-workspace breakdown
- `Review.resolved_at` timestamp set on approval/rejection for latency tracking
- `Review.overdue` computed property in list responses

---

### Ola 3 — Model diff, regression testing, and full engine visibility

**Model diff (`POST /run/compare`)**
- Run the same request against two LLM backends in parallel (e.g. `claude` vs `gpt-4o`)
- `GET /run/compare/{id}` — typed diff: `code_files`, `tickets`, `doc_files`, `test_files` each with `only_in_a / only_in_b / shared` sets; `summary.winner` on cost and latency
- `GET /run/compare` — paginated list of recent comparisons
- Engine support: `team: "engine"` + `goal: "..."` dispatches two `EngineLoop` runs and diffs their `ArtifactStore` output
- `CompareRun` DB row stores `team`, `request`/`goal`, model names, and run IDs for audit

**Prompt regression (`POST /evals/regression`)**
- Replay a list of historical run IDs with current prompts to detect quality regression before merging a prompt change
- Scores each replayed run against 80% tolerance thresholds (ticket count, code file count, review verdict similarity)
- `GET /evals/regression/{id}` — aggregate pass rate, `regression_rate`, per-run detail
- Regression runs are tagged with `regression_id` and surfaced in `GET /evals/`

**Engine artifact visibility**
- `_store_engine_state()` now serializes the full `ArtifactStore` content into `Run.state` for MemoryStore runs (previously all engine artifacts were discarded after each run)
- `GET /runs/{id}/artifacts` returns embedded `code_artifacts`, `test_artifacts`, `doc_artifacts` with full file content for MemoryStore engine runs
- `GET /runs/{id}/artifacts.zip` streams a ZIP from state-embedded artifacts for MemoryStore engine runs
- FilesystemStore runs (with `output_dir`) continue to serve artifacts from disk unchanged

**Engine condition progress (`GET /engine/runs/{id}/progress`)**
- New endpoint: condition satisfaction per engine run — `satisfied / pending / not_reached` for each goal condition
- Capability execution history: name, duration, cost, and produced artifact kinds per executed capability
- Unsatisfied conditions show `pending` while the run is in-flight; `not_reached` after completion

---

## v0.3.3 (2026-07-12)

### Visual pipeline builder
- Interactive SVG canvas for building custom pipelines (per-node model, HITL gate, Slack channel, max cost)
- `GET /pipelines/`, `POST /pipelines/`, `GET /pipelines/{id}`, `PUT /pipelines/{id}`, `DELETE /pipelines/{id}`
- `POST /pipelines/{id}/run` — trigger a run from a saved canvas definition
- Canvas state persisted to DB; zoom/pan preserved across sessions; run history sidebar per pipeline
- Dashboard badge showing the last pipeline run status
- `pipeline_id` field on `Run` rows dispatched via the builder

### Free trial credit
- New workspaces receive a configurable free trial credit (default: $5 USD) on creation
- `Workspace.trial_credit_usd`, `Workspace.trial_expires_at` — trial state tracked in DB
- Trial runs multiplied by a configurable markup factor before budget deduction
- `GET /workspaces/{id}/budget` includes `trial_balance_usd` separately from paid balance
- Trial management UI in workspace settings

### Multi-environment CI/CD
- Three Fly.io targets: `fly.int.toml`, `fly.uat.toml`, `fly.prod.toml`
- GitHub Actions gate: PROD deploy requires UAT integration test suite to pass first
- Alembic `migrate` step runs on every deploy via Fly.io release command
- `/docs` (Swagger UI) and `/redoc` disabled outside `APP_ENV=dev`

---

## v0.3.2 (2026-07-09)

### BYOK (Bring Your Own Key)
- Per-workspace LLM API key management: Anthropic, OpenAI, Groq, Gemini, Ollama, or any OpenAI-compatible endpoint
- Keys encrypted at rest with Fernet (`BYOK_ENCRYPTION_KEY` env var)
- `POST /workspaces/{id}/byok` — store or update a provider key
- `DELETE /workspaces/{id}/byok/{provider}` — remove a key
- `GET /workspaces/{id}/byok` — list configured providers (no key values returned)
- Runner picks up the workspace BYOK key automatically; falls back to server-level env vars
- IDOR fix: BYOK endpoints verify workspace membership before any key access

### Stripe / Lemon Squeezy billing
- Dual-lane MoR: Lemon Squeezy (EU/global) + Stripe (US)
- `POST /billing/stripe/webhook` and `POST /billing/lemonsqueezy/webhook` — signed webhook handlers
- `Workspace.plan` field (`free | trial | pro | enterprise`); budget caps enforced per plan
- `billing` optional dependency group: `pip install antcrew-platform[billing]`

### Landing page + settings
- Public landing page at `/`; dashboard moved to `/dashboard`
- `/settings` — workspace settings SPA: General, Reviewer config, LLM mode (BYOK vs server key)
- Pricing comparison widget: BYOK vs managed key cost per token per provider
- Multi-step onboarding wizard for new workspaces: name → LLM mode → first run

---

## v0.3.1 (2026-07-07)

### Security
- IDOR fix: BYOK endpoints now check workspace membership before any key access
- Timing-safe token comparison in WebSocket auth to prevent oracle attacks
- SSRF blocklist: outbound webhook delivery blocks RFC 1918 ranges (10.x, 172.16.x, 192.168.x) and loopback
- Startup warning: logs a prominent error when running in open mode (no API key configured)

### Engine improvements
- `engine_runner.py` updated to `antcrew-engine` v0.3.x API: `EngineLoop` constructor uses `max_tasks` and `parallel_workers` params
- `edit` verdict now correctly propagates the reviewer's modified content back into the engine's `ArtifactStore` before the loop continues
- Prompt caching enabled by default for engine runs (Anthropic beta header)

### Infrastructure
- PostgreSQL URL normalization: `postgresql://` rewritten to `postgresql+asyncpg://` on startup; `sslmode` stripped for Fly.io Postgres compatibility
- `POST /api-keys/` accessible in open mode to allow bootstrapping the first key

---

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
