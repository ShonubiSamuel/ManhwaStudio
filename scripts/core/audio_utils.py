"""
core/audio_utils.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
All WAV file utilities in one place.

Previously scattered across two engine files:
  dub_engine.py  — get_wav_duration, normalize_wav, stretch_audio
                   (module-level free functions)
                   _concat_wavs (DubEngine method)
  tts_engine.py  — inline audioop RMS normalisation loop inside merge_audio()

Public API
──────────
    get_wav_duration(wav_path)                          → float
    normalize_wav(wav_path, target_rms=None)            → bool   (in-place)
    normalize_frames(frames, sampwidth, target_rms=None)→ bytes
    concat_wavs(wav_paths, out_path, silence_secs=0.0)  → bool
    stretch_audio(input_path, output_path, target_dur)  → (bool, str)
"""

from __future__ import annotations

import audioop
import wave
from pathlib import Path
from typing import List, Tuple

import config


# ─────────────────────────────────────────────────────────────────────────────

def get_wav_duration(wav_path: str) -> float:
    """Return the duration of a WAV file in seconds. Returns 0.0 on any error."""
    try:
        with wave.open(str(wav_path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def normalize_frames(
    frames:     bytes,
    sampwidth:  int,
    target_rms: int = None,
) -> bytes:
    """
    Normalise raw PCM frames to target_rms.
    Gain is capped at 3× to prevent clipping on near-silence clips.
    Returns the adjusted frames (or original if rms == 0).
    """
    if target_rms is None:
        target_rms = config.DUB_NORMALIZE_RMS
    rms = audioop.rms(frames, sampwidth)
    if rms > 0:
        factor = min(target_rms / rms, 3.0)
        frames = audioop.mul(frames, sampwidth, factor)
    return frames


def normalize_wav(wav_path: str, target_rms: int = None) -> bool:
    """
    Normalise WAV file volume to target_rms, defaulting to
    config.DUB_NORMALIZE_RMS.

    Uses a temp-file-then-rename pattern so a crash or interrupt mid-write
    never leaves the original file half-written and corrupt.  The original
    is only replaced after the new data is fully written to disk.

    Returns True on success, False on any error.
    """
    if target_rms is None:
        target_rms = config.DUB_NORMALIZE_RMS

    wav_path = Path(wav_path)
    tmp_path = wav_path.with_name(wav_path.name + ".norm_tmp")

    try:
        with wave.open(str(wav_path), "rb") as wf:
            params    = wf.getparams()
            frames    = wf.readframes(wf.getnframes())
            sampwidth = wf.getsampwidth()

        frames = normalize_frames(frames, sampwidth, target_rms)

        with wave.open(str(tmp_path), "wb") as wf:
            wf.setparams(params)
            wf.writeframes(frames)

        # Atomic on POSIX (rename syscall); near-atomic on Windows.
        # Either way the original is only replaced after the new file is
        # fully written — a crash before this point leaves the original intact.
        tmp_path.replace(wav_path)
        return True

    except Exception as exc:
        # Clean up the temp file if it was created before the failure.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        print(f"[audio_utils] normalize_wav failed on {wav_path}: {exc}")
        return False


def concat_wavs(
    wav_paths:    List[str],
    out_path:     str,
    silence_secs: float = 0.0,
) -> bool:
    """
    Concatenate a list of WAV files into one output file.

    All input files must share the same sample rate, channel count, and
    sample width (bit depth).  Files whose format does not match the first
    file are skipped with a printed warning rather than silently writing
    corrupt mixed-format audio.  If skipping leaves nothing to write,
    the function returns False.

    silence_secs: seconds of silence to insert between each clip.
                  Pass 0.0 (default) for a seamless join.
                  Pass 0.4 for the sentence-gap used in tts_engine.merge_audio.

    Returns True on success.
    """
    if not wav_paths:
        return False
    try:
        frames_list: List[bytes] = []
        sample_rate  = channels = sampwidth = None
        ref_name: str = ""

        for path in wav_paths:
            with wave.open(str(path), "rb") as wf:
                this_rate     = wf.getframerate()
                this_channels = wf.getnchannels()
                this_width    = wf.getsampwidth()

                if sample_rate is None:
                    # First file — establish the reference format.
                    sample_rate = this_rate
                    channels    = this_channels
                    sampwidth   = this_width
                    ref_name    = Path(path).name
                elif (this_rate     != sample_rate
                      or this_channels != channels
                      or this_width    != sampwidth):
                    # Format mismatch — skip this file and warn.
                    # Writing mixed-format PCM produces corrupt audio that
                    # plays at the wrong pitch/speed without any error.
                    print(
                        f"[audio_utils] concat_wavs: skipping {Path(path).name} — "
                        f"format {this_rate}Hz/{this_channels}ch/{this_width * 8}bit "
                        f"does not match reference {ref_name} "
                        f"({sample_rate}Hz/{channels}ch/{sampwidth * 8}bit)"
                    )
                    continue

                frames_list.append(wf.readframes(wf.getnframes()))

        if not frames_list:
            print("[audio_utils] concat_wavs: no usable frames — all files "
                  "were empty or had mismatched formats")
            return False

        silence: bytes = b""
        if silence_secs > 0.0 and sample_rate:
            silence = (b"\x00" * sampwidth) * int(sample_rate * silence_secs * channels)

        with wave.open(str(out_path), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(sampwidth)
            out.setframerate(sample_rate)
            for i, frames in enumerate(frames_list):
                out.writeframes(frames)
                if silence and i < len(frames_list) - 1:
                    out.writeframes(silence)

        return True
    except Exception as exc:
        print(f"[audio_utils] concat_wavs failed: {exc}")
        return False


def stretch_audio(
    input_path:      str,
    output_path:     str,
    target_duration: float,
) -> Tuple[bool, str]:
    """
    Time-stretch input_path to target_duration seconds using pyrubberband.

    The final per-language track must match the English length exactly, so this
    stretches all the way to target_duration, capped only by config.DUB_HARD_STRETCH
    (a high safety limit) to avoid asking pyrubberband for an absurd rate.  The
    "comfortable" threshold (config.DUB_MAX_STRETCH) is applied by the caller
    (dub_engine.sync_to_english) to FLAG rushed panels — not to limit the stretch.
    Returns (success, error_message).
    """
    try:
        import pyrubberband as pyrb
        import soundfile    as sf
        import config
        import runtime_settings as rs

        cap = rs.get_float("dub_hard_stretch", getattr(config, "DUB_HARD_STRETCH", 4.0)) or 4.0
        y, sr   = sf.read(str(input_path))
        current = len(y) / sr
        if current <= 0 or target_duration <= 0:
            return False, (
                f"Bad duration: current={current:.2f}s "
                f"target={target_duration:.2f}s"
            )
        rate = max(1.0 / cap, min(cap, current / target_duration))
        sf.write(str(output_path), pyrb.time_stretch(y, sr, rate), sr)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def pad_to_duration(
    input_path:      str,
    output_path:     str,
    target_duration: float,
) -> Tuple[bool, str]:
    """
    Write input_path padded with trailing silence so it lasts target_duration
    seconds.  If the clip is already at/over the target it is copied unchanged.

    Padding is the preferred way to fill a too-short panel — it is inaudible,
    unlike slowing the voice down.  Returns (success, error_message).
    """
    try:
        import soundfile as sf
        import numpy     as np

        y, sr = sf.read(str(input_path))
        if sr <= 0:
            return False, "bad sample rate"
        current = len(y) / sr
        if target_duration > current:
            pad = int(round((target_duration - current) * sr))
            if pad > 0:
                shape = (pad,) if y.ndim == 1 else (pad, y.shape[1])
                y = np.concatenate([y, np.zeros(shape, dtype=y.dtype)], axis=0)
        sf.write(str(output_path), y, sr)
        return True, ""
    except Exception as exc:
        return False, str(exc)