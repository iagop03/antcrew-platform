# Security Checklist

This file documents the security patterns established across the antcrew stack.
Use it as a PR checklist when touching any of the areas below.
Each pattern has a canonical reference implementation — follow it exactly rather than reimplementing.

---

## PR checklist

Before merging a PR that touches any of the following surfaces, verify each applicable item.

### 1. New secret or credential at startup

> Pattern: `_check_<name>()` in `app/main.py`, called from `lifespan()`.
> Reference: `_check_stripe_config()`, `_check_slack_config()`.

- [ ] If the secret is absent or invalid on a public host, does the server **block startup** with `RuntimeError`?
- [ ] On localhost, does it **warn** instead of blocking?
- [ ] Is the check a **no-op** when the feature is not configured at all?
- [ ] Is the new `_check_*` function called in `lifespan()` before `start_listening()`?

Covered so far: `STRIPE_WEBHOOK_SECRET`, `SLACK_TOKEN_ENCRYPTION_KEY`, `ANTCREW_REQUIRE_AUTH`,
`DATABASE_URL` (no SQLite on public host), `ANTCREW_SANDBOX`, `CORS_ORIGINS`.

---

### 2. New write endpoint on workspace-scoped data

> Pattern: `ws_accessible(target_id, ctx)` before touching any row.
> Reference: `get_review()` and `create_review()` in `app/api/reviews.py`.

- [ ] Does the endpoint call `ws_accessible()` on every workspace_id that comes from the **request body** (not just from ctx)?
- [ ] Is the check **identical** to the corresponding read endpoint?
- [ ] Does a 403 fire — not silently clip to ctx.workspace_id — when access is denied?

---

### 3. New outbound HTTP request

> Pattern: `validate_external_url(url)` before the request.
> Reference: `app/core/security.py`, used in `runner._inject_repo_context()` and `webhook._process_pending()`.

- [ ] Does the URL pass through `validate_external_url()` before any HTTP call is made?
- [ ] If the URL is stored in the DB (e.g. webhook config), is it validated **at creation time** (fail-fast) AND **at use time** (defense in depth)?
- [ ] Does `validate_external_url()` cover the right scheme? (`allow_http=False` for repo clones, `allow_http=True` for webhook delivery.)

---

### 4. New filesystem write from untrusted input

> Pattern: `_safe_path(rel)` using `Path.is_relative_to()`, never `str.startswith()`.
> Reference: `FilesystemStore._safe_path()` in `antcrew-engine/engine/store.py`
>            and `WriteFileTool._safe_path()` in `antcrew/core/tools.py`.

- [ ] Is the path validated with `is_relative_to(root)` — not `startswith(str(root))`?
  (`startswith` allows `/x/project-evil/` to bypass a root of `/x/project`.)
- [ ] Is an absolute path explicitly rejected when `root=None` (no scope configured)?
- [ ] Does the check run **before** `mkdir` and **before** `write_text`?

---

### 5. New string comparison involving a secret

> Pattern: `hmac.compare_digest(a, b)` — never `==` or `!=` on secret values.
> Reference: `get_workspace_context()` and `_verify()` in `app/core/auth.py`.

- [ ] Are all comparisons that involve API keys, tokens, or hashes using `hmac.compare_digest()`?
- [ ] If one side can be `None`, is it coerced to `""` first? (`hmac.compare_digest(value or "", secret)`)

---

### 6. New code execution surface

> Pattern: sandbox via Docker, or refuse with clear error if Docker unavailable.
> Reference: `antcrew-engine/engine/sandbox.py` and `CodeExecutorTool` in `antcrew/core/tools.py`.

- [ ] Does execution go through `sandbox.run()` (Docker-backed), not a raw `subprocess.run()`?
- [ ] If direct subprocess is unavoidable, is `ANTCREW_SANDBOX=required` checked and the call refused?
- [ ] Is the subprocess environment stripped to a safe allowlist (no API keys, no DB URLs)?
- [ ] Is the Docker image pinned via `ANTCREW_SANDBOX_IMAGE` in production, not floating on a mutable tag?

---

### 7. New chat or messaging integration

> Principle: integrations connect **outward** (polling / socket mode).
> Reference: Slack Socket Mode (`app/core/slack_hitl.py`), Telegram polling.

- [ ] Does the integration initiate the connection outward (polling or long-lived socket)?
- [ ] If it receives inbound HTTP webhooks instead: is **every** request signature-verified before processing? (No verification = anyone can forge events.)
- [ ] Is the bot token encrypted at rest using Fernet + an env-var key?

---

### 8. New dependency on an external image or package

> Pattern: pin to an immutable reference; update deliberately, not silently.

- [ ] For Docker images: is the image pinned to a `@sha256:<digest>` in production
  (via `ANTCREW_SANDBOX_IMAGE`)? Is there a note on how to refresh the pin?
- [ ] For Python packages: is the version range in `pyproject.toml` tight enough to prevent silent upgrades to a breaking or malicious version?

---

## Quick reference: where each guard lives

| Guard | File | Function / class |
|---|---|---|
| Startup secret checks | `app/main.py` | `_check_*()` + `lifespan()` |
| Workspace access scoping | `app/core/auth.py` | `ws_accessible()`, `ws_filter()` |
| Outbound URL validation | `app/core/security.py` | `validate_external_url()` |
| Path traversal prevention | `antcrew-engine/engine/store.py` | `FilesystemStore._safe_path()` |
| Path traversal (tools) | `antcrew/core/tools.py` | `WriteFileTool._safe_path()` |
| Constant-time comparison | `app/core/auth.py` | `_verify()`, `get_workspace_context()` |
| Code execution sandbox | `antcrew-engine/engine/sandbox.py` | `run()`, `run_with_install()` |
| Docker image override | `antcrew-engine/engine/sandbox.py` | `_docker_image()` + `ANTCREW_SANDBOX_IMAGE` |
| bcrypt key hashing | `app/core/auth.py` | `_hash()`, `_verify()`, `_key_prefix()` |
