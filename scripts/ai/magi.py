"""Local Magi v3 visual evidence provider for Recap.

Magi v3 is deliberately used as a *grounding* model, not as the source of
long-term character identity.  It can reliably locate comic characters, text,
speech-bubble tails and their within-panel associations; Story Memory remains
the human-reviewable source of truth across chapters.

The model is optional because its official checkpoint is large.  Nothing is
downloaded until the user explicitly installs it from Recap.  It is loaded once
per API process and runs on Apple Silicon MPS when available, otherwise CPU.
"""

from __future__ import annotations

import threading
import inspect
from pathlib import Path
from typing import Iterable

import config

MODEL_ID = "ragavsachdeva/magiv3"
MODEL_DIR = config.BASE_DIR / "models" / "magiv3"

_model = None
_processor = None
_device = ""
_load_lock = threading.Lock()


def is_installed() -> bool:
    """Whether a complete local checkpoint is ready without network access."""
    return (MODEL_DIR / "config.json").is_file() and (MODEL_DIR / "model.safetensors").is_file()


def status() -> dict:
    return {
        "model_id": MODEL_ID,
        "installed": is_installed(),
        "loaded": _model is not None,
        "device": _device or None,
        "license_note": "Magi v3 is licensed for personal, research, non-commercial and not-for-profit use. Contact its author for commercial use.",
    }


def install(log=None) -> dict:
    """Download the official checkpoint to the project-local models folder."""
    if is_installed():
        return status()
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise RuntimeError("huggingface_hub is required to install Magi v3.") from exc
    if log:
        log("Magi v3: downloading the official local checkpoint. This is a one-time download…", "muted")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=MODEL_ID, local_dir=str(MODEL_DIR))
    if not is_installed():
        raise RuntimeError("Magi v3 download finished but the checkpoint files are incomplete.")
    return status()


def _prepare_transformers_compat() -> None:
    """Bridge Magi's 4.45-era Florence code to newer Transformers releases.

    Transformers 4.50 stopped giving every ``PreTrainedModel`` a ``generate``
    method. Magi's nested Florence language model expects that old behaviour.
    Applying the standard mixin to that one dynamic class preserves Magi without
    downgrading the app-wide Transformers version used by WhisperX.
    """
    from transformers.dynamic_module_utils import get_class_from_dynamic_module
    from transformers.generation import GenerationMixin

    root = get_class_from_dynamic_module(
        "modeling_florence2.Florence2ForConditionalGeneration", str(MODEL_DIR),
        local_files_only=True,
    )
    module = inspect.getmodule(root)
    language = getattr(module, "Florence2LanguageForConditionalGeneration", None)
    if language is not None and not hasattr(language, "generate"):
        language.generate = GenerationMixin.generate


def _load():
    global _model, _processor, _device
    if _model is not None:
        return _model, _processor
    if not is_installed():
        raise RuntimeError("Magi v3 is not installed. Install it from Recap before enabling visual grounding.")
    with _load_lock:
        if _model is not None:
            return _model, _processor
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoProcessor
        except ImportError as exc:  # pragma: no cover - environment diagnostic
            raise RuntimeError("Magi v3 needs torch and transformers in the ManhwaStudio Python environment.") from exc
        # The official card demonstrates CUDA.  MPS is the equivalent local
        # acceleration path on this Mac; float32 CPU is a safe fallback.
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.float16 if device == "mps" else torch.float32
        _prepare_transformers_compat()
        try:
            model = AutoModelForCausalLM.from_pretrained(
                str(MODEL_DIR), torch_dtype=dtype, trust_remote_code=True,
                local_files_only=True, attn_implementation="eager",
            ).to(device).eval()
        except Exception:
            # Some PyTorch/MPS combinations cannot execute one custom Florence
            # operation in fp16.  CPU is slower but preserves a working path.
            if device != "mps":
                raise
            device, dtype = "cpu", torch.float32
            model = AutoModelForCausalLM.from_pretrained(
                str(MODEL_DIR), torch_dtype=dtype, trust_remote_code=True,
                local_files_only=True, attn_implementation="eager",
            ).to(device).eval()
        _processor = AutoProcessor.from_pretrained(str(MODEL_DIR), trust_remote_code=True, local_files_only=True)
        _model, _device = model, device
    return _model, _processor


def _box(box) -> list[float]:
    """Return a JSON-safe bounding box, tolerating model/library variations."""
    try:
        return [round(float(value), 1) for value in box[:4]]
    except (TypeError, ValueError):
        return []


def normalise_detection(raw: dict) -> dict:
    """Condense Magi's raw prediction into stable, prompt-safe visual evidence."""
    chars = [_box(box) for box in (raw.get("characters") or [])]
    texts = [_box(box) for box in (raw.get("texts") or [])]
    tails = [_box(box) for box in (raw.get("tails") or [])]
    clusters = list(raw.get("character_cluster_labels") or [])
    associations = []
    for pair in (raw.get("text_character_associations") or []):
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            associations.append({"text": int(pair[0]), "character": int(pair[1])})
    return {
        "provider": "magiv3",
        "characters": chars,
        "texts": texts,
        "tails": tails,
        "character_clusters": [int(label) for label in clusters],
        "dialogue_character_links": associations,
        "summary": (
            f"Magi v3 visual grounding: {len(chars)} character region(s), "
            f"{len(texts)} text region(s), {len(tails)} speech-tail region(s), "
            f"{len(associations)} dialogue-to-character link(s)."
        ),
    }


def analyse(images: Iterable) -> list[dict]:
    """Run official Magi detection + associations for a sequence of PIL images."""
    image_list = list(images)
    if not image_list:
        return []
    model, processor = _load()
    raw = model.predict_detections_and_associations(image_list, processor)
    return [normalise_detection(item if isinstance(item, dict) else {}) for item in raw]
