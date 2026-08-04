"""GitHub App JWT + installation token exchange."""
from __future__ import annotations

import os
import time

import httpx

_APP_ID = os.environ.get("GITHUB_APP_ID", "")
_PRIVATE_KEY_RAW = os.environ.get("GITHUB_PRIVATE_KEY", "")


def _private_key() -> str:
    """Return the PEM private key, normalising literal \\n sequences."""
    return _PRIVATE_KEY_RAW.replace("\\n", "\n")


def _make_app_jwt() -> str:
    """RS256 JWT valid for 10 minutes, signed with the app private key."""
    try:
        import jwt as pyjwt
    except ImportError:
        raise RuntimeError(
            "PyJWT[cryptography] not installed. "
            "Add: pip install 'PyJWT[cryptography]'"
        )
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": _APP_ID}
    return pyjwt.encode(payload, _private_key(), algorithm="RS256")


async def get_installation_token(installation_id: int) -> str:
    """Exchange app JWT for a scoped installation access token (valid ~1 h)."""
    app_jwt = _make_app_jwt()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        r.raise_for_status()
        return r.json()["token"]


async def create_pull_request(
    installation_id: int,
    repo_full_name: str,  # "owner/repo"
    head: str,
    base: str = "main",
    title: str = "",
    body: str = "",
) -> dict:
    """Create a PR using an installation token. Returns the PR JSON."""
    token = await get_installation_token(installation_id)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"https://api.github.com/repos/{repo_full_name}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"title": title, "body": body, "head": head, "base": base},
        )
        r.raise_for_status()
        return r.json()
