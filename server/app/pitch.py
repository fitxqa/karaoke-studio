"""Reference melody (pitch contour) of the original singer, used for scoring."""
from __future__ import annotations

import numpy as np

SR = 16000
HOP = 320  # 20 ms per frame


def vocal_pitch(vocals_path: str) -> dict:
    """Return {"hop": 0.02, "midi": [float|None, ...]} — one value per 20 ms frame."""
    import librosa

    y, _ = librosa.load(vocals_path, sr=SR, mono=True)
    f0, voiced, prob = librosa.pyin(
        y, fmin=65.0, fmax=1050.0, sr=SR, frame_length=1280, hop_length=HOP,
        resolution=0.25, n_thresholds=50,  # ~5x faster; scoring uses whole semitones anyway
    )
    # Quiet frames are bleed from the instruments, not singing.
    rms = librosa.feature.rms(y=y, frame_length=1280, hop_length=HOP)[0]
    rms = rms[: len(f0)]
    gate = max(1e-4, float(np.percentile(rms, 60)) * 0.25)

    midi: list[float | None] = []
    for hz, v, p, r in zip(f0, voiced, prob, rms):
        if v and p > 0.3 and r > gate and np.isfinite(hz):
            midi.append(round(float(69 + 12 * np.log2(hz / 440.0)), 2))
        else:
            midi.append(None)
    return {"hop": HOP / SR, "midi": _smooth(midi)}


def _smooth(midi: list[float | None]) -> list[float | None]:
    """Drop 1-2 frame blips so scoring isn't thrown off by noise."""
    out = list(midi)
    n = len(out)
    for i in range(n):
        if out[i] is None:
            continue
        left = i > 0 and midi[i - 1] is not None
        right = i + 1 < n and midi[i + 1] is not None
        if not left and not right:
            out[i] = None
    return out
