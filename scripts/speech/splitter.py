"""
speech/splitter.py — cut a batched TTS read back into per-cue pieces.

We dub several cues as ONE Qwen3 read so the voice flows naturally, but then we
must place each cue at its OWN source time to stay in sync with the video. This
splits the batch audio into N pieces:

  1. by silence — Qwen pauses between the joined sentences, so we cut at the
     N-1 clearest pauses (clean, no mid-word cuts), else
  2. proportional — fall back to slicing by each cue's text length so we always
     get exactly N pieces (a soft fade later hides any boundary).

    split_batch(samples, sr, n, weights) -> [np.ndarray, ...]  (length n)
"""

from __future__ import annotations

from typing import List, Optional


def _split_silence(y, sr: int, n: int, min_sil_ms: float, thresh_ratio: float):
    import numpy as np
    fl = max(1, int(sr * 0.02))          # 20 ms frames
    nf = len(y) // fl
    if nf < n:
        return None
    e = np.sqrt(np.array([np.mean(np.square(y[i * fl:(i + 1) * fl])) for i in range(nf)]) + 1e-9)
    peak = float(e.max()) or 1.0
    silent = e < peak * thresh_ratio

    runs = []
    i = 0
    while i < nf:
        if silent[i]:
            j = i
            while j < nf and silent[j]:
                j += 1
            if (j - i) * 20 >= min_sil_ms and i > 0 and j < nf:   # interior pause only
                runs.append((i, j, j - i))
            i = j
        else:
            i += 1
    if len(runs) < n - 1:
        return None

    chosen = sorted(sorted(runs, key=lambda r: -r[2])[: n - 1])    # longest n-1 pauses, in order
    cuts   = [0] + [int((r[0] + r[1]) / 2) * fl for r in chosen] + [len(y)]
    return [y[cuts[k]:cuts[k + 1]] for k in range(len(cuts) - 1)]


def _split_proportional(y, n: int, weights: Optional[List[float]]):
    import numpy as np  # noqa: F401
    if not weights or len(weights) != n or sum(weights) <= 0:
        weights = [1.0] * n
    total = float(sum(weights))
    L = len(y)
    pieces, pos, acc = [], 0, 0.0
    for k in range(n):
        acc += weights[k]
        end = L if k == n - 1 else int(L * acc / total)
        pieces.append(y[pos:end])
        pos = end
    return pieces


def split_batch(y, sr: int, n: int, weights: Optional[List[float]] = None):
    """Split batch audio into exactly n pieces (silence-based, proportional fallback)."""
    if n <= 1:
        return [y]
    pieces = _split_silence(y, sr, n, min_sil_ms=90, thresh_ratio=0.12)
    if pieces is not None and len(pieces) == n:
        return pieces
    return _split_proportional(y, n, weights)
