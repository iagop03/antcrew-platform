# Changelog — antcrew-platform

## v0.6.7 (2026-08-04)

### Added

- **GitHub App integration** — workspace admins can install the Antcrew GitHub App and link it to a workspace. Installation is stored in the new `github_installation` table (migration 050). Three new public routes (no API key): `GET /github/callback` (post-install OAuth redirect), `POST /webhooks/github` (HMAC-SHA256-verified lifecycle events). Two protected routes: `GET /github/installations`, `DELETE /github/installations/{id}`.

- **Write-back PR auto-open** — when a run with `write_back=true` has a linked GitHub App installation, the runner fetches a per-installation access token (RS256 JWT → GitHub token exchange), uses it for git authentication, and after a successful push automatically opens a pull request via `POST /repos/{owner}/{repo}/pulls`. The `pr_url` and `pr_number` are stored in `run.state["write_back_result"]` and surfaced as a clickable link in `run.html`.

- **`GET /github/installations`** accepts optional `workspace_id` query parameter to filter by workspace.

### Security

- **`WorkspaceJoinRequest.token` no longer stored in plaintext** (`app/models/auth.py`, `app/api/invites.py`) — join-request tokens are now stored as SHA-256 hashes (`token_hash`). The raw token is generated, embedded in admin approval/rejection email links, and immediately discarded. Approve/reject endpoints look up by hash first, with a plaintext fallback for legacy rows. Migration 051 adds the `token_hash` column (nullable, unique index) and makes the legacy `token` column nullable.

- **Dead endpoint eliminated** (`app/api/auth_session.py`) — the second `@router.patch("/profile")` handler (lines 1087–1128) was unreachable because FastAPI routes to the first matching path. Renamed to `@router.patch("/profile/onboarding")` so the onboarding wizard's `use_case` / `team_size` write actually reaches the handler. Frontend updated (`onboard.html`: `/auth/profile` → `/auth/profile/onboarding`).

- **Rate-limiter multi-worker warning** (`app/core/startup.py`) — startup now logs a warning when `ANTCREW_WORKERS > 1` and `RATE_LIMIT_RPM > 0`, because the in-memory limiter is per-process and the effective cap is `RPM × workers`. This makes the limitation visible in production logs without changing runtime behavior.

- **Workspace isolation on API-key endpoints** (`app/api/api_keys.py`, `app/api/pipeline.py`, `app/api/billing.py`, `app/api/workspaces_members.py`) — all handlers now inject `WorkspaceContext` and call `ws_accessible()` / `ws_filter()` to prevent cross-workspace data access via workspace-scoped API keys.

- **Session credential storage** (`app/api/auth_session.py`) — `register()` now stores only the `token_hash` via `_create_session()` and never writes a plaintext `UserSession` row. Password-change session revocation uses primary-key comparison (`id != current_id`) instead of a NULL-unsafe token-column comparison.

- **MFA secret guard** (`app/api/auth_session.py`) — `_sign_mfa_token()` raises `RuntimeError` immediately if `SECRET_KEY` is not set, preventing silent MFA bypass.

- **`verify_email` race-condition fix** (`app/api/auth_session.py`) — `verification is None` check now happens before any attribute access on the verification object.

### Fixed

- **Lint errors in `antcrew` package** — removed unused imports (`json`, `PRD`), added missing `DiscoveryContext` import in `discover_cmd.py`, fixed import ordering (I001) in `agents/ui_design.py` and `__init__.py`.

- **`test_pipeline.py`** — `mock_dispatch.assert_called_once_with(...)` assertion updated to include `write_back=False`.

---

## v0.6.6 (2026-08-04)

### Added

- **Conversational discovery** — full `/discovery/*` API (4 endpoints) and `discover.html` chat UI. A user answers 1–7 questions from the `DiscoveryAgent` and clicks "Finalizar y crear run" to start a pipeline run seeded with the gathered requirements. `DiscoverySession` model stored in `discovery_session` table (migration 049). Alpine.js chat interface with typing indicator, round progress bar, and team selector. "Discover" nav link added to all authenticated pages.

