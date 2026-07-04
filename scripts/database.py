"""
database.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Single SQLite database.  Every other script talks to this and nothing else.

Structure
─────────
  projects          — one row per manhwa series
  episodes          — one row per video OR pdf chapter inside a project
  panels            — one row per panel inside an episode
  dub_languages     — languages enabled for a given episode
  panel_audio       — per-panel audio file for each language
  narration_batches — tracks Claude/NVIDIA vision batches for PDF episodes
  processing_logs   — full history of every stage attempt
  settings          — global key/value config store

Note: voice_profiles table is intentionally absent.
VoiceProfile storage is handled by tts/voice_profile.py (JSON files in
config.VOICES_DIR).  The old DB table had an incompatible schema and is
dropped by Migration 6 on first open.

Pipeline business logic (invalidate_panel_downstream etc.) has moved to
pipeline_logic.py — database.py only reads and writes rows.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    folder_name   TEXT    NOT NULL,
    cover_path    TEXT    DEFAULT NULL,
    notes         TEXT    DEFAULT '',
    created_at    REAL    NOT NULL,
    updated_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title           TEXT    NOT NULL,
    source_type     TEXT    NOT NULL CHECK(source_type IN ('video','pdf','screenshots')),
    source_path     TEXT    NOT NULL,
    output_folder   TEXT    NOT NULL,
    mode            TEXT    NOT NULL DEFAULT 'auto'
                            CHECK(mode IN ('auto','manual')),
    tone_prompt     TEXT    DEFAULT '',
    stage_detect      TEXT    DEFAULT 'pending',
    stage_extract     TEXT    DEFAULT 'pending',
    stage_screenshot  TEXT    DEFAULT 'skipped',
    stage_upscale     TEXT    DEFAULT 'skipped',
    stage_narrate     TEXT    DEFAULT 'pending',
    stage_translate   TEXT    DEFAULT 'pending',
    stage_tts         TEXT    DEFAULT 'pending',
    stage_dub         TEXT    DEFAULT 'pending',
    stage_sync        TEXT    DEFAULT 'skipped',
    stage_assemble    TEXT    DEFAULT 'skipped',
    progress_detect      INTEGER DEFAULT 0,
    progress_extract     INTEGER DEFAULT 0,
    progress_screenshot  INTEGER DEFAULT 0,
    progress_upscale     INTEGER DEFAULT 0,
    progress_narrate   INTEGER DEFAULT 0,
    progress_translate INTEGER DEFAULT 0,
    progress_tts       INTEGER DEFAULT 0,
    progress_dub       INTEGER DEFAULT 0,
    progress_sync      INTEGER DEFAULT 0,
    progress_assemble  INTEGER DEFAULT 0,
    cuts_json_path   TEXT DEFAULT NULL,
    panels_folder    TEXT DEFAULT NULL,
    upscaled_folder  TEXT DEFAULT NULL,
    script_path      TEXT DEFAULT NULL,
    final_video_path TEXT DEFAULT NULL,
    total_panels    INTEGER DEFAULT 0,
    duration_secs   REAL    DEFAULT NULL,
    total_pages     INTEGER DEFAULT NULL,
    error_message   TEXT    DEFAULT '',
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    started_at      REAL    DEFAULT NULL,
    finished_at     REAL    DEFAULT NULL,
    detect_mode          TEXT    DEFAULT 'combined',
    detect_priority      TEXT    DEFAULT 'combined',
    detect_silence_db    REAL    DEFAULT -45.0,
    detect_min_silence   REAL    DEFAULT 0.25,
    detect_threshold     REAL    DEFAULT 3.0,
    detect_min_scene     REAL    DEFAULT 1.5,
    detect_frame_skip    INTEGER DEFAULT 2,
    detect_merge_window  REAL    DEFAULT 1.5,
    detect_workers       INTEGER DEFAULT 4,
    detect_clip_start    TEXT    DEFAULT '00:00:00',
    detect_clip_duration INTEGER DEFAULT 120,
    detect_confirmed     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS panels (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id        INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    panel_index       INTEGER NOT NULL,
    start_time_sec    REAL    DEFAULT NULL,
    end_time_sec      REAL    DEFAULT NULL,
    duration_sec      REAL    DEFAULT NULL,
    video_clip_path   TEXT    DEFAULT NULL,
    image_path        TEXT    DEFAULT NULL,
    upscaled_path     TEXT    DEFAULT NULL,
    screenshot_path   TEXT    DEFAULT NULL,
    transcript_text   TEXT    DEFAULT '',
    narration_text    TEXT    DEFAULT '',
    narration_status  TEXT    DEFAULT 'pending'
                      CHECK(narration_status IN ('pending','done')),
    created_at        REAL    NOT NULL,
    updated_at        REAL    NOT NULL,
    UNIQUE(episode_id, panel_index)
);

CREATE TABLE IF NOT EXISTS dub_languages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id          INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    lang_code           TEXT    NOT NULL,
    lang_name           TEXT    NOT NULL,
    translate_status    TEXT    DEFAULT 'pending',
    tts_status          TEXT    DEFAULT 'pending',
    sync_status         TEXT    DEFAULT 'pending',
    continuous_wav_path TEXT    DEFAULT NULL,
    final_audio_path    TEXT    DEFAULT NULL,
    created_at          REAL    NOT NULL,
    updated_at          REAL    NOT NULL,
    UNIQUE(episode_id, lang_code)
);

CREATE TABLE IF NOT EXISTS panel_audio (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id        INTEGER NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
    lang_code       TEXT    NOT NULL,
    translated_text TEXT    DEFAULT '',
    raw_wav         TEXT    DEFAULT NULL,
    raw_duration    REAL    DEFAULT NULL,
    synced_wav      TEXT    DEFAULT NULL,
    synced_duration REAL    DEFAULT NULL,
    is_synced       INTEGER DEFAULT 0,
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    UNIQUE(panel_id, lang_code)
);

CREATE TABLE IF NOT EXISTS narration_batches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id     INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    batch_number   INTEGER NOT NULL,
    image_paths    TEXT    DEFAULT '[]',
    context_text   TEXT    DEFAULT '',
    ref_transcript TEXT    DEFAULT '',
    response_text  TEXT    DEFAULT '',
    status         TEXT    DEFAULT 'pending'
                   CHECK(status IN ('pending','sent','done')),
    created_at     REAL    NOT NULL,
    completed_at   REAL    DEFAULT NULL,
    UNIQUE(episode_id, batch_number)
);

CREATE TABLE IF NOT EXISTS processing_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id    INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    stage         TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    started_at    REAL    NOT NULL,
    finished_at   REAL    DEFAULT NULL,
    duration_secs REAL    DEFAULT NULL,
    error         TEXT    DEFAULT '',
    metadata_json TEXT    DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episodes_project   ON episodes(project_id);
CREATE INDEX IF NOT EXISTS idx_panels_episode     ON panels(episode_id);
CREATE INDEX IF NOT EXISTS idx_dub_lang_episode   ON dub_languages(episode_id);
CREATE INDEX IF NOT EXISTS idx_panel_audio_panel  ON panel_audio(panel_id);
CREATE INDEX IF NOT EXISTS idx_narr_batch_episode ON narration_batches(episode_id);
CREATE INDEX IF NOT EXISTS idx_logs_episode       ON processing_logs(episode_id);
"""


