"""
pdf_slicer_downscaler.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
PDF slicing and downscaling engine for AI narration preparation.

One-way pipeline
────────────────
  PDF → load pages → slice (three modes: slice/page/merge)
     → optimize (downscale + grayscale + compress)
     → ai_narrator/optimized/   ready for Claude upload

This is the AI path. Images produced here are small and throwaway — they will
never be used in the final video. They exist only to give Claude the panel
structure and layout for writing narration.

Design decisions
────────────────
  • All steps are resumable — if optimized images already exist, slicing
    and re-optimization are skipped.
  • pdf2image is lazy-imported — if pdf2image is not installed, the user
    gets a clear error message with installation instructions.
  • Aggressive mode overrides are applied to a local copy of OptimizeParams
    so the caller's instance is never mutated.
  • Counter.most_common() replaces the buggy max(set, key=list.count) from
    the original code — unambiguous tie-breaking and O(n) complexity.
  • Batch width-normalisation in slice_by_merge prevents white gaps on
    narrower pages.

Reference: original slicer.py (~85%), optimizer.py (~90%)
"""

from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass, field, replace as dc_replace
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PIL import Image, ImageFilter

import config


# ── Parameter dataclasses ──────────────────────────────────────────────────────

@dataclass
class SliceParams:
    """Controls PDF → image slicing."""
    mode:            str  = field(default_factory=lambda: config.NARR_MODE)
    slice_height:    int  = field(default_factory=lambda: config.NARR_SLICE_HEIGHT)
    merge_count:     int  = field(default_factory=lambda: config.NARR_MERGE_COUNT)
    dpi:             int  = field(default_factory=lambda: config.PDF_DPI)
    skip_first_last: bool = field(default_factory=lambda: config.PDF_SKIP_FIRST_LAST)
    jpeg_quality:    int  = field(default_factory=lambda: config.PDF_JPEG_QUALITY)


@dataclass
class OptimizeParams:
    """Controls image optimization for Claude upload."""
    compression_mode: str  = field(default_factory=lambda: config.OPT_COMPRESSION_MODE)
    jpeg_quality:     int  = field(default_factory=lambda: config.OPT_JPEG_QUALITY)
    target_kb:        int  = field(default_factory=lambda: config.OPT_TARGET_KB)
    min_quality:      int  = field(default_factory=lambda: config.OPT_MIN_QUALITY)
    max_width:        int  = field(default_factory=lambda: config.OPT_MAX_WIDTH)
    grayscale:        bool = field(default_factory=lambda: config.OPT_GRAYSCALE)
    autocrop:         bool = field(default_factory=lambda: config.OPT_AUTOCROP)
    sharpen:          bool = field(default_factory=lambda: config.OPT_SHARPEN)


# ── PdfSlicerDownscaler ────────────────────────────────────────────────────────