- **Brownfield write-back** — when a run is dispatched with `write_back=true` (field on `POST /run` and pipeline dispatcher), after the pipeline completes the platform: writes generated artifacts to the cloned repo using `antcrew.core.writeback.write_back()`, creates a branch `antcrew/wb-{run_id[:8]}`, commits, pushes to origin, and records the branch name + diff summary + push status in `run.state["write_back_result"]`. The "Write-back" card in `run.html` shows branch, diff summary, and a pushed/failed badge.

- **Activity tab** in run detail (`run.html`) — new "Activity" tab merges `Event` rows and `HitlAuditEntry` rows by timestamp for a given run. Each entry shows a type badge (`event` in indigo, `hitl` in amber), kind label, and a payload summary. Backend: `GET /runs/{run_id}/activity`.

- **WorkspaceMembership user_id** (migrations 047–048) — `workspace_membership` gains a `user_id INTEGER REFERENCES "user"(id)` column. Migration 047 adds it nullable and backfills via the `api_key` join; migration 048 enforces `NOT NULL` and adds a unique constraint on `(workspace_id, user_id)`. Auth paths now resolve memberships by both `api_key_id` and `user_id`.

### Changed

- `SECURITY.md` — prepended public vulnerability reporting policy (72-hour SLA, `security@antcrew.org`) and documented the intentional HMAC-SHA256-for-OTPs / bcrypt-for-passwords cryptographic choices.

### Deferred debt declared

- Discovery sessions do not yet expire; stale sessions accumulate indefinitely. A cleanup job (e.g. delete `status='active'` sessions older than 24 h) is tracked for the next maintenance release.

---

## v0.6.5 (2026-07-29)

### Security

- **SSRF in `slack_webhook_url` endpoint fixed** (`app/api/workspaces.py`) — `PUT /workspaces/{id}/slack` now calls `validate_external_url()` on any non-null `slack_webhook_url` before persisting it. Previously a workspace admin could store an SSRF URL (e.g. `http://169.254.169.254/...`) that the HITL listener would fetch on every approval notification. Fix: raises HTTP 400 with the validation error message; `null` (clearing the webhook) bypasses validation as intended.

- **CSRF protection extended to `PATCH /auth/profile`** (`app/api/auth_session.py`) — password changes were not protected by the double-submit CSRF check because `auth_session.router` was included globally without the `_csrf` dependency list. Fix: `require_csrf` imported from `app.core.csrf` and added as an explicit `Depends` on the `update_profile` endpoint only, leaving unauthenticated endpoints (`/register`, `/login`, `/verify-email`) correctly exempt. The `require_csrf` dependency is a no-op for API-key requests, so no breakage for programmatic clients.

- **Production Fly.io config locked to single instance** (`fly.prod.toml`) — changed `auto_start_machines` from `true` to `false`. With the in-memory rate limiter (`app/core/rate_limit.py`) and per-process `ThreadPoolExecutor` pools, a second Fly machine would have a separate rate-limit bucket and 4 additional worker slots — making both controls ineffective under burst traffic. `min_machines_running = 1` guarantees one machine is always warm; `auto_start_machines = false` prevents Fly from spawning a second instance that would violate the single-instance assumption until Redis-backed rate limiting is implemented.

### Deferred debt declared

- Rate limiting (`app/core/rate_limit.py`) uses in-memory token buckets — must be replaced with a Redis-backed implementation before horizontal scaling. Tracked explicitly; `auto_start_machines = false` is the interim guard.
- MFA (TOTP/passkeys) not yet implemented. Flagged for Q3 evaluation when the first enterprise customer formally requires it.

---

## v0.6.4 (2026-07-28)

### Added

