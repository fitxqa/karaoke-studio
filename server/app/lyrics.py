"""Lyrics: fetch synced lyrics from LRCLIB, or generate them with Whisper.

Output format (lyrics.json), used by the desktop app:
{
  "source": "lrclib" | "whisper" | "manual",
  "language": "ta" | "en" | ...,
  "lines": [
    {"start": 12.3, "end": 15.8, "text": "...", "roman": "...",
     "words": [{"start": 12.3, "end": 12.9, "text": "..."}, ...]}
  ]
}
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

LRCLIB = "https://lrclib.net/api"
USER_AGENT = "KaraokeStudio/1.0 (personal karaoke app)"

# ---------------------------------------------------------------- scripts / romanization

_SCRIPTS = {
    # name: (first codepoint, last codepoint, language code, sanscript scheme)
    "tamil": (0x0B80, 0x0BFF, "ta", "tamil"),
    "malayalam": (0x0D00, 0x0D7F, "ml", "malayalam"),
    "devanagari": (0x0900, 0x097F, "hi", "devanagari"),
    "telugu": (0x0C00, 0x0C7F, "te", "telugu"),
    "kannada": (0x0C80, 0x0CFF, "kn", "kannada"),
}


def detect_script(text: str) -> str | None:
    counts = {name: 0 for name in _SCRIPTS}
    for ch in text:
        cp = ord(ch)
        for name, (lo, hi, _, _) in _SCRIPTS.items():
            if lo <= cp <= hi:
                counts[name] += 1
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else None


def romanize(text: str, script: str | None = None) -> str | None:
    """Tamil/Malayalam/Hindi/... text -> Latin letters (e.g. 'வணக்கம்' -> 'vaṇakkam')."""
    script = script or detect_script(text)
    if not script:
        return None
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate
    except ImportError:
        return None
    if script == "tamil":
        from .tamil import romanize as tamil_romanize

        return tamil_romanize(text)
    scheme = _SCRIPTS[script][3]
    try:
        out = transliterate(text, scheme, sanscript.ISO)
    except Exception:
        return None
    return out


def language_of(lines: list[dict]) -> str:
    sample = " ".join(l["text"] for l in lines[:30])
    script = detect_script(sample)
    return _SCRIPTS[script][2] if script else "en"


# ---------------------------------------------------------------- LRC parsing

_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_WORD_TIME = re.compile(r"<(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)>")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\]", re.I)


def _secs(m: str, s: str) -> float:
    return int(m) * 60 + float(s.replace(":", "."))


def parse_lrc(lrc: str, duration: float | None = None) -> list[dict]:
    offset = 0.0
    if (m := _OFFSET.search(lrc)) is not None:
        offset = int(m.group(1)) / 1000.0  # positive offset = lyrics earlier

    entries: list[tuple[float, str]] = []
    for raw in lrc.splitlines():
        stamps = list(_TIME.finditer(raw))
        if not stamps:
            continue
        text = _TIME.sub("", raw).strip()
        for st in stamps:
            entries.append((max(0.0, _secs(st.group(1), st.group(2)) - offset), text))
    entries.sort(key=lambda e: e[0])

    lines: list[dict] = []
    for i, (start, text) in enumerate(entries):
        if not _WORD_TIME.sub("", text).strip():
            continue  # blank line = instrumental gap marker
        nxt = entries[i + 1][0] if i + 1 < len(entries) else (duration or start + 6.0)
        end = min(nxt, start + 12.0)
        words = _parse_enhanced_words(text, start, end)
        clean = _WORD_TIME.sub("", text).strip()
        clean = re.sub(r"\s+", " ", clean)
        lines.append({"start": round(start, 3), "end": round(end, 3), "text": clean,
                      "words": words or spread_words(clean, start, end)})
    return lines


def _parse_enhanced_words(text: str, start: float, end: float) -> list[dict]:
    """Enhanced LRC: <00:12.10>word <00:12.60>word ..."""
    parts = list(_WORD_TIME.finditer(text))
    if not parts:
        return []
    words = []
    for i, p in enumerate(parts):
        seg_end = parts[i + 1].start() if i + 1 < len(parts) else len(text)
        w = text[p.end():seg_end].strip()
        if not w:
            continue
        ws = _secs(p.group(1), p.group(2))
        words.append({"start": ws, "end": None, "text": w})
    for i, w in enumerate(words):
        w["end"] = words[i + 1]["start"] if i + 1 < len(words) else end
    return words


def spread_words(text: str, start: float, end: float) -> list[dict]:
    """No word timings available: share the line's time between words by their length."""
    words = text.split()
    if not words:
        return []
    # Most sung lines don't fill the whole gap to the next line; cap the sung span.
    span = min(end - start, max(1.5, 0.12 * len(text) + 0.8))
    weights = [len(w) + 1 for w in words]
    total = sum(weights)
    out, t = [], start
    for w, wt in zip(words, weights):
        d = span * wt / total
        out.append({"start": round(t, 3), "end": round(t + d, 3), "text": w})
        t += d
    return out


