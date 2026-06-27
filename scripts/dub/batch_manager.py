"""
dub/batch_manager.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Batch state file operations for the dubbing pipeline.

Extracted from DubEngine._load_batch_state / ._save_batch_state.
These were private static methods — moved here as module-level functions
so they can be imported and tested independently.

The batch state is a JSON file at:
    {episode.output_folder}/dub/batch_state.json

Structure
─────────
{
  "_schema_version": 1,
  "batch_size": 5,
  "en": {
    "profile": "Adam_en",
    "batches": [
      {
        "idx":        0,
        "panels":     [0, 1, 2, 3, 4],
        "panel_from": 0,
        "panel_to":   4,
        "audio_path": "/path/batch_0000.wav",
        "status":     "done",
        "duration":   12.4,
        "created_at": 1718000000.0
      },
      ...
    ]
  },
  "zh": { ... }
}

Schema versioning
─────────────────
  BATCH_STATE_VERSION is the single source of truth for the current schema.
  Increment it whenever the state structure changes in a backward-incompatible
  way.  load_batch_state() rejects files written by a different version so
  DubEngine starts a clean batch rather than partially applying a stale or
  incompatible state.
"""

from __future__ import annotations

import json
from pathlib import Path


# ── Schema version ────────────────────────────────────────────────────────────
# Increment this when the batch state structure changes in a way that makes
# old state files incompatible (e.g. renamed keys, restructured nesting).
# load_batch_state() compares this value against the stored "_schema_version"
# field and returns {} if they differ, causing DubEngine to rebuild the state
# from scratch rather than resuming from a stale or mismatched file.

BATCH_STATE_VERSION: int = 1


def load_batch_state(path: Path) -> dict:
    """
    Load the batch state from disk.

    Returns an empty dict if the file does not exist, cannot be parsed, or
    was written by a different schema version.  Returning {} causes DubEngine
    to start the batch fresh — always safe, never corrupt.

    Schema mismatch is printed to stdout so it is visible in the Logs tab
    without requiring a log callback parameter.
    """
    if not path.exists():
        return {}
    try:
        data    = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("_schema_version", 0)
        if version != BATCH_STATE_VERSION:
            print(
                f"[batch_manager] {path.name}: schema version {version} does not "
                f"match current version {BATCH_STATE_VERSION} — "
                f"discarding stale state and starting fresh"
            )
            return {}
        return data
    except Exception:
        return {}


def save_batch_state(path: Path, state: dict):
    """
    Write the batch state to disk atomically using a temp-file-then-rename
    pattern so a crash or power loss mid-write never leaves a half-written
    (corrupt) JSON file behind.

    The current BATCH_STATE_VERSION is always injected into the saved data
    regardless of whether the incoming state dict already contains it.  This
    guarantees the on-disk file is always tagged with the current schema.

    Strategy
    ────────
    1. Write the full JSON to  <path>.tmp  next to the target file.
    2. Call tmp.replace(path) — on POSIX this is a single rename(2) syscall
       (atomic); on Windows it is not truly atomic but the original file is
       only replaced after the new data is fully flushed to disk, which is
       still far safer than overwriting in place.
    3. If anything goes wrong before the rename, the .tmp file is deleted
       and the original (if it exists) is left untouched.

    Creates parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")

    # Always stamp with the current schema version.
    # Build a new dict so we never mutate the caller's state object.
    versioned = {"_schema_version": BATCH_STATE_VERSION}
    versioned.update(state)

    try:
        tmp.write_text(
            json.dumps(versioned, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def get_batch_state_path(output_folder: str) -> Path:
    """Return the canonical batch_state.json path for an episode."""
    return Path(output_folder) / "dub" / "batch_state.json"