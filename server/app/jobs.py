"""Job store (one folder per song) and the background worker that builds each karaoke."""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, duet, lyrics, media, pitch

_lock = threading.Lock()
# One song at a time: Demucs and Whisper each want the whole GPU/CPU.
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="karaoke")


def job_dir(job_id: str) -> Path:
    if not job_id.replace("-", "").isalnum():
        raise ValueError("bad job id")
    return config.JOBS_DIR / job_id


def load(job_id: str) -> dict | None:
    p = job_dir(job_id) / "meta.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save(meta: dict) -> None:
    d = job_dir(meta["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "meta.json")


def update(job_id: str, **fields) -> dict:
    with _lock:
        meta = load(job_id) or {"id": job_id}
        meta.update(fields)
        meta["updated"] = time.time()
        save(meta)
        return meta


def all_jobs() -> list[dict]:
    out = []
    for d in config.JOBS_DIR.iterdir():
        if (d / "meta.json").exists():
            try:
                out.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda m: m.get("created", 0), reverse=True)
    return out


def create(*, source_url: str | None = None, upload_name: str | None = None,
           language: str = "auto", title: str | None = None, artist: str | None = None,
           quality: str = "fast") -> dict:
    job_id = uuid.uuid4().hex[:12]
    meta = {
        "id": job_id, "status": "queued", "progress": "Waiting in queue",
        "source_url": source_url, "upload_name": upload_name, "language": language,
        "quality": quality if quality in ("fast", "best") else "fast",
        "title": title or upload_name or source_url, "artist": artist,
        "created": time.time(), "updated": time.time(), "error": None,
    }
    save(meta)
    return meta


def submit(job_id: str) -> None:
    _worker.submit(_build, job_id)


def resume_unfinished() -> None:
    """Jobs interrupted by a server restart go back in the queue."""
    for m in all_jobs():
        if m.get("status") not in ("done", "error"):
            update(m["id"], status="queued", progress="Waiting in queue (resumed)")
            submit(m["id"])


# ---------------------------------------------------------------- the pipeline

def _build(job_id: str) -> None:
    d = job_dir(job_id)
    meta = load(job_id) or {}
    timings: dict[str, float] = {}
    t0 = time.time()

    def lap(name: str, since: float) -> float:
        now = time.time()
        timings[name] = round(now - since, 1)
        return now

    side = ThreadPoolExecutor(max_workers=3, thread_name_prefix=f"job-{job_id}")
    try:
        # 1. get the media
        t = time.time()
        if meta.get("source_url"):
            update(job_id, status="downloading", progress="Downloading from YouTube (HD)")
            info = media.download_youtube(meta["source_url"], d,
                                          lambda p: update(job_id, progress=p))
            meta = update(job_id, **{k: v for k, v in info.items()
                                     if v is not None or k not in ("title", "artist")})
        src = d / meta["source_file"]
        has_video = media.has_video_stream(src)
        if meta.get("duration") is None:
            meta = update(job_id, duration=media.ffprobe_duration(src))
        t = lap("download", t)

        # Look for synced lyrics online while the vocals are being removed.
        online = side.submit(_search_online, dict(meta))
        if has_video and not (d / "thumb.jpg").exists():
            side.submit(media.make_thumbnail, src, d / "thumb.jpg")

        # 2. split vocals / music (the slow part)
        update(job_id, status="separating", progress="Removing vocals (the slow part)")
        wav = d / "audio.wav"
        media.extract_audio(src, wav)
        vocals, music = media.separate(wav, d, meta.get("quality"))
        t = lap("vocal_removal", t)

        # 3. video, lyrics and melody at the same time
        update(job_id, status="muxing", progress="Making karaoke video, lyrics and melody")
        f_video = side.submit(media.mux_karaoke, src if has_video else None, music, wav, d / "karaoke.mp4")
        f_pitch = side.submit(pitch.vocal_pitch, str(vocals))
        lrc = online.result()
        if lrc and (lines := lyrics.parse_lrc(lrc, meta.get("duration"))):
            lyr = lyrics.finalize(lines, "lrclib")
        else:
            update(job_id, status="lyrics", progress="AI is writing the lyrics from the vocals")
            lyr = lyrics.transcribe(str(vocals), meta.get("language") or "auto")
        (d / "lyrics.json").write_text(json.dumps(lyr, ensure_ascii=False), encoding="utf-8")
        update(job_id, progress="Finishing the karaoke video")
        ref = f_pitch.result()
        (d / "pitch.json").write_text(json.dumps(ref), encoding="utf-8")
        duet_info = duet.analyse(ref)
        (d / "duet.json").write_text(json.dumps(duet_info), encoding="utf-8")
        f_video.result()
        lap("video_lyrics_melody", t)

        wav.unlink(missing_ok=True)
        timings["total"] = round(time.time() - t0, 1)
        update(job_id, status="done", progress="Ready", has_video=has_video,
               lyrics_source=lyr["source"], lyrics_language=lyr["language"],
               lyrics_lines=len(lyr["lines"]), timings=timings, duet=duet_info["duet"])
    except Exception as e:  # noqa: BLE001 - report every failure to the app
        traceback.print_exc()
        update(job_id, status="error", progress="Failed", error=str(e)[:2000], timings=timings)
    finally:
        side.shutdown(wait=False)


def _search_online(meta: dict) -> str | None:
    try:
        return lyrics.search_lrclib(meta.get("full_title") or meta.get("title") or "",
                                    meta.get("artist"), meta.get("track"), meta.get("duration"))
    except Exception:
        return None


def _find_lyrics(meta: dict, vocals: Path) -> dict:
    lang = meta.get("language") or "auto"
    lrc = None
    try:
        lrc = lyrics.search_lrclib(meta.get("full_title") or meta.get("title") or "",
                                   meta.get("artist"), meta.get("track"), meta.get("duration"))
    except Exception:
        lrc = None
    if lrc:
        lines = lyrics.parse_lrc(lrc, meta.get("duration"))
        if lines:
            return lyrics.finalize(lines, "lrclib")
    return lyrics.transcribe(str(vocals), lang)


def redo_lyrics(job_id: str, source: str, language: str | None = None) -> None:
    """Re-run lyrics in the background: source = 'lrclib' or 'whisper'."""
    def work():
        d = job_dir(job_id)
        meta = load(job_id) or {}
        prev_status = meta.get("status")
        try:
            update(job_id, progress=f"Re-doing lyrics ({source})")
            if source == "whisper":
                lyr = lyrics.transcribe(str(d / "vocals.wav"), language or meta.get("language"))
            else:
                lrc = lyrics.search_lrclib(meta.get("full_title") or meta.get("title") or "",
                                           meta.get("artist"), meta.get("track"), meta.get("duration"))
                if not lrc:
                    update(job_id, progress="No synced lyrics found online")
                    return
                lyr = lyrics.finalize(lyrics.parse_lrc(lrc, meta.get("duration")), "lrclib")
            save_lyrics(job_id, lyr)
            update(job_id, progress="Ready", status=prev_status)
        except Exception as e:  # noqa: BLE001
            update(job_id, progress=f"Lyrics failed: {e}"[:300], status=prev_status)

    _worker.submit(work)


_aligner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="align")