- **Parallel node support in `pipeline_builder.py`** — nodes with `"type": "parallel"` and a `"members"` list now build a `ParallelGroup` (from antcrew 0.33.10). Each member gets its own LLM instance. Members must be registered agent types; custom `agent_cfg` inside parallel nodes is not supported in this version.

  JSON format:
  ```json
  {
    "id": "coding", "type": "parallel", "label": "Coding", "model": "claude",
    "members": [{"type": "backend_dev"}, {"type": "frontend_dev"}]
  }
  ```

### Requires

- `antcrew>=0.33.10` (for `ParallelGroup` with LLM race fix)

---

## v0.6.3 (2026-07-28)

### Security fixes

- **Password change now invalidates all other active sessions** (`auth_session.py`). Previously, changing a password updated only `user.password_hash` but left all other `UserSession` rows active — a stolen cookie remained valid for up to 30 days. Fix: after updating the hash, all `UserSession` rows for that user except the current one are marked `revoked=True`. Takes effect on the next request from any other session.

- **Security headers on all responses** (`main.py`). Added `_SecurityHeadersMiddleware` (Starlette `BaseHTTPMiddleware`) that injects `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, and `X-XSS-Protection: 0` on every response. `Strict-Transport-Security: max-age=31536000; includeSubDomains` is added in non-dev/int environments. Closes the most visible security header gap for enterprise evaluations.

---

## v0.6.2 (2026-07-28)

### Critical fix — engine runs were crashing on import

`app/services/engine_runner.py` imported `Operator` from `antcrew_engine.engine`. `Operator` was renamed to `EngineLoop` in `antcrew_engine` (same release that renamed `OperatorError → EngineLoopError`), but the platform consumer was not updated.

**Impact**: every engine pipeline run (`dispatch_engine` → `_run_engine_sync`) raised `ImportError` at function entry — before the `try/except` — so the error was never caught and every run silently failed at dispatch. Team-based runs (runner.py) were unaffected.

**Why tests didn't catch it**: all tests that touch `dispatch_engine` mock it completely (`patch("app.api.compare.dispatch_engine", new_callable=AsyncMock)`). The real `_run_engine_sync` body was never executed in CI.

**Fix**:
- `app/services/engine_runner.py`: import `EngineLoop`; instantiate as `EngineLoop`; update docstring.
- `tests/test_engine_artifacts.py`: `test_run_engine_sync_imports_engine_loop` — calls `_run_engine_sync` for real with `SequencedLLM` (no mock of the function body). If the import is wrong, the test raises `ImportError` instead of returning.

### Known technical debt (antcrew_engine internal imports)

Two imports in `engine_runner.py` use internal paths not exported in `antcrew_engine.__all__`:

- `from antcrew_engine.engine.events import HitlRequested, HitlResolved` (line ~141) — these are `Event` subclasses required by `EventLog.emit()`; the public `HitlRequestedPayload`/`HitlResolvedPayload` are different types used for the HITL review flow, not for event emission.
- `from antcrew_engine.capabilities.validators import artifact_validators` (line ~442) — convenience builder for `ArtifactExistsValidator` objects; neither the function nor the class is in `__all__`.

Both are functional today but fragile against internal refactors. Resolution requires upstreaming these to `antcrew_engine.__all__`; marked with inline comments pending that change.

---

## v0.6.1 (2026-07-28)

### Added

- **Alembic migration `031_custom_agents.py`** — creates the `custom_agent_def` table introduced in v0.6.0. Was missing from the v0.6.0 commit, making `CustomAgentDef` a PostgreSQL-only production blocker (SQLite fresh DBs used `create_all` and were unaffected). Migration follows the workspace-scoping pattern of the rest of the schema.

- **Agent palette role descriptions** — all 15 entries in `_AGENT_PALETTE` now carry `role_description` (a one-sentence summary of the agent's job). Surfaced in the editor via the tooltip popover on palette chips; also returned by `GET /pipelines/agents` for any consumer that wants to render agent metadata.

- **Edge condition autocomplete** — the "Condición del edge" modal now offers autocomplete suggestions via an HTML `<datalist>`. Suggestions are derived from `_TEMPLATES` at module load (`_KNOWN_CONDITIONS`) — single source of truth, no duplication — and exposed by a new `GET /pipelines/conditions` endpoint. Frontend calls `loadConditions()` on `init()` and populates `conditionSuggestions: []` state; the datalist is Alpine `x-for`-rendered.

---

## v0.6.0 (2026-07-28)

### Critical fix — custom agents now work in pipeline runs

Custom agents created via "Crear agente" previously failed silently at run time: `pipeline_builder.py` called `instantiate_agent("custom_N", …)`, got `None` (unregistered type), and raised `ValueError: Unknown agent type` — with no warning in the editor.

**Root cause**: the modal only captured label + color; the type was stored only in `localStorage`; neither reached the backend.

**Fix (3 layers)**:

1. **`CustomAgentDef` table** (`models/run.py`) — new workspace-scoped table storing `agent_type`, `label`, `color`, `system_prompt`, `role_description`, `phase`, `glyph`. Data survives cache clears and is shared across the team.

2. **`/pipelines/custom-agents` API** (`api/pipelines.py`) — `GET/POST/DELETE` endpoints, all scoped to `workspace_id` via `ws_accessible()`. `agent_type` is monotonically generated (`custom_N`), never reused after delete.

3. **`pipeline_builder.py` TemplateAgent fallback** — when `instantiate_agent()` returns `None`, the builder now checks `node.agent_cfg.system_prompt`; if present, it instantiates `TemplateAgent` instead of raising. The `agent_cfg` field is optional on nodes and fully backward-compatible with existing pipelines. New docstring section documents the extended node contract.

4. **`app/static/pipelines.html`** — "Crear agente" modal now requires a `system_prompt` textarea (validated as required, same as label). `saveCustomAgent()` calls `POST /pipelines/custom-agents` instead of `localStorage`. `removeCustomAgent()` calls `DELETE`. `loadCustomAgents()` is called on init (after workspaces are loaded). When dragging a custom agent onto the canvas, `agent_cfg` is embedded in the node so the pipeline definition is self-contained.

5. **`tests/test_pipeline.py`** — three new unit tests: known type succeeds, unknown type without `agent_cfg` still raises `ValueError`, unknown type with valid `agent_cfg.system_prompt` produces a `TemplateAgent`.

### Added

- **Custom agent tooltips** — hovering a palette chip shows a popover with role description and, for custom agents, the first 180 chars of the system prompt.
- **Confirmation modal** — all 6 `confirm()`/`alert()` calls replaced with a consistent modal (`_showConfirm(msg, fn)` + `_showToast(msg)`). No more browser dialogs.
- **isDirty flag + "Descartar cambios"** — `_autoSave()` sets `isDirty = true`; `savePipeline()` success calls `_markSaved()` (sets `isDirty = false`); `loadPipeline()` calls `_markSaved()` after loading. An amber "● sin guardar" indicator appears in the toolbar when dirty. "Descartar" button re-fetches from the server. `beforeunload` warns if the user tries to close the tab with unsaved changes. The localStorage draft does NOT clear `isDirty` — it's a local copy, not a server save.
- **Edge endpoint reconnection** — clicking an edge selects it and shows two indigo handles (⬤) at the from and to endpoints. Dragging a handle to another node or port updates the edge's from/to/fromPort/toPort with live preview (reuses `tempEdgePath`). Escape or clicking empty canvas deselects.
- **Edge selection state** (`selectedEdgeId`) — tracked in JS state; cleared on Escape, canvas click, and `loadPipeline()`.

### Known technical debt (unchanged)
- **Accessibility** — still deferred (see v0.5.1 note).
- **Run pause controls** — deliberately excluded. `ManualActionCapability` is designed for user-defined action items (blocking tickets), not ad-hoc mid-run pause. `antcrew replay` is a CLI checkpoint tool; the platform doesn't use `SqliteSaver`. The existing HITL node flag (`hitl: true`) already covers "pause and wait for human approval at a node". A general pause API endpoint is a separate deliverable requiring a new run status ("paused") and resume endpoint.

---

## v0.5.1 (2026-07-28)

### Added — Pipeline editor redesign (phases 1 + 2)

#### Phase 1 (UX baseline)
- **4-port connection system** — arrows can originate from any of the 4 cardinal port handles (N/E/S/W) instead of fixed left/right. Stored as `fromPort`/`toPort` on edges; existing edges without these fields default to E/W for backward compatibility.
- **Undo/redo** — Ctrl+Z / Ctrl+Y up to 60 history snapshots (JSON diff of `{nodes, edges}`).
- **Comment nodes** — type `"comment"`, rendered as yellow dashed sticky-note, skipped by the builder, double-click to edit text. Ctrl+M to add.
- **Save-as dialog** — opening a built-in template now shows "Guardar como…" instead of "Guardar", cloning it into a user pipeline before saving.
- **Autosave draft** — every mutation writes `{nodes, edges, zoom, pan}` to `localStorage` (debounced 600ms). On next load, an amber banner offers to restore.
- **Snap-to-grid** — 24px alignment toggle (Ctrl+G / ⊞ button).
- **Duplicate node** — Ctrl+D copies the selected node with a 24px offset.

#### Phase 2 (symbology + interaction)
- **5-phase symbology** — all 15 agent types grouped into Discovery / Planning / Build / Quality / Delivery, each with a Unicode glyph (`⊙ ≡ </> ✓ ⇧`) rendered as SVG text inside nodes. Color and glyph sourced from `_AGENT_PALETTE` in `pipelines.py` (single source); frontend builds a `_paletteMap` from the `/pipelines/agents` response.
- **Extended arrow language** — four visually distinct edge types: normal (slate), conditional-if (green solid), conditional-else (amber dashed), HITL-gate (gold + ⏸ badge). Else classification uses an explicit `is_else: bool` field on the edge (visual-only; ignored by `pipeline_builder.py`), with a substring heuristic as fallback for existing edges.
- **Entry / terminal node marks** — auto-detected (0 in-edges = entry → left triangle + dashed outer ring; 0 out-edges = terminal → bottom bar). No schema change.
- **Canvas validation badges** — `⟲ ciclo` (red) and `⚠ huérfano` (amber) badges appear directly on problem nodes after each render.
- **Multi-select** — Shift+drag on empty canvas opens a marquee; Shift+click toggles a node; Ctrl+A selects all. Dragging a selected node moves all selected nodes together. Delete clears selection.
- **Auto-layout** (`⊟` / Ctrl+L) — topological Sugiyama-lite in ~40 lines of vanilla JS; no external library.
- **Zoom-to-fit** (`⤢` / Ctrl+F) — fits all nodes into the viewport with 60px padding. Also called automatically when loading a pipeline.
- **Scroll-wheel zoom** — zooms toward the cursor.
- **Minimap** — 156×96px secondary SVG in the bottom-right corner with a viewport indicator.
- **Collapsible legend** — bottom-left overlay listing phases and arrow types.
- **Run modal model list** — now driven by the same `PROVIDERS` constant used by the per-node model selector; eliminates the previously hardcoded subset of 4 models.

### Changed
- `_AGENT_PALETTE` in `pipelines.py` enriched with `phase` and `glyph` fields; endpoint `/pipelines/agents` returns them.
- `reviewer` color `#d97706` → `#059669` (Quality phase). Amber is now reserved for else/fix edges.
- `codebase_scanner`, `idea` colors `#6b7280` / `#ec4899` → `#7c3aed` (Discovery phase).
- `copywriter` color `#ec4899` → `#0891b2` (Build phase).
- `editor` color `#ec4899` → `#059669` (Quality phase).
- `doc_writer` color `#6b7280` → `#dc2626` (Delivery phase).
- Autosave is now debounced (600ms) instead of firing synchronously on every mutation.

