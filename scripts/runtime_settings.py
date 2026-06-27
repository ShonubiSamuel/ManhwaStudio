"""
runtime_settings.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Resolve user-tunable settings from the DB settings table, falling back to the
config.py default.

config.py holds DEFAULTS; the Settings tab writes overrides into the DB. Modules
that read these values often have no `db` handle (e.g. audio_utils, translator,
dots_backend), so they read through here:

    import runtime_settings as rs
    cap = rs.get_float("dub_hard_stretch", config.DUB_HARD_STRETCH)

Values are cached briefly (settings change rarely) so hot paths like per-panel
stretching don't hit SQLite on every call. The Settings PATCH endpoint calls
invalidate() so edits take effect immediately.
"""

from __future__ import annotations

import threading
import time

import config

_cache: dict   = {}
_loaded_at: float = 0.0
_lock = threading.Lock()
_TTL  = 1.5   # seconds


def _refresh() -> None:
    global _cache, _loaded_at
    try:
        from database import Database
        _cache = Database(str(config.DB_PATH)).get_all_settings() or {}
    except Exception:
        _cache = {}
    _loaded_at = time.time()


def _raw(key: str):
    global _loaded_at
    with _lock:
        if time.time() - _loaded_at > _TTL:
            _refresh()
        return _cache.get(key)


def invalidate() -> None:
    """Force the next read to reload from the DB (call after writing settings)."""
    global _loaded_at
    with _lock:
        _loaded_at = 0.0


# ── Typed getters (values are stored as JSON; usually strings from the UI) ─────

def get_str(key: str, default: str = "") -> str:
    v = _raw(key)
    return default if v is None or v == "" else str(v)


def get_float(key: str, default: float) -> float:
    v = _raw(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def get_int(key: str, default: int) -> int:
    v = _raw(key)
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return int(default)


def get_bool(key: str, default: bool) -> bool:
    v = _raw(key)
    if v is None or v == "":
        return bool(default)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")
