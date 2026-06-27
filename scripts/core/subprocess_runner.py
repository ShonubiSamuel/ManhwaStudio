"""
core/subprocess_runner.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Unified TTS subprocess management.

Previously copy-pasted as three near-identical Popen loops across two files:
  tts_engine.py   TTSEngine.generate_all()
  dub_engine.py   DubEngine.generate_continuous()
  dub_engine.py   DubEngine._generate_batch_wav()

The three copies had silently diverged in stop/timeout handling.
This module is the single implementation.

Public API
──────────
    subprocess_env()            → dict   (UTF-8 forced environment)
    run_tts_script(...)         → (bool, str)

Token protocol (stdout lines from the child process)
─────────────────────────────────────────────────────
    LOADING_MODEL         model load started
    MODEL_READY           model loaded
    VOICE_READY           voice fingerprint ready
    WARMUP_OK             warm-up pass succeeded
    WARMUP_FAIL:<msg>     warm-up failed (non-fatal)
    DONE:<i>              sentence i generated successfully
    SKIP:<i>              sentence i skipped (already done)
    ALL_DONE              all sentences finished
    ERROR:<i>:<msg>       sentence i failed
    FATAL:<msg>           unrecoverable error (also on stderr)
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from typing import Callable, Tuple

import config


def subprocess_env() -> dict:
    """Return os.environ copy with UTF-8 forced for every child process."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def run_tts_script(
    script:    str,
    on_line:   Callable[[str], None],
    stop_flag: Callable[[], bool],
    timeout:   int  = None,
    label:     str  = "",
) -> Tuple[bool, str]:
    """
    Spawn  CONDA_PYTHON -c script  and manage it to completion.

    Parameters
    ──────────
    script     : the Python source string to execute
    on_line    : called for every non-empty stdout token line.
                 Receives the stripped line.  The caller is responsible for
                 parsing DONE / ERROR / etc. and tracking its own state.
    stop_flag  : callable () → bool.  Checked every loop iteration.
                 When True the child is terminated immediately.
    timeout    : hard timeout in seconds (default: config.DUB_CONTINUOUS_TIMEOUT)
    label      : short string included in heartbeat log messages (e.g. lang code)

    Returns
    ───────
    (ok, stderr_tail)
    ok          = True  if the child exited normally without FATAL on stderr.
                  Note: whether any DONE tokens arrived is the caller's concern.
    stderr_tail = last 600 chars of stderr, empty string if stderr was clean.
    """
    if timeout is None:
        timeout = config.DUB_CONTINUOUS_TIMEOUT

    pfx = f"[{label}] " if label else ""

    try:
        proc = subprocess.Popen(
            [config.CONDA_PYTHON, "-c", script],
            stdout   = subprocess.PIPE,
            stderr   = subprocess.PIPE,
            text     = True,
            encoding = "utf-8",
            errors   = "replace",
            bufsize  = 1,
            env      = subprocess_env(),
        )

        stdout_q: queue.Queue = queue.Queue()
        stderr_q: queue.Queue = queue.Queue()

        def _read_stdout(p=proc, q=stdout_q):
            for raw in p.stdout:
                q.put(raw)
            q.put(None)

        def _read_stderr(p=proc, q=stderr_q):
            for raw in p.stderr:
                q.put(raw)
            q.put(None)

        threading.Thread(target=_read_stdout, daemon=True,
                         name="tts-stdout").start()
        threading.Thread(target=_read_stderr, daemon=True,
                         name="tts-stderr").start()

        start_time     = time.time()
        last_heartbeat = time.time()

        while True:
            # ── Stop signal ──────────────────────────────────────────────────
            if stop_flag():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False, ""

            # ── Read next stdout line (non-blocking, 1 s poll) ───────────────
            try:
                raw = stdout_q.get(timeout=1)
            except queue.Empty:
                elapsed = time.time() - start_time
                if time.time() - last_heartbeat >= 30:
                    on_line(f"__HEARTBEAT__:{elapsed:.0f}")
                    last_heartbeat = time.time()
                if elapsed > timeout:
                    on_line(f"__TIMEOUT__:{elapsed:.0f}")
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return False, f"Timed out after {elapsed:.0f}s"
                continue

            if raw is None:
                break  # stdout closed — child finished

            line = raw.strip()
            if line:
                last_heartbeat = time.time()
                on_line(line)

        # ── Collect stderr ───────────────────────────────────────────────────
        stderr_lines = []
        try:
            while True:
                item = stderr_q.get_nowait()
                if item is None:
                    break
                stderr_lines.append(item.rstrip())
        except queue.Empty:
            pass

        stderr_text = "\n".join(stderr_lines)

        # Detect genuine fatal conditions using precise anchors.
        #
        # The original check included the bare string "Error" which matches
        # far too broadly: UserWarning, FutureWarning, DeprecationWarning,
        # and any path or variable name containing "Error" would all trigger
        # a false failure even when the TTS run succeeded.
        #
        # "FATAL:" — the exact token the generated script prints to stderr
        #            via  print(f"FATAL:{e}", file=sys.stderr)
        #
        # "Traceback (most recent call last)" — the exact Python traceback
        #            header; far more specific than bare "Traceback".
        has_fatal = (
            "FATAL:" in stderr_text
            or "Traceback (most recent call last)" in stderr_text
        )

        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()

        if has_fatal:
            return False, stderr_text[-600:]

        return True, ""

    except Exception as exc:
        return False, str(exc)