### Architecture note (SVG-imperative vs diagram library)
The pipeline canvas deliberately stays with Alpine.js + imperative SVG (no build step, no extra bundle). The decision was evaluated before phase 2 against React Flow / Svelte Flow: both require a bundler, which violates the project's CDN-only constraint. All phase-2 features (multi-select, auto-layout, minimap) were implemented in vanilla JS at a cost of ~700 LOC. The tradeoff is increased DOM-juggling complexity (`renderCanvas()` / `_renderNodes()` / `_renderEdges()` are now ~350 LOC combined). If the canvas grows beyond ~5 interactive features more, migrating to a diagram library with a build step would become the better call.

### Known technical debt
- **Accessibility** — the canvas has no ARIA roles, no `tabindex` on nodes/edges, and no keyboard graph navigation. A screen-reader user or someone without a pointer device cannot operate the editor today. Deferred consciously; would require either ARIA live-region updates on every render or a separate accessible tree mirroring the graph structure.

---

## v0.5.0 (2026-07-27)

### Added
- **Proxy LLM key mode** — third billing tier (×0.7) that lets customers hold their own LLM API keys without ever exposing them to the platform.
  - Platform generates a UUID token per workspace, stores it encrypted with the BYOK key; the customer runs `antcrew-proxy` (Docker) with their real provider keys and the UUID token.
  - On every LLM call the platform sends the UUID token to the proxy instead of calling the provider directly. The proxy validates the token (constant-time `hmac.compare_digest`), substitutes the real API key, and forwards the request. Platform never touches provider keys.
  - **Multi-provider routing** by URL path prefix: `/anthropic/…` → `api.anthropic.com`, `/openai/…` → `api.openai.com`, `/groq/…` → `api.groq.com/openai`, `/gemini/…` → `generativelanguage.googleapis.com/v1beta/openai`. Full streaming (SSE) passthrough via `aiter_raw()`.
  - **`resolve_workspace_llm_config(session, workspace, model)`** in `runner_base.py` — canonical three-way dispatch for managed / BYOK / proxy, used by both `runner.py` and `engine_runner.py`.
  - **Proxy API**: `GET /workspaces/{id}/proxy` (status), `POST /workspaces/{id}/proxy/generate` (generate/rotate token + docker command), `POST /workspaces/{id}/proxy/activate` (switch mode), `DELETE /workspaces/{id}/proxy` (revoke, revert to managed).
  - **Settings UI** — violet Proxy ×0.7 button, token generate/reveal panel (shown once), docker run command in `<details>`, revoke button; workspace table badge shows violet for proxy-mode workspaces.
  - Migration 030: adds `proxy_url TEXT NULL` and `proxy_token_enc TEXT NULL` to `workspace`.
  - `_check_proxy_config()` lifespan guard — warns/blocks at startup if proxy-mode workspaces exist without `BYOK_ENCRYPTION_KEY`.
  - **`antcrew-proxy`** open-source companion repo published at `github.com/iagop03/antcrew-proxy` with GitHub Actions CI pushing to `ghcr.io/iagop03/antcrew-proxy` (linux/amd64 + arm64).