def build_lines(raw: list[dict], duration: float | None = None) -> list[dict]:
    """Lines edited in the player -> clean lines. Keeps word timings the app sends
    (shifted lines), and spreads words again for lines whose text was changed."""
    items = sorted(
        ({"start": max(0.0, float(r["start"])), "text": re.sub(r"\s+", " ", str(r.get("text", ""))).strip(),
          "words": r.get("words")} for r in raw),
        key=lambda r: r["start"],
    )
    items = [r for r in items if r["text"]]
    out = []
    for i, r in enumerate(items):
        start = r["start"]
        nxt = items[i + 1]["start"] if i + 1 < len(items) else (duration or start + 6.0)
        end = max(start + 0.3, min(nxt, start + 12.0))
        words = []
        ws = r["words"] or []
        if ws and " ".join(str(w.get("text", "")) for w in ws).split() == r["text"].split():
            for w in ws:
                a = min(max(float(w["start"]), start), end)
                b = min(max(float(w["end"]), a), end)
                words.append({"start": round(a, 3), "end": round(b, 3), "text": str(w["text"])})
        if words:  # don't keep a line on screen long after its last word
            end = max(start + 0.3, min(end, words[-1]["end"] + 1.5))
        out.append({"start": round(start, 3), "end": round(end, 3), "text": r["text"],
                    "words": words or spread_words(r["text"], start, end)})
    return out


def finalize(lines: list[dict], source: str, language: str | None = None) -> dict[str, Any]:
    lang = language or language_of(lines)
    script = detect_script(" ".join(l["text"] for l in lines[:30]))
    for l in lines:
        l["roman"] = romanize(l["text"], script) if script else None
    return {"source": source, "language": lang, "lines": lines}


def to_lrc(lyrics: dict) -> str:
    out = []
    for l in lyrics.get("lines", []):
        m, s = divmod(l["start"], 60)
        out.append(f"[{int(m):02d}:{s:05.2f}]{l['text']}")
    return "\n".join(out)


# ---------------------------------------------------------------- LRCLIB

_JUNK = re.compile(
    r"\b(official|video|lyrics?|lyrical|full\s*song|audio|song|4k|hd|hq|mv|"
    r"music\s*video|remastered|visualizer|feat\.?|ft\.?)\b",
    re.I,
)


def title_candidates(title: str) -> list[str]:
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", title)
    parts = [p.strip() for p in re.split(r"\s[|\-–—:]\s|\||｜", t) if p.strip()]
    cleaned = [re.sub(r"\s+", " ", _JUNK.sub(" ", p)).strip(" -_.,") for p in parts]
    cleaned = [c for c in cleaned if len(c) > 1]
    cands = []
    if cleaned:
        cands.append(cleaned[0])
        if len(cleaned) > 1:
            cands.append(f"{cleaned[0]} {cleaned[1]}")
    whole = re.sub(r"\s+", " ", _JUNK.sub(" ", t)).strip()
    if whole and whole not in cands:
        cands.append(whole)
    return cands


