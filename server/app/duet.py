"""Duet mode: who sings when (male / female / both) and per-singer vocal tracks.

The singer's pitch (already analysed for scoring, pitch.json) tells the voices apart: men
usually sing roughly an octave below women. Each lyric line gets M, F or B (both); lines can be
corrected by hand ("singer" on the line wins). Partner tracks are the vocal stem with only the
other singer's parts left in.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

HOP = 0.02  # pitch.json frame length (s)


def _kmeans2(x: np.ndarray, iters: int = 30) -> tuple[float, float, np.ndarray]:
    lo, hi = np.percentile(x, 20), np.percentile(x, 80)
    for _ in range(iters):
        lab = np.abs(x - hi) < np.abs(x - lo)
        nlo, nhi = (x[~lab].mean() if (~lab).any() else lo), (x[lab].mean() if lab.any() else hi)
        if abs(nlo - lo) < 1e-3 and abs(nhi - hi) < 1e-3:
            break
        lo, hi = nlo, nhi
    return float(lo), float(hi), lab


def _smooth_labels(lab: list[str | None], win: int = 25) -> list[str | None]:
    """Majority vote over ~0.5 s so single wobbly notes don't flip the singer."""
    out = list(lab)
    n = len(lab)
    for i in range(n):
        if lab[i] is None:
            continue
        a, b = max(0, i - win), min(n, i + win + 1)
        m = sum(1 for v in lab[a:b] if v == "M")
        f = sum(1 for v in lab[a:b] if v == "F")
        out[i] = "M" if m >= f else "F"
    return out


def analyse(pitch: dict) -> dict:
    """pitch.json -> {"duet": bool, "threshold": midi, "solo": "M"|"F", "segments": [...]}."""
    midi = pitch.get("midi") or []
    hop = float(pitch.get("hop", HOP))
    voiced = np.array([m for m in midi if m is not None], dtype=float)
    if len(voiced) < 50:
        return {"duet": False, "solo": "M", "threshold": 55.0, "segments": []}
    lo, hi, lab = _kmeans2(voiced)
    share_hi = lab.mean()
    duet = (hi - lo) >= 5.0 and 0.12 <= share_hi <= 0.88
    threshold = (lo + hi) / 2 if duet else 55.0  # ~196 Hz: typical male/female boundary
    solo = "M" if np.median(voiced) < 55.0 else "F"

    labels: list[str | None] = [None if m is None else ("F" if m >= threshold else "M") for m in midi]
    if not duet:
        labels = [None if v is None else solo for v in labels]
    labels = _smooth_labels(labels)

    # frames -> segments (fill short silences inside a phrase)
    segments: list[dict] = []
    cur, start, last_voiced = None, 0, -1
    for i, v in enumerate(labels + [None] * 40):
        if v is not None:
            if cur is None:
                cur, start = v, i
            elif v != cur:
                segments.append({"start": round(start * hop, 2), "end": round((last_voiced + 1) * hop, 2), "singer": cur})
                cur, start = v, i
            last_voiced = i
        elif cur is not None and i - last_voiced > 35:  # >0.7 s of silence ends the phrase
            segments.append({"start": round(start * hop, 2), "end": round((last_voiced + 1) * hop, 2), "singer": cur})
            cur = None
    segments = [s for s in segments if s["end"] - s["start"] >= 0.25]
    return {"duet": bool(duet), "solo": solo, "threshold": round(float(threshold), 2),
            "low": round(lo, 2), "high": round(hi, 2), "segments": segments}


def line_singer(line: dict, segments: list[dict], solo: str = "M") -> str:
    """M / F / B for one lyric line: the manual tag if set, else what the voice analysis heard."""
    if line.get("singer") in ("M", "F", "B"):
        return line["singer"]
    a, b = line["start"], line["end"]
    m = f = 0.0
    for s in segments:
        ov = min(b, s["end"]) - max(a, s["start"])
        if ov > 0:
            if s["singer"] == "M":
                m += ov
            else:
                f += ov
    if m + f == 0:
        return solo
    if m >= 0.75 * (m + f):
        return "M"
    if f >= 0.75 * (m + f):
        return "F"
    return "B"


def effective_segments(duet: dict, lyr: dict) -> list[dict]:
    """Time ranges per singer: lyric lines (with corrections), plus voice-only parts outside lines."""
    segs = duet.get("segments", [])
    solo = duet.get("solo", "M")
    lines = lyr.get("lines", []) if lyr else []
    out = []
    for l in lines:
        out.append({"start": l["start"], "end": max(l["end"], l["words"][-1]["end"] if l.get("words") else l["end"]),
                    "singer": line_singer(l, segs, solo)})
    covered = [(o["start"], o["end"]) for o in out]
    for s in segs:  # humming / ad-libs between lyric lines
        if not any(min(s["end"], b) - max(s["start"], a) > 0 for a, b in covered):
            out.append(dict(s))
    out.sort(key=lambda s: s["start"])
    return out


def segments_hash(segs: list[dict]) -> str:
    return hashlib.md5(json.dumps(segs, sort_keys=True).encode()).hexdigest()[:10]


def render_part(vocals: Path, segs: list[dict], keep: str, out: Path) -> Path:
    """Vocal stem with only the `keep` singer's parts left in (M or F), 40 ms fades at the edges."""
    import soundfile as sf

    if out.exists():
        return out
    y, sr = sf.read(str(vocals), always_2d=True)
    gain = np.zeros(len(y), dtype=np.float32)
    fade = int(0.04 * sr)
    for s in segs:
        if s["singer"] != keep:
            continue
        a, b = max(0, int((s["start"] - 0.15) * sr)), min(len(y), int((s["end"] + 0.25) * sr))
        if b <= a:
            continue
        gain[a:b] = 1.0
        f = min(fade, (b - a) // 2)
        if f > 0:
            gain[a:a + f] = np.minimum(gain[a:a + f], np.linspace(0, 1, f))
            gain[b - f:b] = np.minimum(gain[b - f:b], np.linspace(1, 0, f))
    tmp = out.with_suffix(".tmp.wav")
    sf.write(str(tmp), (y * gain[:, None]).astype(np.float32), sr)
    tmp.replace(out)
    return out
