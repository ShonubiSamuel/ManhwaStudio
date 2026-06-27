"""
scripts/api/deps.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
FastAPI dependency providers.

Every router imports `get_db` from here and declares it as a FastAPI
Depends().  This gives every endpoint a shared Database instance without
passing it around manually or opening a new connection per request.

SQLite with WAL mode handles concurrent reads safely, so a single shared
instance is the right approach for a local desktop app.
"""

from __future__ import annotations

from database import Database
import config

# ── Shared Database instance ──────────────────────────────────────────────────

_db: Database | None = None


def get_db() -> Database:
    """
    Return the application-wide Database instance, creating it on first call.
    Used as a FastAPI dependency:  db: Database = Depends(get_db)
    """
    global _db
    if _db is None:
        _db = Database(str(config.DB_PATH))
    return _db