def _get(url: str, timeout: float = 12.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def search_lrclib(title: str, artist: str | None, track: str | None,
                  duration: float | None) -> str | None:
    """Return synced LRC text for the best matching song, or None."""
    results: list[dict] = []
    queries: list[dict] = []
    if track:
        q = {"track_name": track}
        if artist:
            q["artist_name"] = artist
        queries.append(q)
    for c in title_candidates(title):
        queries.append({"q": c})
    for q in queries:
        try:
            found = _get(f"{LRCLIB}/search?{urllib.parse.urlencode(q)}")
        except Exception:
            continue
        results.extend(r for r in found if r.get("syncedLyrics"))
        if results and duration is None:
            break
    if not results:
        return None
    if duration:
        results = [r for r in results if r.get("duration") and abs(r["duration"] - duration) <= 10] or []
        results.sort(key=lambda r: abs(r["duration"] - duration))
    return results[0]["syncedLyrics"] if results else None


# ---------------------------------------------------------------- Whisper

_whisper_model = None


def _model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        from . import config

        device, compute = config.WHISPER_DEVICE, config.WHISPER_COMPUTE
        if compute == "auto":
            try:
                import ctranslate2

                gpu = device == "cuda" or (device == "auto" and ctranslate2.get_cuda_device_count() > 0)
            except Exception:
                gpu = False
            compute = "float16" if gpu else "int8"
        _whisper_model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)
    return _whisper_model


def _transcriber():
    """Batched pipeline (several times faster) when this faster-whisper version has it."""
    try:
        from faster_whisper import BatchedInferencePipeline

        return BatchedInferencePipeline(model=_model()), True
    except Exception:
        return _model(), False


def transcribe(vocals_path: str, language: str | None = None) -> dict:
    """Speech-to-text on the isolated vocal track, with word timings."""
    model, batched = _transcriber()
    opts = dict(language=None if language in (None, "", "auto") else language,
                word_timestamps=True, vad_filter=True, beam_size=5, condition_on_previous_text=False)
    if batched:
        opts["batch_size"] = 8
    try:
        segments, info = model.transcribe(vocals_path, **opts)
        segments = list(segments)
    except TypeError:  # older API without some options
        opts.pop("batch_size", None)
        segments, info = _model().transcribe(vocals_path, **opts)
        segments = list(segments)
    lines: list[dict] = []
    for seg in segments:
        words = [{"start": round(w.start, 3), "end": round(w.end, 3), "text": w.word.strip()}
                 for w in (seg.words or []) if w.word.strip()]
        if not words:
            continue
        # break long segments into karaoke-sized lines (max ~8 words)
        for i in range(0, len(words), 8):
            chunk = words[i:i + 8]
            lines.append({"start": chunk[0]["start"], "end": chunk[-1]["end"],
                          "text": " ".join(w["text"] for w in chunk), "words": chunk})
    return finalize(lines, "whisper", info.language)


# ---------------------------------------------------------------- fix lyrics with correct text from the web

_LABEL = re.compile(r"^\s*(male|female|chorus|both|all|group|m|f|ஆண்|பெண்|குழு|இருவர்)\s*[:\-–]\s*", re.I)
_HEADER = re.compile(r"^\s*(movie|film|album|singers?|music|composer|lyrics?|lyricist|year|song|starring|cast|"
                     r"director|label|படம்|பாடல்|இசை|பாடியவர்|பாடலாசிரியர்)\b[^:]{0,20}:", re.I)
_NOISE = re.compile(r"\(\s*(x\s*\d+|\d+\s*x|repeat|bgm|music|chorus|humming)\s*\)|\bx\s*\d+\s*$", re.I)


def clean_pasted_lyrics(text: str) -> list[str]:
    """Plain lyrics copied from a website -> clean lines (labels, headers, timestamps removed)."""
    out = []
    for raw in text.splitlines():
        line = _TIME.sub("", raw)
        line = _WORD_TIME.sub("", line)
        if _HEADER.match(line):
            continue
        line = _LABEL.sub("", line)
        line = _NOISE.sub("", line)
        line = re.sub(r"[\"“”]+", "", line)
        line = re.sub(r"\s+", " ", line).strip(" .,-–—…")
        if line and re.search(r"\w", line):
            out.append(line)
    return out