### Changed
- `Workspace.llm_key_mode` now accepts `"proxy"` in addition to `"managed"` and `"byok"`.
- `get_cost_multiplier()` returns `0.7` for proxy mode.
- `_check_workspace_budget` and `_mark_workspace_budget_status` are now in `runner_base.py`; both runners import from there (eliminates the cross-module private import).

---

## v0.4.9 (2026-07-26)

### Added
- **Email verification** — `POST /auth/register` now creates a 6-digit `EmailVerification` code and sends it via `send_verification_code`. Users verify with `POST /auth/verify-email {code}` or request a new code via `POST /auth/resend-code`. `User.email_verified_at` tracks when verification occurred.
- **Workspace invites** — admins send email invites via `POST /workspaces/{id}/invites {email, role}`. Recipients with an active session accept via `POST /auth/accept-invite {token}`, which creates a `WorkspaceMembership` and emails a confirmation. Invites expire after 7 days.
- **Join requests** — authenticated users can request access to a workspace by slug: `POST /workspaces/join-request {workspace_slug, requested_role}`. The workspace admin receives an approve/reject email with one-click URLs backed by `POST /join-requests/{token}/approve` and `/reject`.
- **`require_verified_session`** — FastAPI dependency that blocks unverified session-cookie users (API-key callers pass through). Applied to invite-send and join-request endpoints.
- **Email templates** (`app/services/email.py`): `send_verification_code`, `send_workspace_invite`, `send_join_request`, `send_join_approved`, `send_join_rejected`, `_dispatch` (centralised SMTP boilerplate).
- **XSS fix** — `send_review_assigned` now escapes `assignee_label`, `agent_name`, `run_id`, and `review_id` through `html.escape()` before interpolating into the HTML body.
- Migration 028: adds `email_verification`, `workspace_invite`, `workspace_join_request` tables and `user.email_verified_at` column.

