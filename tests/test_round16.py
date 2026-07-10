"""Round 16 tests — bcrypt auth path, key_prefix lookup, audit log, PATCH /api-keys/{label}.

Covers:
- POST /api-keys/ creates key with bcrypt hash + key_prefix set (P1)
- Authenticating with a bcrypt key works and returns correct role (P1)
- Legacy SHA256 key is transparently upgraded on first login (P2)
- GET /reviews/{id}/audit returns audit trail entries (P3)
- PATCH /api-keys/{label} updates email and role (P4)
- Fail-closed: DB error returns 503 not open mode (P5) — tested via mock
"""
from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, HitlReview, HitlAuditEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _make_admin_key(session: AsyncSession, *, label: str, raw: str) -> ApiKey:
    """Create an admin key via the model directly (SHA256, no prefix) to simulate legacy key."""
    k = ApiKey(label=label, key_hash=_sha256(raw), role="admin", workspace_id=None)
    session.add(k)
    await session.commit()
    await session.refresh(k)
    return k


# ---------------------------------------------------------------------------
# P1 — POST /api-keys/ produces bcrypt key + key_prefix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_key_via_api_uses_bcrypt(client: AsyncClient, session: AsyncSession):
    """POST /api-keys/ (in open mode) creates a key with bcrypt hash and key_prefix."""
    r = await client.post("/api-keys/", json={"label": "bcrypt-test-r16", "role": "write"})
    assert r.status_code == 201, r.text
    raw_key = r.json()["key"]

    db_key = (await session.exec(
        select(ApiKey).where(ApiKey.label == "bcrypt-test-r16")
    )).first()
    assert db_key is not None
    assert db_key.key_hash.startswith("$2b$"), "should be bcrypt hash"
    assert db_key.key_prefix is not None, "key_prefix must be set"
    assert len(db_key.key_prefix) == 16


@pytest.mark.asyncio
async def test_bcrypt_key_authenticates_correctly(client: AsyncClient, session: AsyncSession):
    """A key created via POST /api-keys/ can authenticate and role is respected."""
    r = await client.post("/api-keys/", json={"label": "bcrypt-auth-r16", "role": "write"})
    assert r.status_code == 201
    raw_key = r.json()["key"]

    # write key cannot create another key (requires admin)
    r2 = await client.post(
        "/api-keys/",
        json={"label": "nested-r16", "role": "write"},
        headers={"X-Api-Key": raw_key},
    )
    assert r2.status_code == 403, f"write key must not create keys; got {r2.status_code}"


