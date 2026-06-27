"""
image_upscaler.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
Screenshot intake and Real-ESRGAN upscaling for video-quality panel images.

Two-stage pipeline
───────────────────
  Stage 1 — intake_screenshots()
    You manually screenshot the exact panels from a browser or downloaded PDF.
    Engine renames them to panel_0000.jpg sequence, registers panel rows in DB.
    Output: {output_folder}/panels/panel_0000.jpg …

  Stage 2 — upscale_panels()
    4x upscales every panel with Real-ESRGAN anime model.
    Output: {output_folder}/panels_upscaled/panel_0000.jpg …
    These upscaled images are what video_builder.py uses for final assembly.

Design decisions
────────────────
  • intake_screenshots accepts either a folder path or a list of file paths.
    Files are sorted by natural order: screenshot2.jpg < screenshot10.jpg.
  • Non-JPEG files (PNG, WEBP, TIFF, BMP) are converted to JPEG at quality 95
    (master quality) to preserve original detail from screenshot tools.
    JPEG files are copied directly without re-encoding.
  • All torch/basicsr/realesrgan imports are lazy — they only load when
    upscaling is requested. Other operations work fine without GPU libraries.
  • Upscaling is resumable: already-upscaled panels are skipped automatically.
  • GPU memory is released in a finally block, even on cancellation or error.

Reference: original upscaler_realesrgan.py (~80%)
"""

from __future__ import annotations

from core.file_utils import copy_as_jpeg, natural_sort_key
import shutil
import time
from pathlib import Path
from typing import Callable, List, Optional, Union

from PIL import Image

import config


# ── Module constants ───────────────────────────────────────────────────────────

#: Accepted input formats for screenshot intake.
_IMAGE_EXTS: frozenset = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"}
)

#: Default Real-ESRGAN model — place the .pth file here.
_REALESRGAN_MODEL: Path = (
    config.BASE_DIR / "models" / "RealESRGAN_x4plus_anime_6B.pth"
)

#: Upscale settings kept in one place for easy adjustment.
_UPSCALE_SCALE          = 4
_UPSCALE_TILE_FALLBACKS = (512, 256, 128)  # tried in order on MPS memory errors
_UPSCALE_JPEG_QUALITY   = 85               # final JPEG quality for upscaled images


# ── ImageUpscaler ─────────────────────────────────────────────────────────────

