#!/usr/bin/env bash
#
# setup_dots_tts.sh — one-time setup for the dots.tts (MLX) dub backend.
#
# Creates a dedicated conda env, installs the pure-MLX Apple-Silicon port of
# dots.tts, and downloads the fast MeanFlow int4 weights. After this completes,
# flip TTS_BACKEND = "dots" in scripts/config.py.
#
# Requirements: Apple Silicon Mac, conda (miniconda/anaconda) on PATH.
#
set -euo pipefail

ENV_NAME="dots_tts"
WEIGHTS_DIR="${HOME}/dots-tts-mlx-weights"   # must match config.DOTS_WEIGHTS_DIR's parent
# We fetch BOTH checkpoints:
#   int4    — soar decoder, RECOMMENDED for quality (run at num_steps=10)
#   mf-int4 — MeanFlow, ~2× faster but lower quality (run at num_steps=4)
# Point DOTS_WEIGHTS_DIR at "int4" first; switch to "mf-int4" only once quality
# is confirmed and you want speed. num_steps auto-matches the folder name.
VARIANTS=("int4" "mf-int4")

echo "▶  dots.tts setup — env '${ENV_NAME}', weights → ${WEIGHTS_DIR}"

if ! command -v conda >/dev/null 2>&1; then
  echo "✗  conda not found on PATH. Install miniconda first." >&2
  exit 1
fi

# 1. Create the env (Python 3.10) if it doesn't exist.
if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "✓  conda env '${ENV_NAME}' already exists"
else
  echo "▶  creating conda env '${ENV_NAME}' (python=3.10) …"
  conda create -n "${ENV_NAME}" python=3.10 -y
fi

# 2. Install the MLX port + runtime deps into that env.
echo "▶  installing dots-tts-mlx + deps …"
conda run -n "${ENV_NAME}" python -m pip install --upgrade pip
conda run -n "${ENV_NAME}" python -m pip install \
  "git+https://github.com/sb1992/dots-tts-mlx.git@v0.3.1" \
  "huggingface_hub[cli]" soundfile numpy

# 3. Download the quantized MLX weights (both variants).
mkdir -p "${WEIGHTS_DIR}"
for V in "${VARIANTS[@]}"; do
  echo "▶  downloading '${V}' weights (a few GB) …"
  conda run -n "${ENV_NAME}" hf download shraey/dots-tts-mlx \
    --include "${V}/*" --local-dir "${WEIGHTS_DIR}"
done

echo ""
echo "✓  Done."
echo "   Weights:   ${WEIGHTS_DIR}/int4 (quality) and ${WEIGHTS_DIR}/mf-int4 (speed)"
echo "   Env python: $(conda run -n ${ENV_NAME} which python)"
echo ""
echo "Next steps (all in the app — no code edits needed):"
echo "  • Settings → Advanced:    dots.tts Python Path = the env python above"
echo "  • Settings → Voices & TTS: dots.tts Weights Dir = ${WEIGHTS_DIR}/int4   (soar = best quality)"
echo "                             TTS Engine = dots.tts ;  Steps = blank (auto)"
echo "  • Create a voice in Dubbing → Voices with a clean ~10s WAV reference"
echo "    (the transcript auto-fills). dots clones from that — works for all 24 languages."
echo "  • Want speed later? Point Weights Dir at ${WEIGHTS_DIR}/mf-int4 (auto-uses 4 steps)."
