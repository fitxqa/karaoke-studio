"""Turn a saved recording into a shareable MP4: song video + your mixed voice + karaoke lyrics."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, media

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video")
_lock = threading.Lock()

W, H = 1280, 720


# ---------------------------------------------------------------- ASS subtitles

def _t(sec: float) -> str:
    sec = max(0.0, sec)
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return (text or "").replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def build_ass(lyr: dict, seg_start: float, seg_dur: float, *, title: str, singer: str | None,
              roman: bool, offset: float = 0.0) -> str:
    """Karaoke subtitles for the part of the song that was recorded.

    seg_start: song time where the video starts; offset: the player's per-song lyrics nudge
    (+ = lyrics later). ASS \\kf fills each word from white to cyan as it is sung.
    """
    font = "Nirmala UI" if os.name == "nt" else "Noto Sans Tamil"
    has_roman = roman and any(l.get("roman") for l in lyr.get("lines", []))
    main_v, roman_v, next_v = (158, 110, 44) if has_roman else (120, 0, 48)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},58,&H00FFD334,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,40,40,{main_v},1
Style: Roman,Arial,34,&H00FFE37F,&H00E6E6E6,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,2.5,1,2,40,40,{roman_v},1
Style: Next,{font},36,&H40FFFFFF,&H40FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,2.5,1,2,40,40,{next_v},1
Style: Title,Arial,46,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,3,14,0,8,60,60,46,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    sub = f"\\N{{\\fs30\\c&H00FFD334&}}Sung by {_clean(singer)}" if singer else ""
    ev.append(f"Dialogue: 2,{_t(0)},{_t(min(4.5, seg_dur))},Title,,0,0,0,,{{\\fad(300,500)}}{_clean(title)}{sub}")

    lines = lyr.get("lines", [])
    seg_end = seg_start + seg_dur
    for i, l in enumerate(lines):
        ls, le = l["start"] + offset, l["end"] + offset
        if le <= seg_start or ls >= seg_end:
            continue
        a, b = ls - seg_start, le - seg_start
        # word-by-word fill
        parts, prev = [], ls
        words = l.get("words") or [{"start": ls, "end": le, "text": l["text"]}]
        for w in words:
            ws, we = w["start"] + offset, w["end"] + offset
            if ws > prev:
                parts.append(f"{{\\k{int(round((ws - prev) * 100))}}}")
            parts.append(f"{{\\kf{max(1, int(round((we - max(ws, prev)) * 100)))}}}{_clean(w['text'])} ")
            prev = max(prev, we)
        ev.append(f"Dialogue: 1,{_t(a)},{_t(b)},Main,,0,0,0,,{''.join(parts).rstrip()}")
        if has_roman and l.get("roman"):
            w0 = words[0]["start"] + offset
            lead = max(0, int(round((w0 - ls) * 100)))
            fill = max(1, int(round((prev - w0) * 100)))
            ev.append(f"Dialogue: 3,{_t(a)},{_t(b)},Roman,,0,0,0,,{{\\k{lead}}}{{\\kf{fill}}}{_clean(l['roman'])}")
        if i + 1 < len(lines):
            ev.append(f"Dialogue: 0,{_t(a)},{_t(b)},Next,,0,0,0,,{_clean(lines[i + 1]['text'])}")
    return head + "\n".join(ev) + "\n"


# ---------------------------------------------------------------- render job

def status_path(rec_dir: Path, rid: str) -> Path:
    return rec_dir / f"{rid}.video.json"


def read_status(rec_dir: Path, rid: str) -> dict:
    p = status_path(rec_dir, rid)
    if (rec_dir / f"{rid}.mp4").exists() and not p.exists():
        return {"status": "done", "progress": 100}
    if not p.exists():
        return {"status": "none", "progress": 0}
    return json.loads(p.read_text())


def _write_status(rec_dir: Path, rid: str, **data) -> None:
    with _lock:
        status_path(rec_dir, rid).write_text(json.dumps(data))


KARAOKE_ID = "karaoke"  # the whole-song karaoke video (music only + lyrics)


def start(job_dir: Path, rid: str, lyr: dict, *, title: str, singer: str | None, roman: bool,
          offset: float) -> dict:
    rec_dir = job_dir / "recordings"
    rec_dir.mkdir(exist_ok=True)
    cur = read_status(rec_dir, rid)
    if cur["status"] == "rendering":
        return cur
    _write_status(rec_dir, rid, status="rendering", progress=0)
    _pool.submit(_render, job_dir, rid, lyr, title, singer, roman, offset)
    return {"status": "rendering", "progress": 0}


def _render(job_dir: Path, rid: str, lyr: dict, title: str, singer: str | None, roman: bool,
            offset: float) -> None:
    rec_dir = job_dir / "recordings"
    try:
        if rid == KARAOKE_ID:  # whole song, karaoke music (no vocals)
            audio = job_dir / "no_vocals.wav"
            seg_start = 0.0
        else:
            info = json.loads((rec_dir / f"{rid}.json").read_text())
            audio = rec_dir / f"{rid}.mp3"
            seg_start = max(0.0, float(info.get("song_start", 0.0)) - float(info.get("latency_ms", 0.0)) / 1000)
        dur = media.ffprobe_duration(audio) or 1.0
        ass = rec_dir / f"{rid}.ass"
        ass.write_text(build_ass(lyr, seg_start, dur, title=title, singer=singer, roman=roman,
                                 offset=offset), encoding="utf-8")
        # "ass" filter with complex shaping = correct Tamil/Indic letter joining
        subs = f"ass=filename={ass.name}:shaping=complex"
        if os.name == "nt":
            subs += ":fontsdir='C\\:/Windows/Fonts'"
        elif os.environ.get("KARAOKE_FONTS_DIR"):
            subs += f":fontsdir={os.environ['KARAOKE_FONTS_DIR']}"
        vf = f"scale=w={W}:h={H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,{subs}"
        tmp = rec_dir / f"{rid}.tmp.mp4"
        cmd = [config.FFMPEG, "-y", "-ss", f"{seg_start:.3f}", "-i", str(job_dir / "karaoke.mp4"),
               "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-t", f"{dur:.3f}", "-shortest",
               "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(tmp)]
        proc = subprocess.Popen(cmd, cwd=rec_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        err_tail: list[str] = []

        def drain():
            for line in proc.stderr:
                err_tail.append(line)
                del err_tail[:-30]

        t = threading.Thread(target=drain, daemon=True)
        t.start()
        for line in proc.stdout:
            if line.startswith("out_time_ms="):
                try:
                    pct = min(99, int(int(line.split("=")[1]) / 1e6 / dur * 100))
                    _write_status(rec_dir, rid, status="rendering", progress=pct)
                except ValueError:
                    pass
        proc.wait()
        t.join(timeout=2)
        if proc.returncode != 0 or not tmp.exists():
            raise RuntimeError("".join(err_tail)[-1200:] or "ffmpeg failed")
        tmp.replace(rec_dir / f"{rid}.mp4")
        _write_status(rec_dir, rid, status="done", progress=100)
    except Exception as e:  # noqa: BLE001
        _write_status(rec_dir, rid, status="error", progress=0, error=str(e)[-1500:])
