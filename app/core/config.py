"""Application configuration — environment and version constants."""
from __future__ import annotations

import os

_VALID_APP_ENVS = frozenset({"dev", "int", "uat", "prod"})

APP_ENV: str = os.environ.get("APP_ENV", "dev").lower()
VERSION = "0.5.0"

if APP_ENV not in _VALID_APP_ENVS:
    raise RuntimeError(
        f"APP_ENV={APP_ENV!r} is not valid. "
        f"Must be one of: {', '.join(sorted(_VALID_APP_ENVS))}. "
        "Example: APP_ENV=prod"
    )