---

## v0.4.8 (2026-07-26)

### Added
- **Blocking manual-action tickets** — pipelines can now pause mid-execution waiting for a human to complete a step (configure credentials, run a command, make a decision). Three ways to trigger: (1) `ManualActionCapability` in an engine run (set `manual_action_done` in conditions); (2) `POST /tickets/` with `ticket_type="manual_action"`; (3) SecurityAuditRun creating a blocking ticket for a critical finding that requires manual remediation
- **`Ticket.ticket_type`** — new field (`task | manual_action | bug`), default `"task"`
- **`Ticket.blocking`** — when `True`, blocks the associated run; the run status becomes `"blocked"`
- **`Ticket.assignee`** — email of the human responsible for completing the step
- **`Run.status = "blocked"`** — new valid status; run is suspended waiting for a blocking ticket to be resolved
- **`PATCH /tickets/{ticket_id}/status → done`** — automatically unblocks the run (sets status back to `"running"`) and wakes up the blocked engine thread via `resolve_manual_action(ticket_id)`
- **`GET /runs/{run_id}/blocking-tickets`** — list open blocking tickets for a run with a single API call
- **`POST /runs/{run_id}/unblock`** (admin) — force-unblock a run by marking all blocking tickets done; useful for recovering from stuck pipelines
- **`ManualActionCapability`** (antcrew-engine 0.3.9) — engine capability that pauses the pipeline; activated by adding `"manual_action_done"` to the engine run conditions
- Migration 027: adds `ticket_type`, `blocking`, `assignee` columns to `ticket` table

