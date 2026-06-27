"""
core/file_utils.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Shared file-system utility functions.

Previously copy-pasted as static methods in two separate files:
  image_upscaler.py  — ImageUpscaler._copy_as_jpeg() / ._natural_sort_key()
  library_tab.py     — _ScreenshotsManagerDialog._copy_as_jpeg() / ._natural_sort_key()

The two pairs were functionally identical but had minor implementation
differences (library_tab used lazy imports; image_upscaler used module-level
imports).  This module uses module-level imports and is the single source
of truth for both.

Public API
──────────
    copy_as_jpeg(src, dst, quality=95)  → bool
    natural_sort_key(path)              → list
    IMAGE_EXTENSIONS                    → frozenset[str]
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import List

from PIL import Image


# ── Constants ──────────────────────────────────────────────────────────────────

IMAGE_EXTENSIONS: frozenset = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"}
)


# ── Functions ──────────────────────────────────────────────────────────────────

def copy_as_jpeg(src: Path, dst: Path, quality: int = 95) -> bool:
    """
    Copy src to dst as a JPEG file.

    JPEG source files are copied byte-for-byte (no re-encoding, no quality loss).
    All other supported formats (PNG, WEBP, TIFF, BMP) are converted to JPEG
    via PIL at the specified quality level.

    Parameters
    ----------
    src     : source image path
    dst     : destination path (should end in .jpg or .jpeg)
    quality : JPEG quality 1–95 (default 95 = master quality for panel intake)

    Returns True on success, False on any error.
    """
    try:
        if src.suffix.lower() in {".jpg", ".jpeg"}:
            shutil.copy2(str(src), str(dst))
        else:
            Image.open(src).convert("RGB").save(
                str(dst), "JPEG", quality=quality, optimize=True
            )
        return True
    except Exception:
        return False


def natural_sort_key(path: Path) -> List:
    """
    Sort key that treats embedded digit sequences as integers, not strings.

    Guarantees:   screenshot2.jpg  <  screenshot10.jpg
    Without this: screenshot10.jpg <  screenshot2.jpg   (lexicographic — wrong)

    Example
    -------
        "panel_10.jpg"  →  ["panel_", 10, ".jpg"]
        "panel_2.jpg"   →  ["panel_", 2,  ".jpg"]
        2 < 10  →  panel_2 sorts before panel_10  ✓

    Usage
    -----
        files = sorted(folder.iterdir(), key=natural_sort_key)
    """
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]
