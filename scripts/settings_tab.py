"""
settings_tab.py — ManhwaStudio v2
─────────────────────────────────────────────────────────────────────────────
All application settings in one place, organised into six sub-sections so
the tab is never a single giant scrolling form.

Layout
──────
  Left nav (185 px) — section buttons + [SAVE ALL] + [RESET SECTION]
  Right content      — scrollable form for the selected section

Sub-sections
────────────
  API KEYS   NVIDIA NIM key, Vision model selection
  SLICER     PDF slicing mode for Claude narration (slice / page / merge)
  OPTIMIZER  Image downscaling + compression for Claude upload
  DETECTION  Video panel detection thresholds and audio settings
  TTS        Qwen3 TTS runtime paths + generation defaults
  DUBBING    faster-whisper split, silence snap, RMS normalisation

Settings storage
────────────────
  Every editable value is persisted via db.set_setting(key, value) and
  loaded via db.get_setting(key, default).  Defaults come from config.py
  so first-run behaviour is identical to config.py without DB rows.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

import config
from ui.theme import (
    BG, PANEL, PANEL2, BORDER, ACCENT, ACCENT2,
    TEXT, TEXT_DIM, MUTED, SUCCESS, ERROR, WARNING, INFO,
    BTN_BG, BTN_FG,
    _F, FL, FB, FS, FBTN,
)
from ui.widgets import _FlatBtn, _btn


# ── Label column width ────────────────────────────────────────────────────────
_LBL_W = 28


# ── Per-section default values (derived from config.py) ──────────────────────

_DEFAULTS: Dict[str, object] = {
    # API
    "nvidia_api_key":           "",
    "nvidia_vision_model":      config.NVIDIA_VISION_MODEL,

    # Slicer
    "narr_mode":                config.NARR_MODE,
    "narr_slice_height":        str(config.NARR_SLICE_HEIGHT),
    "narr_merge_count":         str(config.NARR_MERGE_COUNT),
    "narr_images_per_batch":    str(config.NARR_IMAGES_PER_BATCH),
    "pdf_dpi":                  str(config.PDF_DPI),
    "pdf_skip_first_last":      config.PDF_SKIP_FIRST_LAST,
    "pdf_jpeg_quality":         str(config.PDF_JPEG_QUALITY),

    # Optimizer
    "opt_compression_mode":     config.OPT_COMPRESSION_MODE,
    "opt_jpeg_quality":         str(config.OPT_JPEG_QUALITY),
    "opt_target_kb":            str(config.OPT_TARGET_KB),
    "opt_min_quality":          str(config.OPT_MIN_QUALITY),
    "opt_max_width":            str(config.OPT_MAX_WIDTH),
    "opt_grayscale":            config.OPT_GRAYSCALE,
    "opt_autocrop":             config.OPT_AUTOCROP,
    "opt_sharpen":              config.OPT_SHARPEN,

    # Detection
    "detect_mode":              config.DETECT_MODE,
    "detect_silence_db":        str(config.DETECT_SILENCE_DB),
    "detect_min_silence":       str(config.DETECT_MIN_SILENCE),
    "detect_threshold":         str(config.DETECT_THRESHOLD),
    "detect_min_scene":         str(config.DETECT_MIN_SCENE),
    "detect_frame_skip":        str(config.DETECT_FRAME_SKIP),
    "detect_merge_window":      str(config.DETECT_MERGE_WINDOW),
    "detect_priority":          str(config.DETECT_PRIORITY),
    "detect_workers":           str(config.DETECT_WORKERS),
    "whisper_model":            config.WHISPER_MODEL,
    "screenshot_offset":        str(config.SCREENSHOT_OFFSET),

    # TTS
    "tts_recommended_voice_design":  config.TTS_RECOMMENDED_MODELS.get("VoiceDesign", "1.7B-VoiceDesign"),
    "tts_recommended_voice_clone":   config.TTS_RECOMMENDED_MODELS.get("VoiceClone",  "1.7B-Base"),
    "tts_recommended_custom_voice":  config.TTS_RECOMMENDED_MODELS.get("CustomVoice", "1.7B-CustomVoice"),

    # NVIDIA inference
    "nvidia_batch_size":        "30",
    "nvidia_max_concurrent":    "6",

    # LM Studio
    "lm_studio_url":            "http://localhost:1234/v1",
    "lm_studio_model":          "",
    "lm_studio_context_length": "32768",
    "lm_studio_max_concurrent": "4",
    "lm_studio_batch_size":     "6",

    # Dubbing
    "dub_whisper_model":        config.DUB_WHISPER_MODEL,
    "dub_snap_window_ms":       str(config.DUB_SNAP_WINDOW_MS),
    "dub_normalize_rms":        str(config.DUB_NORMALIZE_RMS),
    "dub_continuous_timeout":   str(config.DUB_CONTINUOUS_TIMEOUT),
}

# ── Section metadata ──────────────────────────────────────────────────────────

_SECTIONS: List[Tuple[str, str, str]] = [
    ("api_keys",   "API KEYS",   "NVIDIA NIM key and model selection"),
    ("lm_studio",  "LM STUDIO",  "Local LM Studio connection and model management"),
    ("slicer",     "SLICER",     "PDF slicing for Claude narration"),
    ("optimizer",  "OPTIMIZER",  "Image compression for AI upload"),
    ("detection",  "DETECTION",  "Video panel detection thresholds"),
    ("tts",        "TTS",        "Qwen3 TTS runtime configuration"),
    ("dubbing",    "DUBBING",    "Audio alignment and normalisation"),
]


# ── Local widget helpers ──────────────────────────────────────────────────────

def _div(parent):
    """Settings-tab divider — slightly more padding than the standard one."""
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=8)


def _sec_header(parent, text: str):
    f = tk.Frame(parent, bg=BG)
    f.pack(fill="x", pady=(16, 6))
    tk.Label(f, text=text, font=FL, bg=BG, fg=ACCENT).pack(side="left")
    tk.Frame(f, bg=BORDER, height=1).pack(
        side="left", fill="x", expand=True, padx=(8, 0))


# ── Scrollable frame factory ──────────────────────────────────────────────────

def _make_scrollable(parent: tk.Widget) -> tk.Frame:
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
    sb     = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner  = tk.Frame(canvas, bg=BG)

    inner.bind("<Configure>",
               lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    canvas.bind_all("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 * int(e.delta / 120), "units"))
    return inner


# ── Settings tab ──────────────────────────────────────────────────────────────

class SettingsTab(tk.Frame):
    """
    Six sub-section settings tab.
    All values are persisted to the database as key-value strings.
    Defaults come from config.py on first run.
    """

    def __init__(self, parent, db, on_log: Callable):
        super().__init__(parent, bg=BG)

        self.db      = db
        self._on_log = on_log

        self._vars: Dict[str, tk.Variable] = {}
        self._section_frames: Dict[str, tk.Frame] = {}
        self._nav_btns:       Dict[str, tk.Button] = {}
        self._current_section: Optional[str]       = None

        self._build()
        for key, _name, _desc in _SECTIONS:
            self._show_section(key)
        self._show_section(_SECTIONS[0][0])

    # ══════════════════════════════════════════════════════════════════════════
    # BUILD
    # ══════════════════════════════════════════════════════════════════════════

    def _build(self):
        pw = tk.PanedWindow(self, orient="horizontal", bg=BG,
                            sashwidth=5, sashrelief="flat")
        pw.pack(fill="both", expand=True)

        left  = tk.Frame(pw, bg=BG, width=185)
        right = tk.Frame(pw, bg=BG)
        pw.add(left,  minsize=160)
        pw.add(right, minsize=500)

        self._content_parent = right
        self._build_nav(left)

    def _build_nav(self, parent: tk.Frame):
        tk.Label(parent, text="SETTINGS", font=FL,
                 bg=BG, fg=ACCENT).pack(anchor="w", padx=12, pady=(16, 8))

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=8)

        for key, name, _desc in _SECTIONS:
            b = _btn(parent, name, lambda k=key: self._show_section(k),
                     bg=BG, fg=TEXT_DIM, pady=8, padx=12)
            b.pack(fill="x", pady=1)
            self._nav_btns[key] = b

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(12, 0))

        _btn(parent, "SAVE ALL", self._save_all,
             bg=ACCENT, fg="#000", pady=8
             ).pack(fill="x", padx=8, pady=(8, 2))

        _btn(parent, "RESET SECTION", self._reset_current_section,
             bg=PANEL2, pady=6
             ).pack(fill="x", padx=8, pady=2)

        info = tk.Label(parent,
            text="System paths are set in\nconfig.py  (not saved here)",
            font=(_F, 7), bg=BG, fg=MUTED, justify="center")
        info.pack(side="bottom", pady=12, padx=4)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION SWITCHING
    # ══════════════════════════════════════════════════════════════════════════

    def _show_section(self, key: str):
        if key not in self._section_frames:
            outer = tk.Frame(self._content_parent, bg=BG)
            self._section_frames[key] = outer
            inner = _make_scrollable(outer)
            content = tk.Frame(inner, bg=BG)
            content.pack(fill="both", expand=True, padx=32, pady=20)
            builder = getattr(self, f"_build_{key}", None)
            if builder:
                builder(content)

        for k, f in self._section_frames.items():
            if k == key:
                f.pack(fill="both", expand=True)
            else:
                f.pack_forget()

        for k, b in self._nav_btns.items():
            if k == key:
                b.config(fg=ACCENT, bg=PANEL2)
            else:
                b.config(fg=TEXT_DIM, bg=BG)

        self._current_section = key

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION CONTENT BUILDERS
    # ══════════════════════════════════════════════════════════════════════════

    def _build_api_keys(self, f: tk.Frame):
        _sec_header(f, "NVIDIA NIM API")
        self._field_secret(f, "API Key", "nvidia_api_key",
                           "Get your key at build.nvidia.com → any model → Get API Key")
        self._info(f, "Text Model (config.py)",  config.NVIDIA_MODEL)
        self._info(f, "Vision Model (config.py)", config.NVIDIA_VISION_MODEL)
        self._info(f, "Base URL",                 config.NVIDIA_BASE_URL)

        _div(f)
        _sec_header(f, "PATHS  (config.py — read only)")
        self._info(f, "Base dir",    str(config.BASE_DIR))
        self._info(f, "Output dir",  str(config.OUTPUT_DIR))
        self._info(f, "Voices dir",  str(config.VOICES_DIR))
        self._info(f, "Database",    str(config.DB_PATH))
        self._info(f, "Conda Python", config.CONDA_PYTHON)

        _div(f)
        _sec_header(f, "NVIDIA INFERENCE SETTINGS")
        tk.Label(f,
            text=(
                "Batch size: panels sent per API call.  Llama 3.3 70B has 128K "
                "context — 30 panels per call is well within limits.\n"
                "Max concurrent: parallel language workers.  At 6 concurrent "
                "workers × ~20 s each, translate runs 9× faster than sequential "
                "while staying far below the 40 RPM free-tier limit."
            ),
            font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=500,
        ).pack(anchor="w", pady=(0, 8))

        self._field(f, "Batch size",      "nvidia_batch_size",
                    "Panels per API call.  128K context = generous headroom.  "
                    "Default: 30")
        self._field(f, "Max concurrent",  "nvidia_max_concurrent",
                    "Parallel language workers for TRANSLATE and REFINE.  "
                    "Default: 6  (safe within 40 RPM free tier)")

        _div(f)
        self._save_btn(f, ["nvidia_api_key", "nvidia_batch_size",
                           "nvidia_max_concurrent"])

    def _build_lm_studio(self, f: tk.Frame):
        _sec_header(f, "LM STUDIO CONNECTION  (local inference)")
        tk.Label(f,
            text=(
                "LM Studio runs models locally via its OpenAI-compatible REST API.\n"
                "No internet connection needed once the model is downloaded.\n"
                "REFINE and TRANSLATE stages load the model automatically, "
                "then unload it immediately after — freeing GPU RAM for UPSCALE."
            ),
            font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=500,
        ).pack(anchor="w", pady=(0, 10))

        self._field(f, "Server URL",   "lm_studio_url",
                    "LM Studio endpoint.  Default: http://localhost:1234/v1")
        self._field(f, "Model name",   "lm_studio_model",
                    "Model key as shown in LM Studio (e.g. qwen/qwen3-35b-a3b)")
        self._field(f, "Context length", "lm_studio_context_length",
                    "Tokens loaded into context.  "
                    "Passed at load time — ignored if model already loaded.  "
                    "Default: 32768  Max: model-dependent (e.g. 262144)")

        _div(f)
        _sec_header(f, "PARALLEL INFERENCE")
        tk.Label(f,
            text=(
                "Both REFINE and TRANSLATE use these values when LM Studio is "
                "selected.\nBatch size: panels per API call — reduce if you hit "
                "'Context size exceeded'.\nMatch Max Concurrent to LM Studio's "
                "'Max Concurrent Predictions' in the model loader."
            ),
            font=FS, bg=BG, fg=TEXT_DIM, justify="left", wraplength=500,
        ).pack(anchor="w", pady=(0, 6))

        self._field(f, "Panels per batch", "lm_studio_batch_size",
                    "Panels sent per API call.  "
                    "Reduce if hitting context errors.  Default: 6")

        conc_row = tk.Frame(f, bg=BG)
        conc_row.pack(fill="x", pady=(0, 8))
        tk.Label(conc_row, text="Max concurrent:", font=FS, bg=BG,
                 fg=TEXT_DIM, width=20, anchor="w").pack(side="left")

        saved_conc = int(self.db.get_setting("lm_studio_max_concurrent", "4"))
        self._lms_concurrent_var = tk.IntVar(value=saved_conc)
        conc_val_lbl = tk.Label(conc_row, text=str(saved_conc),
                                font=FS, bg=BG, fg=ACCENT, width=3, anchor="w")
        conc_val_lbl.pack(side="right", padx=(0, 8))

        def _on_conc_change(val):
            v = int(float(val))
            self._lms_concurrent_var.set(v)
            conc_val_lbl.config(text=str(v))
            self.db.set_setting("lm_studio_max_concurrent", str(v))

        tk.Scale(conc_row, from_=1, to=8,
                 orient="horizontal", variable=self._lms_concurrent_var,
                 command=_on_conc_change,
                 bg=BG, fg=TEXT, highlightthickness=0,
                 troughcolor=BTN_BG, activebackground=ACCENT,
                 length=180, showvalue=False,
                 ).pack(side="left", padx=(4, 0))

        _div(f)
        _sec_header(f, "ACTIONS")
        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(fill="x", pady=(0, 8))

        _btn(btn_row, "TEST CONNECTION",
             self._lms_test_connection,
             bg=PANEL2, pady=5, padx=10).pack(side="left", padx=(0, 8))
        _btn(btn_row, "LOAD MODEL NOW",
             self._lms_load_model,
             bg=PANEL2, pady=5, padx=10).pack(side="left", padx=(0, 8))
        _btn(btn_row, "UNLOAD MODEL",
             self._lms_unload_model,
             bg=PANEL2, pady=5, padx=10).pack(side="left")

        tk.Label(f,
            text=(
                "LOAD MODEL NOW  — manually pre-load the model (optional; "
                "stages load it automatically).\n"
                "UNLOAD MODEL    — immediately free GPU RAM "
                "(useful before running UPSCALE)."
            ),
            font=FS, bg=BG, fg=MUTED, justify="left", wraplength=500,
        ).pack(anchor="w", pady=(0, 10))

        _div(f)
        self._save_btn(f, ["lm_studio_url", "lm_studio_model",
                           "lm_studio_context_length",
                           "lm_studio_batch_size", "lm_studio_max_concurrent"])

    def _lms_test_connection(self):
        import threading
        def _bg():
            try:
                import lmstudio as lms
                api_host = lms.Client.find_default_local_api_host()
                if api_host:
                    msg = f"LM Studio reachable — SDK port: {api_host} ✓"
                    lvl = "success"
                else:
                    msg = ("Cannot reach LM Studio — make sure the app is open "
                           "and a model is loaded")
                    lvl = "error"
            except ImportError:
                msg = "lmstudio SDK not installed — run: pip install lmstudio"
                lvl = "error"
            except Exception as exc:
                msg = f"Connection test error: {exc}"
                lvl = "error"
            self.after(0, lambda: self._log(msg, lvl))
        threading.Thread(target=_bg, daemon=True, name="lms-test").start()

    def _lms_load_model(self):
        import threading
        url   = (self._vars.get("lm_studio_url")   or tk.StringVar()).get().strip()
        model = (self._vars.get("lm_studio_model") or tk.StringVar()).get().strip()
        if not model:
            self._log("Model name is empty — enter it above and save first", "error")
            return
        def _bg():
            try:
                from ai_engine import load_lmstudio_model
                self.after(0, lambda: self._log(f"Loading '{model}' into LM Studio …", "info"))
                ok = load_lmstudio_model(url, model)
                if ok:
                    self.after(0, lambda: self._log(f"'{model}' loaded ✓", "success"))
                else:
                    self.after(0, lambda: self._log(
                        "Load failed — check LM Studio is running and model exists", "error"))
            except ImportError:
                self.after(0, lambda: self._log(
                    "lmstudio SDK not installed — run: pip install lmstudio", "error"))
            except Exception as exc:
                self.after(0, lambda m=str(exc): self._log(f"Load error: {m}", "error"))
        threading.Thread(target=_bg, daemon=True, name="lms-load").start()

    def _lms_unload_model(self):
        import threading
        url   = (self._vars.get("lm_studio_url")   or tk.StringVar()).get().strip()
        model = (self._vars.get("lm_studio_model") or tk.StringVar()).get().strip()
        if not model:
            self._log("Model name is empty — enter it above and save first", "error")
            return
        def _bg():
            try:
                from ai_engine import unload_lmstudio_model
                self.after(0, lambda: self._log(f"Unloading '{model}' …", "info"))
                ok = unload_lmstudio_model(model, url)
                if ok:
                    self.after(0, lambda: self._log(
                        f"'{model}' unloaded — GPU RAM freed ✓", "success"))
                else:
                    self.after(0, lambda: self._log(
                        "Unload failed — is the model currently loaded?", "error"))
            except ImportError:
                self.after(0, lambda: self._log(
                    "lmstudio SDK not installed — run: pip install lmstudio", "error"))
            except Exception as exc:
                self.after(0, lambda m=str(exc): self._log(f"Unload error: {m}", "error"))
        threading.Thread(target=_bg, daemon=True, name="lms-unload").start()

    def _build_slicer(self, f: tk.Frame):
        _sec_header(f, "PDF SLICER  (AI narration path)")
        self._dropdown(f, "Slice Mode", "narr_mode",
                       ["page", "slice", "merge"],
                       "page=one image per page  slice=cut by height  merge=join pages")
        self._field(f, "Slice Height (px)", "narr_slice_height",
                    "Height per slice in slice mode.  Default: 1800")
        self._field(f, "Pages per Merge", "narr_merge_count",
                    "Pages joined in one image in merge mode.  Default: 3")
        self._field(f, "Images per Batch", "narr_images_per_batch",
                    "Images sent to Claude per API call.  Default: 3")
        _div(f)
        _sec_header(f, "PDF RENDERING")
        self._field(f, "DPI", "pdf_dpi",
                    "Rasterisation resolution.  Higher = sharper, slower.  Default: 200")
        self._checkbox(f, "Skip Cover + Back Page", "pdf_skip_first_last",
                       "Remove first and last pages (cover, credits, ads)")
        self._field(f, "Raw Slice JPEG Quality", "pdf_jpeg_quality",
                    "Quality before optimizer runs.  Default: 95")
        _div(f)
        self._save_btn(f, ["narr_mode", "narr_slice_height", "narr_merge_count",
                           "narr_images_per_batch", "pdf_dpi",
                           "pdf_skip_first_last", "pdf_jpeg_quality"])

    def _build_optimizer(self, f: tk.Frame):
        _sec_header(f, "IMAGE OPTIMIZER  (Claude upload compression)")
        self._dropdown(f, "Compression Mode", "opt_compression_mode",
                       ["quality", "target_size", "aggressive"],
                       "quality=fixed JPEG quality  target_size=auto-reduce  aggressive=maximum")
        self._field(f, "JPEG Quality", "opt_jpeg_quality",
                    "Fixed quality in 'quality' mode.  85=high  75=balanced  65=lean")
        self._field(f, "Target KB per Image", "opt_target_kb",
                    "Target in 'target_size' mode.  Default: 150 KB")
        self._field(f, "Min Quality Floor", "opt_min_quality",
                    "Never compress below this level.  Default: 25")
        self._field(f, "Max Width (px)", "opt_max_width",
                    "Resize to this width before compression.  800=balanced")
        _div(f)
        _sec_header(f, "IMAGE CLEANUP")
        self._checkbox(f, "Grayscale", "opt_grayscale",
                       "Convert to black & white — saves 30–40% tokens")
        self._checkbox(f, "Autocrop White Borders", "opt_autocrop",
                       "Strip white padding from around each panel")
        self._checkbox(f, "Sharpen After Resize", "opt_sharpen",
                       "UnsharpMask pass to keep text crisp after downscaling")
        _div(f)
        self._save_btn(f, ["opt_compression_mode", "opt_jpeg_quality",
                           "opt_target_kb", "opt_min_quality", "opt_max_width",
                           "opt_grayscale", "opt_autocrop", "opt_sharpen"])

    def _build_detection(self, f: tk.Frame):
        _sec_header(f, "PANEL DETECTION")
        tk.Label(f,
            text=(
                "Detection settings are now configured per-episode\n"
                "inside the DETECT stage of the Pipeline tab.\n\n"
                "This gives every episode its own tuned parameters\n"
                "so you can use the interactive Parameter Tuner,\n"
                "preview results on a short clip, and confirm\n"
                "before running detection on the full video.\n\n"
                "Global fallback defaults are in config.py."
            ),
            font=("Courier New", 9), bg=BG, fg=MUTED,
            justify="left", anchor="w",
        ).pack(anchor="w", padx=16, pady=(8, 4))
        _div(f)
        _sec_header(f, "TRANSCRIPT EXTRACTION  (global default)")
        self._dropdown(f, "Whisper Model", "whisper_model",
                       ["tiny", "base", "small", "medium",
                        "large-v2", "large-v3",
                        "tiny.en", "base.en", "small.en", "medium.en"],
                       "faster-whisper model used for transcript extraction.")
        self._field(f, "Screenshot Offset (sec)", "screenshot_offset",
                    "Seconds into each panel to grab the representative frame.  Default: 0.5")
        _div(f)
        self._save_btn(f, ["whisper_model", "screenshot_offset"])

    def _build_tts(self, f: tk.Frame):
        _sec_header(f, "QWEN3 TTS  (system paths — edit config.py to change)")
        self._info(f, "Conda Python",   config.CONDA_PYTHON)
        self._info(f, "Models folder",  str(config.TTS_MODELS_BASE))
        _div(f)
        _sec_header(f, "DEFAULT MODEL VARIANTS  (per TTS mode)")
        self._dropdown(f, "VoiceDesign default", "tts_recommended_voice_design",
                       list(config.TTS_MODEL_PATHS.keys()),
                       "Model used when creating VoiceDesign profiles")
        self._dropdown(f, "VoiceClone default", "tts_recommended_voice_clone",
                       list(config.TTS_MODEL_PATHS.keys()),
                       "Model used when creating VoiceClone profiles")
        self._dropdown(f, "CustomVoice default", "tts_recommended_custom_voice",
                       list(config.TTS_MODEL_PATHS.keys()),
                       "Model used when creating CustomVoice profiles")
        _div(f)
        _sec_header(f, "AVAILABLE SPEAKERS  (config.py)")
        self._info(f, "Preset speakers", "  ·  ".join(config.TTS_PRESET_SPEAKERS))
        _div(f)
        _sec_header(f, "NOTE")
        tk.Label(f,
            text="Individual voice parameters are configured per voice profile "
                 "— use the DUBBING tab to manage profiles.",
            font=FS, bg=BG, fg=MUTED, justify="left", wraplength=480,
        ).pack(anchor="w")
        _div(f)
        self._save_btn(f, [
            "tts_recommended_voice_design",
            "tts_recommended_voice_clone",
            "tts_recommended_custom_voice",
        ])

    def _build_dubbing(self, f: tk.Frame):
        _sec_header(f, "AUDIO ALIGNMENT  (faster-whisper split)")
        self._dropdown(f, "Whisper Model", "dub_whisper_model",
                       ["tiny", "base", "small", "medium", "large-v2"],
                       "Model used to transcribe continuous TTS audio for splitting.  Default: small")
        self._field(f, "Silence Snap Window (ms)", "dub_snap_window_ms",
                    "Search window for nearest silence at each cut point.  Default: 600 ms")
        _div(f)
        _sec_header(f, "AUDIO PROCESSING")
        self._field(f, "Continuous Timeout (sec)", "dub_continuous_timeout",
                    "Hard timeout for one continuous TTS generation.  Default: 900 (15 min)")
        self._field(f, "Normalise RMS Target", "dub_normalize_rms",
                    "Target RMS for per-panel audio normalisation.  "
                    "Default: 3000  (capped at 3× gain to prevent clipping)")
        _div(f)
        self._save_btn(f, [
            "dub_whisper_model", "dub_snap_window_ms",
            "dub_continuous_timeout", "dub_normalize_rms",
        ])

    # ══════════════════════════════════════════════════════════════════════════
    # FORM FIELD FACTORIES
    # ══════════════════════════════════════════════════════════════════════════

    def _field(self, parent: tk.Frame, label: str, key: str, hint: str = ""):
        saved = self.db.get_setting(key, _DEFAULTS.get(key, ""))
        var   = tk.StringVar(value=str(saved) if saved is not None else "")
        self._vars[key] = var
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, font=FL, bg=BG, fg=TEXT_DIM,
                 width=_LBL_W, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, font=FB, bg=BTN_BG, fg=TEXT,
                 insertbackground=ACCENT, relief="flat", width=16,
                 highlightthickness=1, highlightcolor=ACCENT,
                 highlightbackground=BORDER,
                 ).pack(side="left", padx=8)
        if hint:
            tk.Label(row, text=hint, font=FS, bg=BG, fg=MUTED).pack(side="left")

    def _field_secret(self, parent: tk.Frame, label: str, key: str, hint: str = ""):
        saved = self.db.get_setting(key, _DEFAULTS.get(key, ""))
        var   = tk.StringVar(value=str(saved) if saved is not None else "")
        self._vars[key] = var
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, font=FL, bg=BG, fg=TEXT_DIM,
                 width=_LBL_W, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, show="*", font=FB, bg=BTN_BG, fg=TEXT,
                 insertbackground=ACCENT, relief="flat", width=40,
                 highlightthickness=1, highlightcolor=ACCENT,
                 highlightbackground=BORDER,
                 ).pack(side="left", padx=8)
        if hint:
            tk.Label(row, text=hint, font=FS, bg=BG, fg=MUTED, wraplength=360
                     ).pack(anchor="w", padx=(8 + _LBL_W * 7 + 8, 0))

    def _dropdown(self, parent: tk.Frame, label: str, key: str,
                  options: List[str], hint: str = ""):
        saved = self.db.get_setting(key, _DEFAULTS.get(key, options[0]))
        value = saved if saved in options else options[0]
        var   = tk.StringVar(value=str(value))
        self._vars[key] = var
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, font=FL, bg=BG, fg=TEXT_DIM,
                 width=_LBL_W, anchor="w").pack(side="left")
        om = tk.OptionMenu(row, var, *options)
        om.config(font=FB, bg=BTN_BG, fg=TEXT, activebackground=ACCENT2,
                  relief="flat", highlightthickness=0, bd=0)
        om["menu"].config(bg=BTN_BG, fg=TEXT, activebackground=ACCENT2, font=FB)
        om.pack(side="left", padx=8)
        if hint:
            tk.Label(row, text=hint, font=FS, bg=BG, fg=MUTED).pack(side="left")

    def _checkbox(self, parent: tk.Frame, label: str, key: str, hint: str = ""):
        saved = self.db.get_setting(key, _DEFAULTS.get(key, False))
        if isinstance(saved, str):
            bool_val = saved.lower() in ("true", "1", "yes")
        else:
            bool_val = bool(saved)
        var = tk.BooleanVar(value=bool_val)
        self._vars[key] = var
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Checkbutton(row, text=label, variable=var, font=FB, bg=BG, fg=TEXT,
                       activebackground=BG, activeforeground=ACCENT,
                       selectcolor=BTN_BG, highlightthickness=0,
                       cursor="hand2").pack(side="left")
        if hint:
            tk.Label(row, text=f"  {hint}", font=FS, bg=BG, fg=MUTED).pack(side="left")

    def _info(self, parent: tk.Frame, label: str, value: str):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, font=FL, bg=BG, fg=MUTED,
                 width=_LBL_W, anchor="w").pack(side="left")
        short = value if len(value) <= 72 else "…" + value[-70:]
        tk.Label(row, text=short, font=FS, bg=BG, fg=TEXT_DIM,
                 anchor="w").pack(side="left", padx=8)

    def _save_btn(self, parent: tk.Frame, keys: List[str]):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(16, 4))
        _btn(row, "  SAVE SECTION  ",
             lambda: self._save_keys(keys),
             bg=ACCENT, fg="#000").pack(side="left", padx=(0, 12))
        _btn(row, "RESET TO DEFAULTS",
             lambda: self._reset_keys(keys),
             bg=PANEL2).pack(side="left")

    # ══════════════════════════════════════════════════════════════════════════
    # LOAD / SAVE / RESET
    # ══════════════════════════════════════════════════════════════════════════

    def _load_from_db(self):
        for key, var in self._vars.items():
            default = _DEFAULTS.get(key, "")
            saved   = self.db.get_setting(key, default)
            try:
                if isinstance(var, tk.BooleanVar):
                    if isinstance(saved, str):
                        var.set(saved.lower() in ("true", "1", "yes"))
                    else:
                        var.set(bool(saved))
                else:
                    var.set(str(saved))
            except Exception:
                pass

    def _save_keys(self, keys: List[str]):
        for key in keys:
            if key in self._vars:
                self.db.set_setting(key, self._vars[key].get())
        self._log(f"Saved {len(keys)} setting(s) ✓", "success")

    def _save_all(self):
        for key, var in self._vars.items():
            self.db.set_setting(key, var.get())
        self._log(f"All settings saved ({len(self._vars)} values) ✓", "success")

    def _reset_current_section(self):
        if not self._current_section:
            return
        section_key_map: Dict[str, List[str]] = {
            "api_keys":  ["nvidia_api_key", "nvidia_batch_size", "nvidia_max_concurrent"],
            "lm_studio": ["lm_studio_url", "lm_studio_model",
                          "lm_studio_context_length",
                          "lm_studio_batch_size", "lm_studio_max_concurrent"],
            "slicer":    [k for k in self._vars if k.startswith(("narr_", "pdf_"))],
            "optimizer": [k for k in self._vars if k.startswith("opt_")],
            "detection": [k for k in self._vars if k.startswith(("detect_",
                          "whisper_", "screenshot_"))],
            "tts":       [k for k in self._vars if k.startswith("tts_")],
            "dubbing":   [k for k in self._vars if k.startswith("dub_")],
        }
        keys = section_key_map.get(self._current_section, [])
        self._reset_keys(keys)

    def _reset_keys(self, keys: List[str]):
        for key in keys:
            default = _DEFAULTS.get(key, "")
            if key in self._vars:
                var = self._vars[key]
                try:
                    if isinstance(var, tk.BooleanVar):
                        var.set(bool(default))
                    else:
                        var.set(str(default))
                except Exception:
                    pass
        self._log(f"Reset {len(keys)} setting(s) to defaults", "info")

    # ══════════════════════════════════════════════════════════════════════════
    # LOGGING
    # ══════════════════════════════════════════════════════════════════════════

    def _log(self, msg: str, level: str = "info"):
        self._on_log(msg, level)