# ── Database class ────────────────────────────────────────────────────────────

class Database:

    def __init__(self, db_path: str = "studio.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.execute("INSERT OR IGNORE INTO projects (id, name, folder_name, created_at, updated_at) VALUES (0, 'Dub Studio', 'dub_studio', 0, 0)")
        self._conn.execute("INSERT OR IGNORE INTO episodes (id, project_id, title, source_type, source_path, output_folder, created_at, updated_at) VALUES (0, 0, 'Adhoc Jobs', 'video', '', '', 0, 0)")
        self._migrate()

    def close(self):
        self._conn.close()

    # ── Schema migration ──────────────────────────────────────────────────────

    def _migrate(self):
        row = self._fetchone(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='episodes'"
        )
        if not row:
            return

        leftover = self._fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_episodes_old'"
        )
        if leftover:
            self._conn.execute("DROP TABLE _episodes_old")

        if "'screenshots'" in (row["sql"] or ""):
            # Already on v1 schema — apply column migrations only
            pass
        else:
            # Full schema upgrade
            self._conn.execute("ALTER TABLE episodes RENAME TO _episodes_old")
            self._conn.execute("""
        CREATE TABLE episodes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title           TEXT    NOT NULL,
            source_type     TEXT    NOT NULL CHECK(source_type IN ('video','pdf','screenshots')),
            source_path     TEXT    NOT NULL,
            output_folder   TEXT    NOT NULL,
            mode            TEXT    NOT NULL DEFAULT 'auto' CHECK(mode IN ('auto','manual')),
            tone_prompt     TEXT    DEFAULT '',
            stage_detect      TEXT    DEFAULT 'pending',
            stage_extract     TEXT    DEFAULT 'pending',
            stage_screenshot  TEXT    DEFAULT 'skipped',
            stage_upscale     TEXT    DEFAULT 'skipped',
            stage_narrate     TEXT    DEFAULT 'pending',
            stage_translate   TEXT    DEFAULT 'pending',
            stage_tts         TEXT    DEFAULT 'pending',
            stage_dub         TEXT    DEFAULT 'pending',
            stage_sync        TEXT    DEFAULT 'skipped',
            stage_assemble    TEXT    DEFAULT 'skipped',
            progress_detect      INTEGER DEFAULT 0,
            progress_extract     INTEGER DEFAULT 0,
            progress_screenshot  INTEGER DEFAULT 0,
            progress_upscale     INTEGER DEFAULT 0,
            progress_narrate   INTEGER DEFAULT 0,
            progress_translate INTEGER DEFAULT 0,
            progress_tts       INTEGER DEFAULT 0,
            progress_dub       INTEGER DEFAULT 0,
            progress_sync      INTEGER DEFAULT 0,
            progress_assemble  INTEGER DEFAULT 0,
            cuts_json_path   TEXT DEFAULT NULL,
            panels_folder    TEXT DEFAULT NULL,
            upscaled_folder  TEXT DEFAULT NULL,
            script_path      TEXT DEFAULT NULL,
            final_video_path TEXT DEFAULT NULL,
            total_panels    INTEGER DEFAULT 0,
            duration_secs   REAL    DEFAULT NULL,
            total_pages     INTEGER DEFAULT NULL,
            error_message   TEXT    DEFAULT '',
            created_at      REAL    NOT NULL,
            updated_at      REAL    NOT NULL,
            started_at      REAL    DEFAULT NULL,
            finished_at     REAL    DEFAULT NULL
        )""")
            self._conn.execute("INSERT INTO episodes SELECT * FROM _episodes_old")
            self._conn.execute("DROP TABLE _episodes_old")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project_id)"
            )

        # ── Migration 2: stage_screenshot column ──────────────────────────────
        try:
            self._fetchone("SELECT stage_screenshot FROM episodes LIMIT 1")
        except Exception:
            self._conn.execute(
                "ALTER TABLE episodes ADD COLUMN stage_screenshot TEXT DEFAULT 'skipped'"
            )
            self._conn.execute(
                "UPDATE episodes SET stage_screenshot='pending' WHERE source_type='video'"
            )

        # ── Migration 3: progress_screenshot column ───────────────────────────
        try:
            self._fetchone("SELECT progress_screenshot FROM episodes LIMIT 1")
        except Exception:
            self._conn.execute(
                "ALTER TABLE episodes ADD COLUMN progress_screenshot INTEGER DEFAULT 0"
            )

        # ── Migration 4: stage_sync + progress_sync ───────────────────────────
        try:
            self._fetchone("SELECT stage_sync FROM episodes LIMIT 1")
        except Exception:
            self._conn.execute(
                "ALTER TABLE episodes ADD COLUMN stage_sync TEXT DEFAULT 'skipped'"
            )
            self._conn.execute(
                "UPDATE episodes SET stage_sync='pending' WHERE source_type='video'"
            )
        try:
            self._fetchone("SELECT progress_sync FROM episodes LIMIT 1")
        except Exception:
            self._conn.execute(
                "ALTER TABLE episodes ADD COLUMN progress_sync INTEGER DEFAULT 0"
            )

        # ── Migration 5: Per-episode detection settings ───────────────────────
        _detect_cols = [
            ("detect_mode",         "TEXT",    "'combined'"),
            ("detect_priority",     "TEXT",    "'combined'"),
            ("detect_silence_db",   "REAL",    "-45.0"),
            ("detect_min_silence",  "REAL",    "0.25"),
            ("detect_threshold",    "REAL",    "3.0"),
            ("detect_min_scene",    "REAL",    "1.5"),
            ("detect_frame_skip",   "INTEGER", "2"),
            ("detect_merge_window", "REAL",    "1.5"),
            ("detect_workers",      "INTEGER", "4"),
            ("detect_clip_start",   "TEXT",    "'00:00:00'"),
            ("detect_clip_duration","INTEGER", "120"),
            ("detect_confirmed",    "INTEGER", "0"),
        ]
        for col, typ, default in _detect_cols:
            try:
                self._fetchone(f"SELECT {col} FROM episodes LIMIT 1")
            except Exception:
                self._conn.execute(
                    f"ALTER TABLE episodes ADD COLUMN {col} {typ} DEFAULT {default}"
                )

        # ── Migration 6: Drop the legacy voice_profiles table ─────────────────
        # VoiceProfile storage has moved to JSON files (tts/voice_profile.py).
        # The old table had an incompatible schema — columns don't match the
        # current VoiceProfile dataclass.  Safe to drop because all code now
        # reads from config.VOICES_DIR/*.json exclusively.
        try:
            self._conn.execute("DROP TABLE IF EXISTS voice_profiles")
        except Exception:
            pass

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple = ()) -> list:
        return self._conn.execute(sql, params).fetchall()

    def _now(self) -> float:
        return time.time()

    def _row(self, row) -> Optional[dict]:
        return dict(row) if row else None

    def _rows(self, rows) -> list[dict]:
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════════════════
    # PROJECTS
    # ══════════════════════════════════════════════════════════════════════════

    def add_project(self, name: str) -> int:
        folder_name = name.lower().replace(" ", "_")
        now = self._now()
        try:
            cur = self._execute(
                "INSERT INTO projects (name, folder_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, folder_name, now, now),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return self._fetchone("SELECT id FROM projects WHERE name=?", (name,))["id"]

    def get_project(self, project_id: int) -> Optional[dict]:
        return self._row(self._fetchone("SELECT * FROM projects WHERE id=?", (project_id,)))

    def get_project_by_name(self, name: str) -> Optional[dict]:
        return self._row(self._fetchone("SELECT * FROM projects WHERE name=?", (name,)))

    def list_projects(self) -> list[dict]:
        return self._rows(self._fetchall("SELECT * FROM projects ORDER BY name"))

    def update_project(self, project_id: int, **kwargs):
        kwargs["updated_at"] = self._now()
        sets   = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [project_id]
        self._execute(f"UPDATE projects SET {sets} WHERE id=?", values)

    def delete_project(self, project_id: int):
        self._execute("DELETE FROM projects WHERE id=?", (project_id,))

    def project_stats(self, project_id: int) -> dict:
        total = self._fetchone(
            "SELECT COUNT(*) as n FROM episodes WHERE project_id=?", (project_id,))["n"]
        done = self._fetchone(
            "SELECT COUNT(*) as n FROM episodes WHERE project_id=? AND stage_assemble='done'",
            (project_id,))["n"]
        return {"total_episodes": total, "done_episodes": done}

    # ══════════════════════════════════════════════════════════════════════════
    # EPISODES
    # ══════════════════════════════════════════════════════════════════════════

    def add_episode(self, project_id: int, title: str, source_type: str,
                    source_path: str, output_folder: str,
                    mode: str = "auto", tone_prompt: str = "") -> int:
        now = self._now()
        if source_type == "video":
            stage_detect = "pending"; stage_extract = "pending"
            stage_screenshot = "pending"; stage_upscale = "skipped"
            stage_narrate = "skipped"; stage_sync = "pending"
        elif source_type == "screenshots":
            stage_detect = "skipped"; stage_extract = "skipped"
            stage_screenshot = "skipped"; stage_upscale = "pending"
            stage_narrate = "skipped"; stage_sync = "skipped"
        else:
            stage_detect = "skipped"; stage_extract = "pending"
            stage_screenshot = "skipped"; stage_upscale = "skipped"
            stage_narrate = "pending"; stage_sync = "skipped"

        cur = self._execute(
            """INSERT INTO episodes (
                project_id, title, source_type, source_path, output_folder,
                mode, tone_prompt,
                stage_detect, stage_extract, stage_screenshot,
                stage_upscale, stage_narrate, stage_sync,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, title, source_type, str(source_path),
             str(output_folder), mode, tone_prompt,
             stage_detect, stage_extract, stage_screenshot,
             stage_upscale, stage_narrate, stage_sync,
             now, now),
        )
        return cur.lastrowid

    def get_episode(self, episode_id: int) -> Optional[dict]:
        return self._row(self._fetchone("SELECT * FROM episodes WHERE id=?", (episode_id,)))

    def list_episodes(self, project_id: int) -> list[dict]:
        return self._rows(self._fetchall(
            "SELECT * FROM episodes WHERE project_id=? ORDER BY created_at", (project_id,)))

    def update_episode(self, episode_id: int, **kwargs):
        kwargs["updated_at"] = self._now()
        sets   = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [episode_id]
        self._execute(f"UPDATE episodes SET {sets} WHERE id=?", values)

    def delete_episode(self, episode_id: int):
        self._execute("DELETE FROM episodes WHERE id=?", (episode_id,))

    def set_episode_stage(self, episode_id: int, stage: str, status: str,
                          progress: int = None, error: str = "",
                          output_path: str = None):
        now    = self._now()
        fields = {f"stage_{stage}": status, "updated_at": now}
        if progress is not None:
            fields[f"progress_{stage}"] = progress
        if error:
            fields["error_message"] = error
        path_col = {
            "detect":   "cuts_json_path",
            "extract":  "panels_folder",
            "upscale":  "upscaled_folder",
            "narrate":  "script_path",
            "assemble": "final_video_path",
        }.get(stage)
        if output_path and path_col:
            fields[path_col] = str(output_path)
        if status == "running":
            fields.setdefault("started_at", now)
        if status == "done":
            fields[f"progress_{stage}"] = 100
        elif status in ("pending", "outdated", "failed"):
            # An invalidated / not-yet-run stage is 0% — otherwise its stale
            # progress keeps the episode's overall % pinned (e.g. editing
            # narration leaves overall at 100 while downstream is outdated).
            fields[f"progress_{stage}"] = 0
        sets   = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [episode_id]
        self._execute(f"UPDATE episodes SET {sets} WHERE id=?", values)

    def set_episode_progress(self, episode_id: int, stage: str, progress: int):
        self._execute(
            f"UPDATE episodes SET progress_{stage}=?, updated_at=? WHERE id=?",
            (progress, self._now(), episode_id),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PANELS
    # ══════════════════════════════════════════════════════════════════════════

    def add_panel(self, episode_id: int, panel_index: int, **kwargs) -> int:
        now = self._now()
        kwargs.update({"episode_id": episode_id, "panel_index": panel_index,
                        "created_at": now, "updated_at": now})
        cols         = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" * len(kwargs))
        try:
            cur = self._execute(
                f"INSERT INTO panels ({cols}) VALUES ({placeholders})", tuple(kwargs.values()))
            self._execute(
                "UPDATE episodes SET total_panels="
                "(SELECT COUNT(*) FROM panels WHERE episode_id=?),"
                "updated_at=? WHERE id=?",
                (episode_id, now, episode_id),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return self._fetchone(
                "SELECT id FROM panels WHERE episode_id=? AND panel_index=?",
                (episode_id, panel_index))["id"]

    def get_panel(self, panel_id: int) -> Optional[dict]:
        return self._row(self._fetchone("SELECT * FROM panels WHERE id=?", (panel_id,)))

    def get_panel_by_index(self, episode_id: int, panel_index: int) -> Optional[dict]:
        return self._row(self._fetchone(
            "SELECT * FROM panels WHERE episode_id=? AND panel_index=?",
            (episode_id, panel_index)))

    def list_panels(self, episode_id: int) -> list[dict]:
        return self._rows(self._fetchall(
            "SELECT * FROM panels WHERE episode_id=? ORDER BY panel_index", (episode_id,)))

    def update_panel(self, panel_id: int, **kwargs):
        kwargs["updated_at"] = self._now()
        sets   = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [panel_id]
        self._execute(f"UPDATE panels SET {sets} WHERE id=?", values)

    def delete_panel(self, panel_id: int):
        self._execute("DELETE FROM panels WHERE id=?", (panel_id,))

    # ══════════════════════════════════════════════════════════════════════════
    # PANEL AUDIO
    # ══════════════════════════════════════════════════════════════════════════

    def ensure_panel_audio(self, panel_id: int, lang_code: str) -> int:
        now = self._now()
        try:
            cur = self._execute(
                "INSERT INTO panel_audio (panel_id, lang_code, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (panel_id, lang_code, now, now))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return self._fetchone(
                "SELECT id FROM panel_audio WHERE panel_id=? AND lang_code=?",
                (panel_id, lang_code))["id"]

    def get_panel_audio(self, panel_id: int, lang_code: str) -> Optional[dict]:
        return self._row(self._fetchone(
            "SELECT * FROM panel_audio WHERE panel_id=? AND lang_code=?",
            (panel_id, lang_code)))

    def list_panel_audio(self, panel_id: int) -> list[dict]:
        return self._rows(self._fetchall(
            "SELECT * FROM panel_audio WHERE panel_id=?", (panel_id,)))

    def update_panel_audio(self, audio_id: int, **kwargs):
        kwargs["updated_at"] = self._now()
        sets   = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [audio_id]
        self._execute(f"UPDATE panel_audio SET {sets} WHERE id=?", values)

    def delete_panel_audio(self, panel_id: int, lang_code: str):
        self._execute(
            "DELETE FROM panel_audio WHERE panel_id=? AND lang_code=?",
            (panel_id, lang_code))

    def count_panel_audio_done(self, episode_id: int, lang_code: str) -> int:
        return self._fetchone(
            """SELECT COUNT(*) as n FROM panel_audio pa
               JOIN panels p ON p.id = pa.panel_id
               WHERE p.episode_id=? AND pa.lang_code=?
               AND pa.raw_wav IS NOT NULL""",
            (episode_id, lang_code))["n"]

    def get_panels_missing_audio(self, episode_id: int, lang_code: str) -> list[dict]:
        return self._rows(self._fetchall(
            """SELECT p.*, pa.id as audio_id, pa.translated_text
               FROM panels p
               LEFT JOIN panel_audio pa ON pa.panel_id = p.id AND pa.lang_code = ?
               WHERE p.episode_id=?
               AND pa.translated_text IS NOT NULL AND pa.translated_text != ''
               AND (pa.raw_wav IS NULL OR pa.raw_wav = '')
               ORDER BY p.panel_index""",
            (lang_code, episode_id)))

    # ══════════════════════════════════════════════════════════════════════════
    # NARRATION BATCHES
    # ══════════════════════════════════════════════════════════════════════════

    def add_narration_batch(self, episode_id: int, batch_number: int,
                            image_paths: list) -> int:
        now = self._now()
        try:
            cur = self._execute(
                "INSERT INTO narration_batches "
                "(episode_id, batch_number, image_paths, created_at) VALUES (?, ?, ?, ?)",
                (episode_id, batch_number, json.dumps(image_paths), now))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return self._fetchone(
                "SELECT id FROM narration_batches WHERE episode_id=? AND batch_number=?",
                (episode_id, batch_number))["id"]

    def get_narration_batch(self, episode_id: int, batch_number: int) -> Optional[dict]:
        row = self._fetchone(
            "SELECT * FROM narration_batches WHERE episode_id=? AND batch_number=?",
            (episode_id, batch_number))
        if not row:
            return None
        d = dict(row)
        try:
            d["image_paths"] = json.loads(d.get("image_paths", "[]"))
        except Exception:
            d["image_paths"] = []
        return d

    def list_narration_batches(self, episode_id: int) -> list[dict]:
        rows   = self._fetchall(
            "SELECT * FROM narration_batches WHERE episode_id=? ORDER BY batch_number",
            (episode_id,))
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["image_paths"] = json.loads(d.get("image_paths", "[]"))
            except Exception:
                d["image_paths"] = []
            result.append(d)
        return result

    def update_narration_batch(self, episode_id: int, batch_number: int, **kwargs):
        if "image_paths" in kwargs and isinstance(kwargs["image_paths"], list):
            kwargs["image_paths"] = json.dumps(kwargs["image_paths"])
        sets   = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [episode_id, batch_number]
        self._execute(
            f"UPDATE narration_batches SET {sets} WHERE episode_id=? AND batch_number=?",
            values)

    def complete_narration_batch(self, episode_id: int, batch_number: int,
                                  response_text: str):
        self._execute(
            "UPDATE narration_batches SET status='done', response_text=?, completed_at=? "
            "WHERE episode_id=? AND batch_number=?",
            (response_text, self._now(), episode_id, batch_number))

    def narration_progress(self, episode_id: int) -> dict:
        total = self._fetchone(
            "SELECT COUNT(*) as n FROM narration_batches WHERE episode_id=?",
            (episode_id,))["n"]
        done = self._fetchone(
            "SELECT COUNT(*) as n FROM narration_batches WHERE episode_id=? AND status='done'",
            (episode_id,))["n"]
        return {"total": total, "done": done, "pending": total - done,
                "pct": round((done / total) * 100) if total else 0}

    # ══════════════════════════════════════════════════════════════════════════
    # PROCESSING LOGS
    # ══════════════════════════════════════════════════════════════════════════

    def log_stage_start(self, episode_id: int, stage: str) -> int:
        cur = self._execute(
            "INSERT INTO processing_logs (episode_id, stage, status, started_at) "
            "VALUES (?, ?, 'running', ?)",
            (episode_id, stage, self._now()))
        return cur.lastrowid

    def log_stage_end(self, log_id: int, status: str,
                      error: str = "", metadata: dict = None):
        now = self._now()
        row = self._fetchone(
            "SELECT started_at FROM processing_logs WHERE id=?", (log_id,))
        dur = round(now - row["started_at"], 2) if row else None
        self._execute(
            "UPDATE processing_logs SET status=?, finished_at=?, "
            "duration_secs=?, error=?, metadata_json=? WHERE id=?",
            (status, now, dur, error, json.dumps(metadata or {}), log_id))

    def log_action(self, episode_id: int, stage: str, status: str = "done",
                   error: str = "") -> int:
        """Record a one-shot action (edit, clear, regenerate, config save) in
        processing_logs so the Logs archive captures every action, not just
        long-running stage executions."""
        now = self._now()
        cur = self._execute(
            "INSERT INTO processing_logs (episode_id, stage, status, "
            "started_at, finished_at, duration_secs, error) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (episode_id, stage, status, now, now, error))
        return cur.lastrowid

    def log_adhoc_start(self, stage: str, project_name: str = "Dub Studio") -> int:
        """Start a live adhoc log entry so the UI can see it running immediately."""
        now = self._now()
        metadata = {"project_name": project_name, "log": []}
        cur = self._execute(
            "INSERT INTO processing_logs (episode_id, stage, status, started_at, metadata_json) "
            "VALUES (0, ?, 'running', ?, ?)",
            (stage, now, json.dumps(metadata)))
        return cur.lastrowid

    def log_adhoc_update(self, log_id: int, status: str, log_lines: list, error: str = ""):
        """Update a running adhoc log entry with new lines or completion status."""
        now = self._now()
        row = self._fetchone("SELECT started_at, metadata_json FROM processing_logs WHERE id=?", (log_id,))
        if not row: return
        
        try:
            meta = json.loads(row["metadata_json"])
        except Exception:
            meta = {}
            
        meta["log"] = log_lines
        dur = round(now - row["started_at"], 2)
        
        # Only set finished_at if it's done or failed
        finished_at = now if status in ("done", "failed") else None
        
        self._execute(
            "UPDATE processing_logs SET status=?, finished_at=?, "
            "duration_secs=?, error=?, metadata_json=? WHERE id=?",
            (status, finished_at, dur, error, json.dumps(meta), log_id))

    def log_adhoc_activity(self, stage: str, status: str, duration_secs: float,
                           log_lines: list, error: str = "", project_name: str = "") -> int:
        """Record a completed adhoc action in one shot (legacy support)."""
        now = self._now()
        metadata = {"log": log_lines, "project_name": project_name}
        cur = self._execute(
            "INSERT INTO processing_logs (episode_id, stage, status, "
            "started_at, finished_at, duration_secs, error, metadata_json) "
            "VALUES (0, ?, ?, ?, ?, ?, ?, ?)",
            (stage, status, now - duration_secs, now, duration_secs, error, json.dumps(metadata)))
        return cur.lastrowid

    def get_episode_logs(self, episode_id: int) -> list[dict]:
        return self._rows(self._fetchall(
            "SELECT * FROM processing_logs WHERE episode_id=? ORDER BY started_at DESC",
            (episode_id,)))

    def list_recent_logs(self, limit: int = 200) -> list[dict]:
        """Recent processing-log rows across all episodes, newest first,
        annotated with episode title + project name for the Logs archive."""
        rows = self._rows(self._fetchall(
            """SELECT l.*, e.title AS episode_title, p.name AS project_name
               FROM processing_logs l
               LEFT JOIN episodes e ON e.id = l.episode_id
               LEFT JOIN projects p ON p.id = e.project_id
               ORDER BY l.started_at DESC
               LIMIT ?""",
            (int(limit),)))
        
        for r in rows:
            if r["episode_id"] == 0 and r.get("metadata_json"):
                try:
                    meta = json.loads(r["metadata_json"])
                    r["project_name"] = meta.get("project_name", "")
                except Exception:
                    pass
        return rows

    def clear_logs(self, episode_id: int = None) -> int:
        """Delete processing-log rows (all, or one episode). Returns rows removed."""
        if episode_id is None:
            cur = self._execute("DELETE FROM processing_logs")
        else:
            cur = self._execute("DELETE FROM processing_logs WHERE episode_id=?", (episode_id,))
        return cur.rowcount if cur else 0

    # ══════════════════════════════════════════════════════════════════════════
    # SETTINGS
    # ══════════════════════════════════════════════════════════════════════════

    def get_setting(self, key: str, default=None):
        row = self._fetchone("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def get_setting_json(self, key: str, default):
        """Return a setting already parsed to a list/dict, tolerating both the
        auto-decoded form (get_setting decodes JSON) and a raw JSON string.
        Use this instead of json.loads(get_setting(...)) — get_setting already
        decodes, so json.loads() on the result would raise on a list/dict."""
        val = self.get_setting(key, default)
        if isinstance(val, (list, dict)):
            return val
        if val is None:
            return default
        try:
            return json.loads(val)
        except Exception:
            return default

    def set_setting(self, key: str, value):
        self._execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value)))

    def get_all_settings(self) -> dict:
        rows   = self._fetchall("SELECT key, value FROM settings")
        result = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except Exception:
                result[row["key"]] = row["value"]
        return result

    # ══════════════════════════════════════════════════════════════════════════
    # STATS & QUERIES
    # ══════════════════════════════════════════════════════════════════════════

    def global_stats(self) -> dict:
        projects = self._fetchone("SELECT COUNT(*) as n FROM projects")["n"]
        episodes = self._fetchone("SELECT COUNT(*) as n FROM episodes")["n"]
        # "done" = final video assembled.  (Was stage_tts='done', which never
        # became true since TTS is folded into the dub stage and stage_tts is
        # no longer authored — see pipeline_logic module docstring.)
        done     = self._fetchone(
            "SELECT COUNT(*) as n FROM episodes WHERE stage_assemble='done'")["n"]
        failed   = self._fetchone(
            """SELECT COUNT(*) as n FROM episodes WHERE
               stage_detect='failed' OR stage_extract='failed' OR
               stage_narrate='failed' OR stage_dub='failed' OR
               stage_assemble='failed'""")["n"]
        return {
            "total_projects":   projects,
            "total_episodes":   episodes,
            "done_episodes":    done,
            "failed_episodes":  failed,
            "pending_episodes": episodes - done - failed,
        }

    def list_episodes_with_project(self) -> list[dict]:
        return self._rows(self._fetchall(
            """SELECT e.*, p.name as project_name, p.folder_name
               FROM episodes e JOIN projects p ON p.id = e.project_id
               ORDER BY p.name, e.created_at"""))

    def __repr__(self):
        s = self.global_stats()
        return (
            f"Database({self.db_path} | "
            f"{s['total_projects']} projects | "
            f"{s['total_episodes']} episodes | "
            f"{s['done_episodes']} done | "
            f"{s['failed_episodes']} failed)"
        )
