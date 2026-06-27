"""
tts/voice_profile.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
VoiceProfile dataclass and VoiceProfileManager.

Extracted from tts_engine.py.
The database voice_profiles table has been removed (it was dead code with
an incompatible schema).  JSON files are the single source of truth.

Profile storage location:  {config.VOICES_DIR}/{name}.json

Usage
─────
    from tts.voice_profile import VoiceProfile, VoiceProfileManager

    vpm     = VoiceProfileManager(str(config.VOICES_DIR))
    profile = vpm.load("Adam_en")
    vpm.save(profile)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


# ── Whitelist of every legitimate VoiceProfile attribute ─────────────────────
# from_dict() will only setattr() keys that appear here.  Any other key in a
# JSON file — whether from a future schema version, a corrupt file, or a
# malicious payload — is silently skipped rather than being applied to the
# object.  This prevents overwriting dunder methods or injecting unexpected
# state.
#
# When adding a new attribute to VoiceProfile.__init__, add it here too.
_PROFILE_FIELDS = frozenset({
    "name",
    "mode",
    "model",
    "language",
    # CustomVoice
    "speaker",
    "instruct",
    # VoiceClone
    "ref_wav_path",
    "ref_wav_text",
    "x_vector_only",
    # Generation params
    "temperature",
    "top_p",
    "top_k",
    "repetition_penalty",
    "max_new_tokens",
    "seed",
    # Timestamps
    "created_at",
    "updated_at",
})


class VoiceProfile:
    """
    Holds all configuration for a single TTS voice.
    Serialises to/from JSON for persistent storage on disk.

    Modes
    ─────
    CustomVoice  — preset speaker (Aiden, Eric, Sohee, etc.)
    VoiceDesign  — text description of the voice
    VoiceClone   — clone from reference audio file
    """

    def __init__(self, name: str = "default"):
        self.name               = name
        self.mode               = "CustomVoice"   # CustomVoice | VoiceDesign | VoiceClone
        self.model              = "1.7B-CustomVoice"
        self.language           = "English"

        # CustomVoice
        self.speaker            = "Aiden"
        self.instruct           = ""              # optional style note

        # VoiceClone
        self.ref_wav_path       = ""
        self.ref_wav_text       = ""              # transcript (ICL mode)
        self.x_vector_only      = True            # True = no transcript needed

        # Generation params — tightened defaults for CJK stability
        self.temperature        = 0.7
        self.top_p              = 1.0
        self.top_k              = 50
        self.repetition_penalty = 1.1
        self.max_new_tokens     = 2048
        # Optional RNG seed. -1 = random (recommended — lets prosody vary
        # naturally). A fixed value only locks rhythm/intonation; it does NOT
        # make the voice more consistent (the reference clone does that), and a
        # bad fixed seed is consistently bad.
        self.seed               = -1

        self.created_at         = time.time()
        self.updated_at         = time.time()

    # ── Serialisation ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "VoiceProfile":
        """
        Deserialise a VoiceProfile from a plain dict (e.g. loaded from JSON).

        Only keys listed in _PROFILE_FIELDS are applied via setattr().
        Unknown keys — from newer schema versions, corrupt files, or any
        other source — are silently ignored so they cannot overwrite methods
        or inject unexpected object state.
        """
        p = cls(d.get("name", "default"))
        for k, v in d.items():
            if k not in _PROFILE_FIELDS:
                # Unknown field — skip rather than blindly applying to the
                # object.  This covers both forward-compatibility (newer JSON
                # loaded by older code) and security (corrupted/malicious JSON).
                continue
            setattr(p, k, v)
        return p

    @classmethod
    def load(cls, path: str) -> "VoiceProfile":
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8"))
        )

    def save(self, path: str):
        self.updated_at = time.time()
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8"
        )


class VoiceProfileManager:
    """
    Persist and load VoiceProfile objects as JSON files.

    Each profile is stored as  {folder}/{name}.json.
    The profile name is embedded in the filename so list_profiles()
    never needs to parse file contents.
    """

    def __init__(self, voices_folder: str):
        self.folder = Path(voices_folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def save(self, profile: VoiceProfile):
        profile.save(str(self.folder / f"{profile.name}.json"))

    def load(self, name: str) -> Optional[VoiceProfile]:
        path = self.folder / f"{name}.json"
        if not path.exists():
            return None
        return VoiceProfile.load(str(path))

    def list_profiles(self) -> list:
        return sorted(p.stem for p in self.folder.glob("*.json"))

    def delete(self, name: str):
        path = self.folder / f"{name}.json"
        if path.exists():
            path.unlink()

    def exists(self, name: str) -> bool:
        return (self.folder / f"{name}.json").exists()