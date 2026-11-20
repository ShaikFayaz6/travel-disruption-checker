"""Runs the SQL in supabase/migrations/ against DATABASE_URL (the same as pasting it into Supabase's SQL Editor).

    cd backend && python scripts/db_migrate.py      (or: python -m scripts.db_migrate)

Safe to run again: every statement checks whether its table, index or policy already exists.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `app` imports when run as a file

import asyncpg

from app.config import DATABASE_URL

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


async def main() -> None:
    if not DATABASE_URL:
        sys.exit("Set DATABASE_URL in backend/.env first (Supabase -> Connect -> Session pooler).")
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        for f in sorted(MIGRATIONS.glob("*.sql")):
            await conn.execute(f.read_text(encoding="utf-8"))
            print(f"Ran {f.name}")
        tables = await conn.fetch("select table_name from information_schema.tables "
                                  "where table_schema = 'public' and table_name in ('profiles','checks','stripe_events')")
        print("Tables ready:", ", ".join(sorted(r["table_name"] for r in tables)))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