class PdfSlicer:
    """
    Prepare a PDF for Claude narration by slicing and optimizing.
    All database writes go through the injected db object.
    All user feedback goes through on_log / on_progress callbacks.
    """

    def __init__(self, db, output_base: str, on_log: Callable = None):
        self.db          = db
        self.output_base = Path(output_base)
        self.on_log      = on_log
        self._stop_flag  = False

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC METHOD
    # ══════════════════════════════════════════════════════════════════════════

    def prepare_for_narration(
        self,
        episode_id:   int,
        slice_params: SliceParams   = None,
        opt_params:   OptimizeParams = None,
        on_progress:  Callable       = None,
    ) -> bool:
        """
        Stage: extract  (PDF path)
        ───────────────────────────
        Step 1 — Slice:   load the PDF and cut it into images using the
                          configured mode (slice / page / merge).
        Step 2 — Optimize: downscale, optionally grayscale, and compress
                           every slice for efficient Claude upload.

        Output paths (by convention, not DB columns):
            {output_folder}/ai_narrator/slices/      raw slices
            {output_folder}/ai_narrator/optimized/   Claude-ready images

        Both steps are resumable: if the destination folder already has
        images from a previous run that step is skipped automatically.

        on_progress(current, total) is called during the optimize step.
        Returns True on success, False on cancellation or error.
        """
        sp = slice_params or SliceParams()
        op = opt_params   or OptimizeParams()

        ep = self.db.get_episode(episode_id)
        if not ep:
            self._log("Episode not found", "error")
            return False

        source_path   = ep["source_path"]
        output_folder = Path(ep["output_folder"])
        slices_dir    = output_folder / "ai_narrator" / "slices"
        opt_dir       = output_folder / "ai_narrator" / "optimized"
        slices_dir.mkdir(parents=True, exist_ok=True)
        opt_dir.mkdir(parents=True, exist_ok=True)

        log_id = self.db.log_stage_start(episode_id, "extract")
        self.db.set_episode_stage(episode_id, "extract", "running")
        self._stop_flag = False

        try:
            # ── Step 1: Slice ─────────────────────────────────────────────────
            existing_slices = sorted(slices_dir.glob("*.jpg"))
            if existing_slices:
                self._log(
                    f"Slices already exist ({len(existing_slices)} file(s)) — "
                    f"skipping slice step",
                    "info",
                )
                n_pages = len(existing_slices)  # best approximation
            else:
                self._log(
                    f"Loading PDF (dpi={sp.dpi}, "
                    f"skip_first_last={sp.skip_first_last}) …",
                    "accent",
                )
                pages = self._load_pdf_pages(source_path, sp)

                if self._stop_flag:
                    return self._abort(episode_id, log_id, "extract")

                n_pages = len(pages)
                self.db.update_episode(episode_id, total_pages=n_pages)

                self._log(
                    f"Slicing {n_pages} page(s) in '{sp.mode}' mode …", "info"
                )
                slices_produced = self._run_slicer(pages, slices_dir, sp)
                self._log(
                    f"{len(slices_produced)} slice(s) saved → ai_narrator/slices/",
                    "success",
                )

            if self._stop_flag:
                return self._abort(episode_id, log_id, "extract")

            # ── Step 2: Optimize ──────────────────────────────────────────────
            existing_opt = sorted(opt_dir.glob("*.jpg"))
            if existing_opt:
                self._log(
                    f"Optimized images already exist ({len(existing_opt)} file(s)) — "
                    f"skipping optimization step",
                    "info",
                )
                n_optimized = len(existing_opt)
            else:
                slices_to_optimize = sorted(slices_dir.glob("*.jpg"))
                if not slices_to_optimize:
                    raise RuntimeError(
                        "No slices found to optimize — "
                        "the slice step may have failed"
                    )

                self._log(
                    f"Optimizing {len(slices_to_optimize)} slice(s) for Claude "
                    f"(mode={op.compression_mode}) …",
                    "accent",
                )
                n_optimized = self._run_optimizer(
                    slices_to_optimize, opt_dir, op, on_progress
                )
                self._log(
                    f"{n_optimized} image(s) ready → ai_narrator/optimized/",
                    "success",
                )

            if self._stop_flag:
                return self._abort(episode_id, log_id, "extract")

            # ── Finalise ──────────────────────────────────────────────────────
            self.db.set_episode_stage(episode_id, "extract", "done")
            self.db.log_stage_end(
                log_id, "done",
                metadata={
                    "optimized_folder": str(opt_dir),
                    "optimized_count":  n_optimized,
                },
            )
            self._log("Narration preparation complete ✓", "success")
            return True

        except Exception as exc:
            error = str(exc)
            self._log(f"prepare_for_narration failed: {error}", "error")
            self.db.set_episode_stage(episode_id, "extract", "failed", error=error)
            self.db.log_stage_end(log_id, "failed", error=error)
            return False

    # ──────────────────────────────────────────────────────────────────────────

    def stop(self):
        """Signal the running operation to cancel after its current step."""
        self._stop_flag = True

    # ══════════════════════════════════════════════════════════════════════════
    # SLICING  (private)
    # ══════════════════════════════════════════════════════════════════════════

    def _load_pdf_pages(
        self,
        pdf_path: str,
        p:        SliceParams,
    ) -> List[Image.Image]:
        """
        Rasterise every page of the PDF via pdf2image.
        If skip_first_last is True and there are more than 2 pages,
        the first (cover) and last (back) pages are removed.
        Returns a list of PIL Image objects.
        """
        try:
            from pdf2image import convert_from_path
        except ImportError:
            raise RuntimeError(
                "pdf2image is not installed.\n"
                "Fix: pip install pdf2image  "
                "(also requires poppler on PATH — "
                "brew install poppler on macOS)"
            )

        self._log(f"Rasterising PDF at {p.dpi} DPI …", "info")
        pages = convert_from_path(pdf_path, dpi=p.dpi)

        if p.skip_first_last and len(pages) > 2:
            pages = pages[1:-1]
            self._log(
                f"Skipped cover + back page — {len(pages)} page(s) remaining",
                "muted",
            )
        return pages

    @staticmethod
    def _stitch_pages_to_strip(pages: List[Image.Image]) -> Image.Image:
        """
        Normalise all pages to the most frequently occurring page width,
        then stack them vertically into one continuous strip.

        Using the most-common width avoids a single rogue wide or narrow
        page stretching or shrinking an entire chapter.
        """
        widths   = [p.size[0] for p in pages]
        target_w = Counter(widths).most_common(1)[0][0]

        normalised: List[Image.Image] = []
        for page in pages:
            if page.size[0] != target_w:
                ratio = target_w / page.size[0]
                new_h = int(page.size[1] * ratio)
                page  = page.resize((target_w, new_h), Image.LANCZOS)
            normalised.append(page)

        total_h = sum(pg.size[1] for pg in normalised)
        strip   = Image.new("RGB", (target_w, total_h), (255, 255, 255))
        y = 0
        for pg in normalised:
            strip.paste(pg, (0, y))
            y += pg.size[1]

        return strip

    def _run_slicer(
        self,
        pages:         List[Image.Image],
        output_folder: Path,
        p:             SliceParams,
    ) -> List[str]:
        """Dispatch to the correct slice mode, return list of output paths."""
        if p.mode == "slice":
            return self._slice_by_height(pages, output_folder, p)
        elif p.mode == "page":
            return self._slice_by_page(pages, output_folder, p)
        elif p.mode == "merge":
            return self._slice_by_merge(pages, output_folder, p)
        else:
            raise ValueError(
                f"Unknown slice mode: '{p.mode}'. "
                "Expected 'slice', 'page', or 'merge'."
            )

    def _slice_by_height(
        self,
        pages:         List[Image.Image],
        output_folder: Path,
        p:             SliceParams,
    ) -> List[str]:
        """
        Stitch all pages into one vertical strip, then cut into
        p.slice_height-pixel tall chunks from top to bottom.
        """
        strip        = self._stitch_pages_to_strip(pages)
        total_height = strip.size[1]
        width        = strip.size[0]
        paths: List[str] = []
        n = 0
        top = 0

        while top < total_height:
            if self._stop_flag:
                break
            bottom = min(top + p.slice_height, total_height)
            chunk  = strip.crop((0, top, width, bottom))
            out    = output_folder / f"slice_{n + 1:03d}.jpg"
            chunk.save(str(out), "JPEG", quality=p.jpeg_quality)
            paths.append(str(out))
            n   += 1
            top += p.slice_height

        self._log(
            f"  Slice mode: {n} slice(s)  "
            f"(≤{p.slice_height}px each, {width}px wide)",
            "muted",
        )
        return paths

    def _slice_by_page(
        self,
        pages:         List[Image.Image],
        output_folder: Path,
        p:             SliceParams,
    ) -> List[str]:
        """Save each page as its own image file, no stitching."""
        paths: List[str] = []
        for i, page in enumerate(pages, 1):
            if self._stop_flag:
                break
            out = output_folder / f"slice_{i:03d}.jpg"
            page.save(str(out), "JPEG", quality=p.jpeg_quality)
            paths.append(str(out))

        self._log(f"  Page mode: {len(paths)} page(s)", "muted")
        return paths

    def _slice_by_merge(
        self,
        pages:         List[Image.Image],
        output_folder: Path,
        p:             SliceParams,
    ) -> List[str]:
        """
        Merge p.merge_count consecutive pages into one tall image.
        Pages within each batch are width-normalised before merging so
        narrower pages are not left with white gaps on the right side.
        """
        paths: List[str] = []
        idx   = 0
        count = 1

        while idx < len(pages):
            if self._stop_flag:
                break

            batch = pages[idx: idx + p.merge_count]

            # Normalise widths within this batch to the most common width
            batch_w = Counter(pg.size[0] for pg in batch).most_common(1)[0][0]
            normalised: List[Image.Image] = []
            for pg in batch:
                if pg.size[0] != batch_w:
                    ratio = batch_w / pg.size[0]
                    new_h = int(pg.size[1] * ratio)
                    pg    = pg.resize((batch_w, new_h), Image.LANCZOS)
                normalised.append(pg)

            total_h = sum(pg.size[1] for pg in normalised)
            canvas  = Image.new("RGB", (batch_w, total_h), (255, 255, 255))
            y = 0
            for pg in normalised:
                canvas.paste(pg, (0, y))
                y += pg.size[1]

            out = output_folder / f"slice_{count:03d}.jpg"
            canvas.save(str(out), "JPEG", quality=p.jpeg_quality)
            paths.append(str(out))
            count += 1
            idx   += p.merge_count

        self._log(
            f"  Merge mode: {len(paths)} image(s) "
            f"({p.merge_count} page(s) per image)",
            "muted",
        )
        return paths

    # ══════════════════════════════════════════════════════════════════════════
    # OPTIMIZATION  (private)
    # ══════════════════════════════════════════════════════════════════════════

    def _run_optimizer(
        self,
        slices:      List[Path],
        out_folder:  Path,
        p:           OptimizeParams,
        on_progress: Callable = None,
    ) -> int:
        """
        Optimize every slice and log a per-image and overall summary.
        Aggressive mode overrides are applied to a local copy of params
        so the caller's OptimizeParams object is never mutated.
        Returns the count of output images produced.
        """
        # Apply aggressive overrides to a local copy only
        if p.compression_mode == "aggressive":
            p = dc_replace(
                p,
                grayscale    = True,
                max_width    = 700,
                jpeg_quality = 45,
                autocrop     = True,
            )

        self._log(
            f"  Mode: {p.compression_mode}  |  "
            f"max_width: {p.max_width}px  |  "
            f"grayscale: {p.grayscale}  |  "
            f"autocrop: {p.autocrop}",
            "muted",
        )

        total_before = 0
        total_after  = 0
        n_done       = 0

        for i, img_path in enumerate(slices):
            if self._stop_flag:
                break

            out_path = out_folder / img_path.name
            before, after = self._optimize_one_image(img_path, out_path, p)
            total_before += before
            total_after  += after
            n_done       += 1

            reduction = round((1 - after / before) * 100, 1) if before else 0
            self._log(
                f"  {img_path.name:<28} "
                f"{before // 1024:>5} KB → "
                f"{after // 1024:>4} KB  "
                f"(-{reduction}%)",
                "muted",
            )

            if on_progress:
                on_progress(i + 1, len(slices))

        if total_before > 0:
            pct = round((1 - total_after / total_before) * 100, 1)
            self._log(
                f"Optimizer total: "
                f"{total_before / 1048576:.2f} MB → "
                f"{total_after / 1048576:.2f} MB  "
                f"(-{pct}%)",
                "info",
            )

        return n_done

    @staticmethod
    def _optimize_one_image(
        img_path: Path,
        out_path: Path,
        p:        OptimizeParams,
    ) -> Tuple[int, int]:
        """
        Apply autocrop → resize → compress to a single image.
        Returns (original_file_bytes, output_file_bytes).
        """
        original_bytes = img_path.stat().st_size
        img = Image.open(img_path).convert("RGB")

        # Step 1: Autocrop white border padding
        if p.autocrop:
            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)

        # Step 2: Downscale to max_width (proportional height)
        if img.width > p.max_width:
            new_h = int(img.height * p.max_width / img.width)
            img   = img.resize((p.max_width, new_h), Image.LANCZOS)
            if p.sharpen:
                img = img.filter(
                    ImageFilter.UnsharpMask(radius=0.5, percent=80, threshold=3)
                )

        # Step 3: Compress to output
        if p.compression_mode == "target_size":
            img_bytes, _ = PdfSlicer._compress_to_target(
                img, p.target_kb, p.min_quality, p.grayscale
            )
            out_path.write_bytes(img_bytes)
        else:
            # "quality" or "aggressive" (already overridden via dc_replace)
            if p.grayscale:
                img = img.convert("L").convert("RGB")
            img.save(str(out_path), "JPEG", quality=p.jpeg_quality, optimize=True)

        return original_bytes, out_path.stat().st_size

    @staticmethod
    def _compress_to_target(
        img:         Image.Image,
        target_kb:   int,
        min_quality: int,
        grayscale:   bool,
    ) -> Tuple[bytes, int]:
        """
        Progressively lower JPEG quality in steps of 5 until the output
        is at or below target_kb.  Returns (bytes, quality_used).
        """
        if grayscale:
            img = img.convert("L").convert("RGB")

        for quality in range(85, min_quality - 1, -5):
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=True)
            if buf.tell() / 1024 <= target_kb:
                return buf.getvalue(), quality

        # Still over target at min_quality — return it anyway
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=min_quality, optimize=True)
        return buf.getvalue(), min_quality

    # ══════════════════════════════════════════════════════════════════════════
    # HELPERS  (private)
    # ══════════════════════════════════════════════════════════════════════════

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
            print(f"[PdfSlicer] {msg}")
