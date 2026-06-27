"""
ui/stages/__init__.py
Registers all available pipeline stages dynamically.
"""
import importlib
import traceback

STAGE_MODULES = {}

_stages = [
    "detect_stage",
    "video_refine_stage",
    "video_screenshot_stage",
    "pdf_slice_stage",
    "pdf_narrate_stage",
    "translate_stage",
    "dub_stage",
    "sync_stage",
    "assemble_stage",
    "upscale_stage",
]

for _module_name in _stages:
    try:
        _mod = importlib.import_module(f"ui.stages.{_module_name}")
        _key = _module_name.replace("_stage", "")
        STAGE_MODULES[_key] = _mod
    except ImportError:
        # Module not installed yet — expected, silently skip.
        pass
    except Exception as _exc:
        # Syntax errors, NameErrors, etc. in stage files must not crash
        # the whole application — log and continue so other stages still load.
        print(f"[stages] Warning: failed to load '{_module_name}': {_exc}")
        traceback.print_exc()