---

## v0.4.7 (2026-07-26)

### Added
- **SecurityAuditor — LLM-based cross-file security audit** (`app/api/security_audit.py`, migration 026).
  Three independent trigger modes per workspace: manual (`POST /security/runs/trigger`), GitHub push webhook (`POST /security/webhook/github`, HMAC-verified), and scheduled (cron expression via `schedule_cron`).

  Two-phase LLM audit: Phase 1 builds a catalog of established defensive controls in the repo; Phase 2 checks every equivalent code path for consistency gaps and classic anti-patterns (SSRF, path traversal, CSRF, fail-open auth, hardcoded secrets, IDOR, …). The cross-file consistency check is the main differentiator over bandit — an LLM with full-repo context maintains the catalog and checks every surface against it.

  **FindingsTriager**: critical/high findings create a Ticket + HitlReview (human approves fix before merge); medium/low create a Ticket (BugFixer can auto-fix; human reviews PR).

  **Convergence loop**: after each run, automatically triggers the next iteration (diff-mode) unless: two consecutive runs produce zero net-new findings at `min_severity_to_stop` (converged), `max_iterations` is reached, or cumulative cost exceeds `max_cost_usd`. Stops and reports — never loops silently.

  `AuditFinding` carries a `fingerprint` (sha256[:16]) for dedup across iterations and a `reference_fix` field pointing to the existing fix pattern in the repo that BugFixer should replicate.

  New models: `SecurityAuditConfig`, `SecurityAuditRun`, `AuditFinding`.
  `AsyncSessionFactory` added to `app/core/database.py` for background-task DB access.

---

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