class ImageUpscaler:
    """
    Handle screenshot intake and 4x upscaling for video-quality panel images.
    All database writes go through the injected db object.
    All user feedback goes through on_log / on_progress callbacks.
    """

    def __init__(self, db, output_base: str, on_log: Callable = None):
        self.db          = db
        self.output_base = Path(output_base)
        self.on_log      = on_log
        self._stop_flag  = False

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC METHODS
    # ══════════════════════════════════════════════════════════════════════════

    def intake_screenshots(
        self,
        episode_id:  int,
        source:      Union[str, List[str]],
        on_progress: Callable = None,
    ) -> bool:
        """
        No stage tracking — standalone operation.
        ──────────────────────────────────────────
        Accepts either a folder path (str) or a list of individual file paths.
        Supports JPG, PNG, WEBP, TIFF, BMP.  Non-JPEG files are converted to
        JPEG at quality 95 (master quality) to preserve the original detail.

        Files are sorted by natural order so screenshot2.jpg always comes
        before screenshot10.jpg regardless of filename convention.

        Output: {output_folder}/panels/panel_0000.jpg, panel_0001.jpg …
        Panel rows are created in the database with image_path set.
        episode.panels_folder is updated to the panels/ folder.

        Existing panels for this episode are cleared before the new set is
        registered.  Run intake once with your complete set of images.

        Returns True if at least one panel was registered, False otherwise.
        """
        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return False

        output_folder = Path(ep["output_folder"])
        panels_folder = output_folder / "panels"
        panels_folder.mkdir(parents=True, exist_ok=True)

        # ── Collect and validate source files ─────────────────────────────────
        if isinstance(source, (str, Path)):
            src_files = self._collect_images(Path(source))
            if not src_files:
                self._log(f"No supported image files found in: {source}", "error")
                return False
            self._log(f"Found {len(src_files)} image(s) in source folder", "info")
        else:
            src_files = sorted(
                [Path(f) for f in source
                 if Path(f).suffix.lower() in _IMAGE_EXTS],
                key=natural_sort_key,
            )
            if not src_files:
                self._log("No valid image files in the provided list", "error")
                return False
            self._log(f"Received {len(src_files)} image file(s)", "info")

        # ── Clear any existing panels (fresh intake = fresh panels) ───────────
        existing = self.db.list_panels(episode_id)
        if existing:
            self._log(
                f"Clearing {len(existing)} existing panel(s) "
                f"(and their associated audio) for fresh intake …",
                "warning",
            )
            for panel in existing:
                self.db.delete_panel(panel["id"])

        # ── Process and register ──────────────────────────────────────────────
        self._stop_flag = False
        total  = len(src_files)
        done   = 0
        failed = 0

        self._log(f"Intaking {total} screenshot(s) …", "accent")

        for i, src_path in enumerate(src_files):
            if self._stop_flag:
                self._log("Intake cancelled", "warning")
                break

            out_path = panels_folder / f"panel_{i:04d}.jpg"

            if copy_as_jpeg(src_path, out_path, quality=95):
                self.db.add_panel(
                    episode_id  = episode_id,
                    panel_index = i,
                    image_path  = str(out_path),
                )
                done += 1
            else:
                self._log(
                    f"  Could not process {src_path.name} — skipping", "warning"
                )
                failed += 1

            if on_progress:
                on_progress(i + 1, total)

        if done == 0:
            self._log("No panels were registered — intake failed", "error")
            return False

        # ── Update episode ────────────────────────────────────────────────────
        self.db.update_episode(episode_id, panels_folder=str(panels_folder))
        self._log(
            f"Intake complete — {done} panel(s) registered"
            + (f", {failed} failed" if failed else "") + " ✓",
            "success" if failed == 0 else "warning",
        )
        return True

    # ──────────────────────────────────────────────────────────────────────────

    def upscale_panels(
        self,
        episode_id:  int,
        model_path:  Path    = None,
        on_progress: Callable = None,
    ) -> bool:
        """
        Stage: upscale
        ──────────────
        4x upscales every panel in panels/ using Real-ESRGAN anime model.

        Reads panels from database panel rows (image_path column).
        Writes upscaled images to {output_folder}/panels_upscaled/.
        Updates panels.upscaled_path per panel in the database.
        Sets episode.upscaled_folder when all panels are done.

        Already-upscaled panels (upscaled_path exists and file is on disk)
        are skipped automatically — the stage is safely resumable.

        All heavy imports (torch, basicsr, realesrgan) are lazy: if Real-ESRGAN
        is not installed the method fails cleanly with a clear error message
        without breaking any other engine functionality.

        Returns True on success (≥1 upscaled), False on failure or cancellation.
        """
        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return False

        panels = self.db.list_panels(episode_id)
        if not panels:
            self._log(
                "No panels found — run intake_screenshots first", "error"
            )
            return False

        panels_with_images = [
            p for p in panels
            if p.get("image_path") and Path(p["image_path"]).exists()
        ]
        if not panels_with_images:
            self._log(
                "No panel image files found on disk — "
                "run intake_screenshots first",
                "error",
            )
            return False

        output_folder   = Path(ep["output_folder"])
        upscaled_folder = output_folder / "panels_upscaled"
        upscaled_folder.mkdir(parents=True, exist_ok=True)
        model_path      = model_path or _REALESRGAN_MODEL

        total       = len(panels_with_images)
        done_already = sum(
            1 for p in panels_with_images
            if p.get("upscaled_path") and Path(p["upscaled_path"]).exists()
        )

        self._log(
            f"{total} panel(s) total  |  "
            f"{done_already} already upscaled  |  "
            f"{total - done_already} remaining",
            "info",
        )

        log_id = self.db.log_stage_start(episode_id, "upscale")
        self.db.set_episode_stage(episode_id, "upscale", "running")
        self._stop_flag = False

        # ── Load model ────────────────────────────────────────────────────────
        try:
            upsampler = self._load_realesrgan_model(model_path)
        except RuntimeError as exc:
            error = str(exc)
            self._log(error, "error")
            self.db.set_episode_stage(episode_id, "upscale", "failed", error=error)
            self.db.log_stage_end(log_id, "failed", error=error)
            return False

        # ── Upscale loop ──────────────────────────────────────────────────────
        succeeded = 0
        fail_list: List[str] = []

        try:
            for i, panel in enumerate(panels_with_images):
                if self._stop_flag:
                    self._log("Upscaling cancelled", "warning")
                    return self._abort(episode_id, log_id, "upscale")

                img_path = Path(panel["image_path"])

                # Skip if already done
                existing_up = panel.get("upscaled_path")
                if existing_up and Path(existing_up).exists():
                    succeeded += 1
                    if on_progress:
                        on_progress(i + 1, total)
                    continue

                self._log(f"  [{i + 1}/{total}] {img_path.name} …", "muted")
                out_path = upscaled_folder / img_path.name
                result   = self._upscale_one_image(upsampler, img_path, out_path)

                if result:
                    self.db.update_panel(panel["id"], upscaled_path=str(result))
                    succeeded += 1
                else:
                    fail_list.append(img_path.name)

                if on_progress:
                    on_progress(i + 1, total)

        finally:
            # Release GPU memory regardless of outcome
            self._release_upsampler(upsampler)

        # ── Finalise ──────────────────────────────────────────────────────────
        if fail_list:
            self._log(
                f"Upscale summary: {succeeded} succeeded, "
                f"{len(fail_list)} failed",
                "warning",
            )
            for name in fail_list:
                self._log(f"  Failed: {name}", "warning")
        else:
            self._log(
                f"Upscale complete — {succeeded} panel(s) ✓", "success"
            )

        if succeeded == 0:
            error = "All images failed to upscale"
            self.db.set_episode_stage(episode_id, "upscale", "failed", error=error)
            self.db.log_stage_end(log_id, "failed", error=error)
            return False

        self.db.set_episode_stage(
            episode_id, "upscale", "done",
            output_path = str(upscaled_folder),
        )
        self.db.log_stage_end(
            log_id, "done",
            metadata={"succeeded": succeeded, "failed": len(fail_list)},
        )
        return True

    # ──────────────────────────────────────────────────────────────────────────

    def stop(self):
        """Signal the running operation to cancel after its current image."""
        self._stop_flag = True

    # ══════════════════════════════════════════════════════════════════════════
    # SINGLE-PANEL REPLACE  (Screenshot Manager)
    # ══════════════════════════════════════════════════════════════════════════

    def replace_panel(
        self,
        episode_id:  int,
        panel_index: int,
        src_path:    str,
    ) -> bool:
        """
        Replace one panel image at the given panel_index without disturbing
        any other panel.  The replacement image is copied to the panels/ folder
        with the same canonical name (panel_NNNN.jpg) it already had, so the
        folder stays clean and ordered.

        If the panel did not previously exist (the slot was empty/deleted), a
        new panel row is inserted at that exact index.

        After replacement, upscaled_path is cleared so the upscale stage will
        re-process this panel on the next run.

        Returns True on success, False on any error.
        """
        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return False

        output_folder = Path(ep["output_folder"])
        panels_folder = output_folder / "panels"
        panels_folder.mkdir(parents=True, exist_ok=True)

        out_path = panels_folder / f"panel_{panel_index:04d}.jpg"
        src      = Path(src_path)

        if not src.exists():
            self._log(f"Source file not found: {src}", "error")
            return False

        if not copy_as_jpeg(src, out_path, quality=95):
            self._log(f"Could not copy {src.name} to panels folder", "error")
            return False

        # Update or insert panel row
        replaced = self.db.replace_panel_image(
            episode_id, panel_index, str(out_path)
        )
        if not replaced:
            # Panel row didn't exist — insert it
            self.db.add_panel(
                episode_id  = episode_id,
                panel_index = panel_index,
                image_path  = str(out_path),
            )

        self._log(
            f"Panel {panel_index:04d} replaced with {src.name} ✓", "success"
        )
        return True

    # ══════════════════════════════════════════════════════════════════════════
    # UPSCALING  (private)
    # ══════════════════════════════════════════════════════════════════════════

    def _load_realesrgan_model(self, model_path: Path):
        """
        Load the Real-ESRGAN RRDBNet upsampler.
        All imports are lazy — the rest of the engine works without a GPU
        environment installed.
        Raises RuntimeError with actionable messages on any failure.
        """
        if not model_path.exists():
            raise RuntimeError(
                f"Real-ESRGAN model not found at:\n  {model_path}\n"
                "Download RealESRGAN_x4plus_anime_6B.pth from:\n"
                "  https://github.com/xinntao/Real-ESRGAN/releases\n"
                f"Then place it in:  {model_path.parent}"
            )

        try:
            import torch
            from basicsr.archs.rrdbnet_arch import RRDBNet
            from realesrgan import RealESRGANer
        except ImportError as exc:
            raise RuntimeError(
                f"Real-ESRGAN dependencies not installed: {exc}\n"
                "Fix: pip install torch realesrgan basicsr"
            )

        # ── Select compute device ─────────────────────────────────────────────
        if torch.backends.mps.is_available():
            device   = torch.device("mps")
            use_half = True
            self._log("Using Apple MPS (M-series GPU) ✓", "success")
        elif torch.cuda.is_available():
            device   = torch.device("cuda")
            use_half = False   # CUDA half-precision opt-in left for advanced users
            self._log("Using CUDA GPU ✓", "success")
        else:
            device   = torch.device("cpu")
            use_half = False
            self._log("No GPU found — falling back to CPU (slow)", "warning")

        # ── Load and prepare model weights ────────────────────────────────────
        self._log("Loading Real-ESRGAN model weights …", "info")
        state_dict = torch.load(
            str(model_path),
            map_location  = device,
            weights_only  = False,   # required for .pth files with pickled objects
        )
        if "params_ema" in state_dict:
            state_dict = state_dict["params_ema"]
        elif "params" in state_dict:
            state_dict = state_dict["params"]

        model = RRDBNet(
            num_in_ch  = 3,
            num_out_ch = 3,
            num_feat   = 64,
            num_block  = 6,
            num_grow_ch = 32,
            scale      = _UPSCALE_SCALE,
        )
        model.load_state_dict(state_dict, strict=True)
        model = model.to(device)
        if use_half:
            model = model.half()

        upsampler = RealESRGANer(
            scale      = _UPSCALE_SCALE,
            model_path = str(model_path),
            model      = model,
            tile       = _UPSCALE_TILE_FALLBACKS[0],
            tile_pad   = 10,
            pre_pad    = 0,
            half       = use_half,
            device     = device,
        )
        self._log(
            f"Model ready on {device}  (half={use_half}) ✓", "success"
        )
        return upsampler

    def _upscale_one_image(
        self,
        upsampler,
        img_path: Path,
        out_path: Path,
    ) -> Optional[str]:
        """
        4x upscale one image with automatic tile-size fallback on memory errors.
        Skips if the output already exists and is non-empty.
        Returns the output path string on success, None on total failure.
        """
        import numpy as np  # lazy — only needed in the upscale path

        if out_path.exists() and out_path.stat().st_size > 1000:
            self._log(f"  {img_path.name} — already done, skipping", "muted")
            return str(out_path)

        img_array = np.ascontiguousarray(
            np.array(Image.open(img_path).convert("RGB"))
        )

        for tile_size in _UPSCALE_TILE_FALLBACKS:
            try:
                t0             = time.time()
                upsampler.tile = tile_size
                output, _      = upsampler.enhance(
                    img_array, outscale=_UPSCALE_SCALE
                )
                elapsed = round(time.time() - t0, 1)

                Image.fromarray(output).save(
                    str(out_path), "JPEG",
                    quality=_UPSCALE_JPEG_QUALITY, optimize=True,
                )
                self._log(
                    f"  {img_path.name} → {out_path.name}  ({elapsed}s)",
                    "muted",
                )
                return str(out_path)

            except Exception as exc:
                msg = str(exc)
                is_memory_error = any(
                    k in msg
                    for k in ("convolution_overrideable", "MPS", "memory", "Memory")
                )
                if is_memory_error:
                    self._log(
                        f"  Tile {tile_size} too large — retrying smaller …",
                        "warning",
                    )
                    continue
                else:
                    self._log(
                        f"  {img_path.name} failed: {msg[:200]}", "error"
                    )
                    return None

        self._log(
            f"  {img_path.name} — exhausted all tile sizes", "error"
        )
        return None

    @staticmethod
    def _release_upsampler(upsampler) -> None:
        """
        Delete the upsampler and attempt to flush GPU caches.
        Called in a finally block so it runs even on cancellation or error.
        """
        try:
            del upsampler
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            # MPS has no public cache-clear API; del + GC is the best we can do.
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    # HELPERS  (private)
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _collect_images(folder: Path) -> List[Path]:
        """
        Return all supported image files in folder (one level deep, no recursion),
        sorted by natural order.
        """
        files = [
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
        ]
        return sorted(files, key=natural_sort_key)

    def _abort(self, episode_id: int, log_id: int, stage: str) -> bool:
        """Mark a stage cancelled. Always returns False for clean caller returns."""
        self.db.set_episode_stage(
            episode_id, stage, "failed", error="Cancelled by user"
        )
        self.db.log_stage_end(log_id, "failed", error="Cancelled by user")
        self._log("Cancelled", "warning")
        return False

    def _log(self, msg: str, level: str = "info") -> None:
        if self.on_log:
            self.on_log(msg, level)
        else:
            print(f"[ImageUpscaler] {msg}")