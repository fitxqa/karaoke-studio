"""Match lyric words to the singer's voice (forced alignment).

Uses Meta's MMS forced-alignment model that ships with torchaudio (multilingual, works on
romanized text). Tamil lyrics are turned into Latin letters first (see tamil.py), so the model
can follow them. The first run downloads the model (~1.2 GB) into the normal torch cache.
"""
from __future__ import annotations

import re
import unicodedata

from . import lyrics as L

SR = 16000
CHUNK_SECONDS = 30  # the model's attention grows with length; keep memory small on normal PCs

_model = None


def _load():
    global _model
    if _model is None:
        import torch  # noqa: F401
        import torchaudio

        bundle = torchaudio.pipelines.MMS_FA
        model = bundle.get_model(with_star=False)
        model.eval()
        _model = (bundle, model, bundle.get_tokenizer(), bundle.get_aligner(), set(bundle.get_dict()))
    return _model


def _latin(word: str, vocab: set[str]) -> str:
    """Any script -> the letters the model knows (a-z and ')."""
    script = L.detect_script(word)
    w = (L.romanize(word, script) or word) if script else word
    w = unicodedata.normalize("NFKD", w)
    w = "".join(c for c in w if not unicodedata.combining(c)).lower()
    w = re.sub(r"[^a-z']", "", w)
    return "".join(c for c in w if c in vocab)


def align_to_voice(vocals_path: str, text: str, duration: float | None = None,
                   progress=lambda msg: None) -> tuple[list[dict], float]:
    """Pasted lyrics + vocals.wav -> timed lines. Returns (lines, fraction of words aligned)."""
    import librosa
    import torch

    lines = L.clean_pasted_lyrics(text)
    if not lines:
        raise ValueError("No lyrics found in the pasted text.")
    progress("Loading the voice-matching model (the first time downloads about 1.2 GB)")
    bundle, model, tokenizer, aligner, vocab = _load()

    words = [(li, w) for li, line in enumerate(lines) for w in line.split()]
    latin = [_latin(w, vocab) for _, w in words]
    keep = [i for i, t in enumerate(latin) if t]
    if not keep:
        raise ValueError("Could not read these lyrics for matching.")

    progress("Listening to the singer's voice")
    y, _ = librosa.load(vocals_path, sr=SR, mono=True)
    wav = torch.from_numpy(y).unsqueeze(0)
    chunk = CHUNK_SECONDS * SR
    emissions = []
    with torch.inference_mode():
        for s in range(0, wav.size(1), chunk):
            part = wav[:, s:s + chunk]
            if part.size(1) < 400:
                continue
            em, _ = model(part)
            emissions.append(em[0])
    emission = torch.cat(emissions, dim=0)
    ratio = wav.size(1) / emission.size(0) / SR  # seconds per frame

    progress("Matching each word to the singing")
    spans = aligner(emission, tokenizer([latin[i] for i in keep]))
    times: list[tuple[float, float] | None] = [None] * len(words)
    for i, sp in zip(keep, spans):
        if sp:
            times[i] = (sp[0].start * ratio, sp[-1].end * ratio)

    # words the model couldn't read (numbers, symbols): spread between neighbours
    n = len(words)
    k = 0
    while k < n:
        if times[k] is not None:
            k += 1
            continue
        e = k
        while e < n and times[e] is None:
            e += 1
        left = times[k - 1][1] if k > 0 else (times[e][0] - 0.3 * (e - k) if e < n else 0.0)
        right = times[e][0] if e < n else left + 0.3 * (e - k)
        step = max(0.05, (right - left) / (e - k))
        for q in range(k, e):
            times[q] = (max(0.0, left + step * (q - k)), max(0.0, left + step * (q - k + 1)))
        k = e

    out = []
    for li, line in enumerate(lines):
        idx = [q for q, (wl, _) in enumerate(words) if wl == li]
        ws = [{"start": round(times[q][0], 3), "end": round(max(times[q][1], times[q][0] + 0.05), 3),
               "text": words[q][1]} for q in idx]
        out.append({"start": ws[0]["start"], "text": line, "words": ws})
    return L.build_lines(out, duration), len(keep) / n