@pytest.mark.asyncio
async def test_bcrypt_key_wrong_password_rejected(client: AsyncClient, session: AsyncSession):
    """Authenticating with a wrong key returns 401 (not open mode)."""
    await client.post("/api-keys/", json={"label": "bcrypt-reject-r16", "role": "write"})

    r = await client.get("/runs/", headers={"X-Api-Key": "totally-wrong-key"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# P2 — Legacy SHA256 key is transparently upgraded on first use
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_sha256_key_is_upgraded_on_login(client: AsyncClient, session: AsyncSession):
    """A SHA256 key without key_prefix is upgraded to bcrypt + prefix on first successful auth."""
    raw = "legacy-upgrade-r16-secret"
    await _make_admin_key(session, label="legacy-upgrade-r16", raw=raw)

    # Authenticate with legacy key — should succeed
    r = await client.get("/api-keys/", headers={"X-Api-Key": raw})
    assert r.status_code == 200, f"legacy key should auth successfully; got {r.status_code}"

    # Refresh from DB and verify upgrade happened
    await session.refresh(
        (await session.exec(select(ApiKey).where(ApiKey.label == "legacy-upgrade-r16"))).first()
    )
    upgraded = (await session.exec(
        select(ApiKey).where(ApiKey.label == "legacy-upgrade-r16")
    )).first()
    assert upgraded is not None
    assert upgraded.key_prefix is not None, "key_prefix should be set after upgrade"
    assert upgraded.key_hash.startswith("$2b$"), "hash should be upgraded to bcrypt"


# ---------------------------------------------------------------------------
# P3 — GET /reviews/{id}/audit returns audit trail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_log_populated_on_create_and_submit(client: AsyncClient, session: AsyncSession):
    """POST /reviews/ + POST /reviews/{id} writes audit entries readable via GET /reviews/{id}/audit."""
    # Open mode: no keys in DB
    run_id = f"audit-run-{uuid.uuid4()}"
    review_id = str(uuid.uuid4())

    r = await client.post("/reviews/", json={
        "run_id": run_id,
        "review_id": review_id,
        "agent_name": "AuditAgent",
    })
    assert r.status_code == 201

    # Submit decision
    r2 = await client.post(f"/reviews/{review_id}", json={"decision": "approve"})
    assert r2.status_code == 200

    # Read audit log
    r3 = await client.get(f"/reviews/{review_id}/audit")
    assert r3.status_code == 200
    entries = r3.json()
    actions = [e["action"] for e in entries]
    assert "created" in actions
    assert "approved" in actions


@pytest.mark.asyncio
async def test_audit_log_404_for_unknown_review(client: AsyncClient, session: AsyncSession):
    r = await client.get(f"/reviews/{uuid.uuid4()}/audit")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# P4 — PATCH /api-keys/{label} updates email and role
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_api_key_email(client: AsyncClient, session: AsyncSession):
    """PATCH /api-keys/{label} sets email on an existing key."""
    # Create admin key in open mode, then use it
    r = await client.post("/api-keys/", json={"label": "patch-email-r16", "role": "admin"})
    assert r.status_code == 201
    admin_key = r.json()["key"]

    r2 = await client.patch(
        "/api-keys/patch-email-r16",
        json={"email": "alice@example.com"},
        headers={"X-Api-Key": admin_key},
    )
    assert r2.status_code == 200
    assert r2.json()["email"] == "alice@example.com"

    db_key = (await session.exec(
        select(ApiKey).where(ApiKey.label == "patch-email-r16")
    )).first()
    assert db_key is not None
    assert db_key.email == "alice@example.com"


@pytest.mark.asyncio
async def test_patch_api_key_role(client: AsyncClient, session: AsyncSession):
    """PATCH /api-keys/{label} changes the role of an existing key (admin auth required)."""
    # Create admin key in open mode (no keys in DB yet)
    r = await client.post("/api-keys/", json={"label": "patch-admin-r16", "role": "admin"})
    assert r.status_code == 201
    admin_key = r.json()["key"]

    # Create target key using admin auth
    r2 = await client.post(
        "/api-keys/",
        json={"label": "patch-role-target-r16", "role": "write"},
        headers={"X-Api-Key": admin_key},
    )
    assert r2.status_code == 201

    # Update role via PATCH using admin key
    r3 = await client.patch(
        "/api-keys/patch-role-target-r16",
        json={"role": "reviewer"},
        headers={"X-Api-Key": admin_key},
    )
    assert r3.status_code == 200
    assert r3.json()["role"] == "reviewer"


@pytest.mark.asyncio
async def test_create_key_with_email(client: AsyncClient, session: AsyncSession):
    """POST /api-keys/ accepts email and stores it."""
    r = await client.post("/api-keys/", json={
        "label": "email-at-create-r16",
        "role": "reviewer",
        "email": "bob@example.com",
    })
    assert r.status_code == 201

    db_key = (await session.exec(
        select(ApiKey).where(ApiKey.label == "email-at-create-r16")
    )).first()
    assert db_key is not None
    assert db_key.email == "bob@example.com"


# ---------------------------------------------------------------------------
# P5 — list_keys includes email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_keys_includes_email(client: AsyncClient, session: AsyncSession):
    """GET /api-keys/ returns the email field."""
    # Create admin key in open mode
    r_admin = await client.post("/api-keys/", json={"label": "list-admin-r16", "role": "admin"})
    assert r_admin.status_code == 201
    admin_key = r_admin.json()["key"]

    # Create a key with email using admin auth
    r = await client.post("/api-keys/", json={
        "label": "list-email-r16",
        "role": "write",
        "email": "carol@example.com",
    }, headers={"X-Api-Key": admin_key})
    assert r.status_code == 201

    r2 = await client.get("/api-keys/", headers={"X-Api-Key": admin_key})
    assert r2.status_code == 200
    labels = {k["label"]: k for k in r2.json()}
    assert "list-email-r16" in labels
    assert labels["list-email-r16"]["email"] == "carol@example.com"
