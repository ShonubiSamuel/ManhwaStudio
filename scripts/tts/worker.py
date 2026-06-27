"""
tts/worker.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
A resident TTS worker: one long-lived subprocess that loads the model + voice
ONCE and then synthesizes many lines over stdin, instead of paying a fresh model
load per call.

The big win is the auto-fix loop, which otherwise re-loads the model for every
panel × attempt. With a worker the model stays warm across all of them — faster,
and every take comes from the same loaded model.

Usage:
    w = TTSWorker(profile, on_log=log)
    if w.start():
        ok = w.generate("Bonjour", "/tmp/panel_0003.wav")
        ...
        w.close()
    else:
        ... fall back to the one-shot per-call path ...

Everything is best-effort and fail-safe: if the worker can't start or a request
fails, the caller falls back to the existing subprocess path, so behaviour never
regresses — the worker is a pure speed optimisation.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


class TTSWorker:
    def __init__(self, profile, on_log=None, ready_timeout: float = 240.0,
                 gen_timeout: float = 300.0):
        self.profile       = profile
        self._log          = on_log or (lambda *a, **k: None)
        self.ready_timeout = ready_timeout
        self.gen_timeout   = gen_timeout
        self.proc          = None

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Launch the worker and wait until the model + voice are loaded."""
        from tts import synth
        script = synth.build_worker_script(self.profile)
        if not script:
            return False   # no worker for this engine → caller falls back
        try:
            self.proc = subprocess.Popen(
                [synth.synth_python(self.profile), "-c", script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=synth.synth_env(self.profile),
            )
        except Exception as exc:
            self._log(f"  TTS worker launch failed ({exc}) — using per-call mode", "warning")
            self.proc = None
            return False

        deadline = time.time() + self.ready_timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line == "VOICE_READY":
                self._log("  TTS model resident — reusing it for all fixes", "info")
                return True
            if line.startswith("FATAL"):
                self._log(f"  TTS worker: {line} — using per-call mode", "warning")
                break
        self.close()
        return False

    def generate(self, text: str, out_path: str) -> bool:
        """Synthesize one line to out_path through the resident model."""
        if not self.proc or self.proc.poll() is not None:
            return False
        try:
            self.proc.stdin.write(json.dumps({"text": text, "out": out_path}) + "\n")
            self.proc.stdin.flush()
        except Exception:
            return False

        deadline = time.time() + self.gen_timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                return False
            line = line.strip()
            if line == "DONE":
                return Path(out_path).exists()
            if line.startswith("ERR"):
                self._log(f"  worker generate error: {line}", "warning")
                return False
        return False

    def close(self) -> None:
        if not self.proc:
            return
        try:
            if self.proc.poll() is None:
                try:
                    self.proc.stdin.write('{"cmd": "quit"}\n')
                    self.proc.stdin.flush()
                except Exception:
                    pass
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    self.proc.terminate()
        except Exception:
            pass
        finally:
            self.proc = None