def align_lyrics_async(job_id: str, text: str, language: str | None) -> None:
    """Pasted correct lyrics -> timed to the singer's voice (falls back to the current lyrics' timing)."""
    def work():
        d = job_dir(job_id)
        meta = load(job_id) or {}
        update(job_id, align_status="working", align_error=None, align_progress="Starting")
        method, matched = "voice", 0.0
        try:
            from . import voice_align

            lines, matched = voice_align.align_to_voice(
                str(d / "vocals.wav"), text, meta.get("duration"),
                progress=lambda m: update(job_id, align_progress=m))
        except Exception as voice_err:  # noqa: BLE001
            traceback.print_exc()
            method = "text"
            try:
                update(job_id, align_progress="Voice matching unavailable - using the current timing")
                current = json.loads((d / "lyrics.json").read_text(encoding="utf-8"))
                lines, matched = lyrics.align_text(current, text, meta.get("duration"))
            except Exception as e:  # noqa: BLE001
                update(job_id, align_status="error", align_error=f"{e} (voice matching: {voice_err})"[:500])
                return
        lyr = lyrics.finalize(lines, "manual", language)
        save_lyrics(job_id, lyr)
        update(job_id, align_status="done", align_method=method, align_matched=round(matched, 3),
               align_progress="Done")

    _aligner.submit(work)


def save_lyrics(job_id: str, lyr: dict) -> None:
    (job_dir(job_id) / "lyrics.json").write_text(json.dumps(lyr, ensure_ascii=False), encoding="utf-8")
    update(job_id, lyrics_source=lyr["source"], lyrics_language=lyr["language"],
           lyrics_lines=len(lyr["lines"]),
           lyrics_version=int(time.time()))


def duet_info(job_id: str) -> dict:
    """duet.json, worked out (and saved) from pitch.json for songs made before duet mode existed."""
    d = job_dir(job_id)
    p = d / "duet.json"
    if p.exists():
        return json.loads(p.read_text())
    pp = d / "pitch.json"
    info = duet.analyse(json.loads(pp.read_text())) if pp.exists() else {"duet": False, "solo": "M", "segments": []}
    p.write_text(json.dumps(info))
    update(job_id, duet=info["duet"])
    return info


def add_score(job_id: str, score: float, singer: str | None, rec_id: str) -> None:
    meta = load(job_id) or {}
    scores = meta.get("scores", [])
    scores.append({"score": round(score, 1), "singer": (singer or "").strip()[:40] or None,
                   "rec": rec_id, "created": time.time()})
    scores.sort(key=lambda s: s["score"], reverse=True)
    update(job_id, scores=scores[:10], best_score=scores[0]["score"], best_singer=scores[0]["singer"])


# ---------------------------------------------------------------- party queue (shared by PC and phones)

def _queue_path() -> Path:
    return config.DATA_DIR / "queue.json"


def get_queue() -> list[dict]:
    p = _queue_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def set_queue(items: list[dict]) -> list[dict]:
    clean = [{"id": str(i.get("id") or uuid.uuid4().hex[:8])[:16], "song_id": str(i["song_id"])[:16],
              "singer": (str(i.get("singer") or "").strip()[:40] or None)}
             for i in items if i.get("song_id")][:100]
    with _lock:
        _queue_path().write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    return clean