def _sound(word: str) -> str:
    """Rough sound of a word in Latin letters, so Tamil script, Tanglish and AI spellings compare."""
    script = detect_script(word)
    w = romanize(word, script) or word if script else word
    w = w.lower()
    w = re.sub(r"[^a-z]", "", w.replace("ā", "a").replace("ī", "i").replace("ū", "u")
               .replace("ē", "e").replace("ō", "o").replace("ṇ", "n").replace("ḷ", "l").replace("ṟ", "r"))
    w = w.replace("zh", "l").replace("ae", "e").replace("th", "t").replace("dh", "d").replace("ch", "s")
    w = re.sub(r"(.)\1+", r"\1", w)  # kannae -> kane, aa -> a
    return w


def _sim(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def align_text(timed: dict, text: str, duration: float | None = None) -> tuple[list[dict], float]:
    """Give correct lyrics (no timings) the timing of the existing (AI/online) lyrics.

    Returns (lines, matched_fraction). Words are aligned by sound with a dynamic-programming
    alignment; words that match nothing get times spread between their neighbours.
    """
    src = [(w["text"], float(w["start"]), float(w["end"]))
           for l in timed.get("lines", []) for w in (l.get("words") or [])]
    if not src:
        raise ValueError("This song has no timed lyrics to copy timing from. Use 'Tap along' instead.")
    lines = clean_pasted_lyrics(text)
    if not lines:
        raise ValueError("No lyrics found in the pasted text.")
    words = [(li, w) for li, line in enumerate(lines) for w in line.split()]

    a = [_sound(t) for t, _, _ in src]
    b = [_sound(w) for _, w in words]
    n, m = len(a), len(b)
    gap = -0.45
    # score matrix (n+1) x (m+1), kept small: lyrics are a few hundred words
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[0] * (m + 1) for _ in range(n + 1)]  # 1 = diag, 2 = up (skip src), 3 = left (skip pasted)
    for i in range(1, n + 1):
        score[i][0] = i * gap
        move[i][0] = 2
    for j in range(1, m + 1):
        score[0][j] = j * gap
        move[0][j] = 3
    for i in range(1, n + 1):
        ai = a[i - 1]
        row, prev = score[i], score[i - 1]
        for j in range(1, m + 1):
            s = _sim(ai, b[j - 1])
            d = prev[j - 1] + (2 * s - 1)
            u = prev[j] + gap
            l = row[j - 1] + gap
            if d >= u and d >= l:
                row[j], move[i][j] = d, 1
            elif u >= l:
                row[j], move[i][j] = u, 2
            else:
                row[j], move[i][j] = l, 3
    # walk back: pasted word j <- source word i (only when they really sound alike)
    times: list[tuple[float, float] | None] = [None] * m
    i, j = n, m
    while i > 0 and j > 0:
        mv = move[i][j]
        if mv == 1:
            if _sim(a[i - 1], b[j - 1]) >= 0.45:
                times[j - 1] = (src[i - 1][1], src[i - 1][2])
            i, j = i - 1, j - 1
        elif mv == 2:
            i -= 1
        else:
            j -= 1
    matched = sum(t is not None for t in times) / max(1, m)
    if matched == 0:
        raise ValueError("The pasted lyrics don't sound like this song's lyrics. Check it's the right song.")

    # fill gaps: spread unmatched words between the matched neighbours
    first = src[0][1]
    last = duration or src[-1][2] + 3
    k = 0
    while k < m:
        if times[k] is not None:
            k += 1
            continue
        e = k
        while e < m and times[e] is None:
            e += 1
        left = times[k - 1][1] if k > 0 else max(0.0, (times[e][0] if e < m else first) - 0.4 * (e - k))
        right = times[e][0] if e < m else min(last, left + 0.4 * (e - k) + 0.5)
        right = max(right, left + 0.05 * (e - k))
        step = (right - left) / (e - k)
        for q in range(k, e):
            times[q] = (left + step * (q - k), left + step * (q - k + 1))
        k = e

    # keep times moving forward
    for q in range(1, m):
        if times[q][0] < times[q - 1][0]:
            times[q] = (times[q - 1][0], max(times[q - 1][0], times[q][1]))

    out = []
    for li, line in enumerate(lines):
        idx = [q for q, (wl, _) in enumerate(words) if wl == li]
        ws = [{"start": round(times[q][0], 3), "end": round(max(times[q][1], times[q][0] + 0.05), 3),
               "text": words[q][1]} for q in idx]
        out.append({"start": ws[0]["start"], "text": line, "words": ws})
    return build_lines(out, duration), matched
