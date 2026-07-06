#!/usr/bin/env python
"""CI migration consistency check.

Exits 0 if the database is at the latest Alembic head.
Exits 1 with a human-readable message if there are pending migrations.

Usage:
    python scripts/check_migrations.py
    # or via Makefile:
    make check-migrations

Requires the DATABASE_URL env var to be set (or defaults to sqlite:///platform.db).
"""
from __future__ import annotations

import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///platform.db")

# Alembic uses synchronous connections for the config lookup.
SYNC_URL = DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")


def main() -> int:
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        import sqlalchemy as sa
    except ImportError:
        print("ERROR: alembic and sqlalchemy must be installed.")
        return 1

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", SYNC_URL)
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())

    try:
        engine = sa.create_engine(SYNC_URL)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current = set(ctx.get_current_heads())
    except Exception as exc:
        print(f"ERROR connecting to database: {exc}")
        return 1

    if current == heads:
        print(f"OK  database is at head ({', '.join(sorted(heads))})")
        return 0

    pending = heads - current
    not_applied = []
    for rev in script.iterate_revisions("head", "base"):
        if rev.revision in pending:
            not_applied.append(f"  {rev.revision}: {rev.doc}")
    not_applied.reverse()

    print(f"FAIL  {len(pending)} pending migration(s) — run `make migrate` to apply:")
    for line in not_applied:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
