"""Karaoke Studio server — HTTP API + the web app (served at /).

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import threading

from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, duet, jobs, lyrics, media, share_video


@asynccontextmanager
async def lifespan(_app):
    jobs.resume_unfinished()
    yield


APP_VERSION = "2.0.0"
app = FastAPI(title="Karaoke Studio", version=APP_VERSION, lifespan=lifespan)


# The Android app runs from its own origin and talks to this server over Wi-Fi.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Content-Length", "Content-Disposition"])


@app.middleware("http")
async def no_stale_app(request, call_next):
    """Make browsers re-check the app's own files every time, so updates show up at once."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


COOKIE = "karaoke_key"


def auth(x_api_key: str | None = Header(default=None),
         karaoke_key: str | None = Cookie(default=None),
         key: str | None = Query(default=None)):
    # header (apps), cookie (browser after login) or ?key= (video/audio links in the phone app)
    if config.API_KEY and config.API_KEY not in (x_api_key, karaoke_key, key):
        raise HTTPException(401, "Wrong or missing API key")


class Login(BaseModel):
    key: str


@app.post("/login")
def login(body: Login, response: Response):
    if config.API_KEY and body.key != config.API_KEY:
        raise HTTPException(401, "Wrong key")
    response.set_cookie(COOKIE, body.key, max_age=365 * 24 * 3600, httponly=True, samesite="lax")
    return {"ok": True}


def _job(job_id: str) -> dict:
    try:
        meta = jobs.load(job_id)
    except ValueError:
        meta = None
    if meta is None:
        raise HTTPException(404, "Song not found")
    return meta


ALLOWED_UPLOADS = {".mp3", ".mp4", ".m4a", ".wav", ".flac", ".ogg", ".mkv", ".webm", ".aac", ".mov"}
MEDIA_FILES = {"karaoke.mp4", "thumb.jpg", "no_vocals.wav", "vocals.wav"}


class NewSong(BaseModel):
    url: str
    language: str = "auto"  # auto / ta / en / ml / hi ...
    quality: str = "fast"  # fast / best (cleaner vocal removal, slower)


class LyricsText(BaseModel):
    lrc: str
    language: str | None = None


class EditedLine(BaseModel):
    start: float
    text: str
    words: list[dict] | None = None
    singer: str | None = None  # duet part set by hand: M / F / B


class EditedLyrics(BaseModel):
    lines: list[EditedLine]
    language: str | None = None


class VideoRequest(BaseModel):
    part: str | None = None  # karaoke video for a duet part: M or F = the part YOU sing
    singer: str | None = None
    roman: bool = True
    offset: float = 0.0  # the player's "Lyrics timing" value for this song


class PastedLyrics(BaseModel):
    text: str
    language: str | None = None


class RedoLyrics(BaseModel):
    source: str = "whisper"  # whisper / lrclib
    language: str | None = None


@app.get("/health")
def health():
    return {"ok": True, "version": APP_VERSION, "ffmpeg": shutil.which(config.FFMPEG) is not None,
            "auth": bool(config.API_KEY)}


def _lan_addresses(port: int = 8000) -> list[str]:
    import socket

    ips = set()
    try:  # the address used to reach the internet = the Wi-Fi/LAN address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips = [ip for ip in ips if not ip.startswith(("127.", "169.254."))]
    return [f"http://{ip}:{port}" for ip in sorted(ips)]


@app.get("/network")
def network():
    """Addresses the phone app can use to connect (same Wi-Fi)."""
    return {"addresses": _lan_addresses(), "auth": bool(config.API_KEY)}


@app.get("/songs", dependencies=[Depends(auth)])
def list_songs():
    return jobs.all_jobs()


@app.post("/songs", dependencies=[Depends(auth)])
def add_song(body: NewSong):
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Paste a full YouTube link (https://...)")
    meta = jobs.create(source_url=url, language=body.language, quality=body.quality)
    jobs.submit(meta["id"])
    return meta


@app.post("/songs/upload", dependencies=[Depends(auth)])
def upload_song(file: UploadFile = File(...), language: str = Form("auto"),
                title: str | None = Form(None), artist: str | None = Form(None),
                quality: str = Form("fast")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOADS:
        raise HTTPException(400, f"Unsupported file type {ext}")
    name = Path(file.filename).stem
    meta = jobs.create(upload_name=name, language=language, title=title or name, artist=artist, quality=quality)
    d = jobs.job_dir(meta["id"])
    with open(d / f"source{ext}", "wb") as out:
        shutil.copyfileobj(file.file, out)
    jobs.update(meta["id"], source_file=f"source{ext}")
    jobs.submit(meta["id"])
    return jobs.load(meta["id"])


@app.get("/songs/{job_id}", dependencies=[Depends(auth)])
def get_song(job_id: str):
    return _job(job_id)


@app.delete("/songs/{job_id}", dependencies=[Depends(auth)])
def delete_song(job_id: str):
    meta = _job(job_id)
    if meta["status"] not in ("done", "error"):
        raise HTTPException(409, "Song is still being processed")
    shutil.rmtree(jobs.job_dir(job_id), ignore_errors=True)
    return {"deleted": job_id}


@app.post("/songs/{job_id}/retry", dependencies=[Depends(auth)])
def retry_song(job_id: str):
    meta = _job(job_id)
    if meta["status"] != "error":
        raise HTTPException(409, "Only failed songs can be retried")
    jobs.update(job_id, status="queued", progress="Waiting in queue", error=None)
    jobs.submit(job_id)
    return jobs.load(job_id)


@app.get("/songs/{job_id}/files/{name}", dependencies=[Depends(auth)])
def get_file(job_id: str, name: str):
    _job(job_id)
    if name not in MEDIA_FILES:
        raise HTTPException(404, "No such file")
    p = jobs.job_dir(job_id) / name
    if not p.exists():
        raise HTTPException(404, "File not ready")
    return FileResponse(p)


_audio_locks: dict[str, threading.Lock] = {}


def _lyrics_of(job_id: str) -> dict:
    p = jobs.job_dir(job_id) / "lyrics.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"lines": []}


def _part_wav(job_id: str, keep: str) -> Path:
    """Vocal track with only one singer (M or F) left in, following the current lyrics/corrections."""
    d = jobs.job_dir(job_id)
    segs = duet.effective_segments(jobs.duet_info(job_id), _lyrics_of(job_id))
    out = d / "cache" / f"part_{keep}_{duet.segments_hash(segs)}.wav"
    out.parent.mkdir(exist_ok=True)
    lock = _audio_locks.setdefault(str(out), threading.Lock())
    with lock:
        return duet.render_part(d / "vocals.wav", segs, keep, out)


_audio_locks: dict[str, threading.Lock] = {}


@app.get("/songs/{job_id}/audio/{kind}.m4a", dependencies=[Depends(auth)])
def get_audio(job_id: str, kind: str, key: int = 0, speed: float = 1.0):
    """music (no vocals), vocals (the singer), male / female (one duet singer only);
    moved to another key and/or slowed down for practice (pitch kept)."""
    _job(job_id)
    if kind not in ("music", "vocals", "male", "female") or not -12 <= key <= 12:
        raise HTTPException(404, "No such audio")
    speed = round(max(0.5, min(1.25, speed)) * 20) / 20  # 5 % steps
    d = jobs.job_dir(job_id)
    if not (d / "vocals.wav").exists():
        raise HTTPException(409, "Song is not processed yet")
    if kind in ("male", "female"):
        src = _part_wav(job_id, "M" if kind == "male" else "F")
        tag = f"{kind}_{src.stem.split('_')[-1]}"
    else:
        src = d / ("no_vocals.wav" if kind == "music" else "vocals.wav")
        tag = kind
    out = d / "cache" / f"{tag}_k{key:+d}_s{int(speed * 100)}.m4a"
    out.parent.mkdir(exist_ok=True)
    lock = _audio_locks.setdefault(str(out), threading.Lock())
    with lock:  # two tabs asking for the same version render it once
        try:
            media.shifted_audio(src, out, key, speed)
        except RuntimeError as e:
            raise HTTPException(500, f"Could not prepare the audio: {e}") from e
    return FileResponse(out, media_type="audio/mp4")


@app.get("/songs/{job_id}/duet", dependencies=[Depends(auth)])
def get_duet(job_id: str):
    """Who sings when: {"duet": bool, "solo": "M"|"F", "segments": [{start, end, singer}]}."""
    _job(job_id)
    if not (jobs.job_dir(job_id) / "pitch.json").exists():
        return {"duet": False, "solo": "M", "segments": []}
    return jobs.duet_info(job_id)


class QueueBody(BaseModel):
    items: list[dict]


@app.get("/queue", dependencies=[Depends(auth)])
def get_queue():
    return jobs.get_queue()


@app.put("/queue", dependencies=[Depends(auth)])
def put_queue(body: QueueBody):
    return jobs.set_queue(body.items)


@app.get("/songs/{job_id}/lyrics", dependencies=[Depends(auth)])
def get_lyrics(job_id: str):
    _job(job_id)
    p = jobs.job_dir(job_id) / "lyrics.json"
    if not p.exists():
        raise HTTPException(404, "Lyrics not ready")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/songs/{job_id}/lyrics.lrc", dependencies=[Depends(auth)], response_class=PlainTextResponse)
def get_lyrics_lrc(job_id: str):
    return lyrics.to_lrc(get_lyrics(job_id))


@app.put("/songs/{job_id}/lyrics", dependencies=[Depends(auth)])
def put_lyrics(job_id: str, body: LyricsText):
    """Paste your own LRC lyrics (e.g. for Tamil songs that aren't online)."""
    meta = _job(job_id)
    lines = lyrics.parse_lrc(body.lrc, meta.get("duration"))
    if not lines:
        raise HTTPException(400, "No timed lines found. Use LRC format: [mm:ss.xx] lyric line")
    lyr = lyrics.finalize(lines, "manual", body.language)
    jobs.save_lyrics(job_id, lyr)
    return lyr


@app.put("/songs/{job_id}/lyrics/lines", dependencies=[Depends(auth)])
def put_lyrics_lines(job_id: str, body: EditedLyrics):
    """Save lyrics corrected line by line in the player (text and timing)."""
    meta = _job(job_id)
    lines = lyrics.build_lines([l.model_dump() for l in body.lines], meta.get("duration"))
    if not lines:
        raise HTTPException(400, "There are no lyric lines left")
    lang = body.language or meta.get("lyrics_language")
    lyr = lyrics.finalize(lines, "manual", lang)
    jobs.save_lyrics(job_id, lyr)
    return lyr


@app.post("/songs/{job_id}/lyrics/align", dependencies=[Depends(auth)])
def align_lyrics(job_id: str, body: PastedLyrics):
    """Correct lyrics copied from a website, matched to the singer's voice (runs in the background).
    Poll GET /songs/{id}: align_status = working / done / error."""
    _job(job_id)
    if not (jobs.job_dir(job_id) / "vocals.wav").exists():
        raise HTTPException(409, "Song is not processed yet")
    if not lyrics.clean_pasted_lyrics(body.text):
        raise HTTPException(400, "No lyrics found in the pasted text.")
    jobs.update(job_id, align_status="working", align_error=None, align_progress="Starting")
    jobs.align_lyrics_async(job_id, body.text, body.language)
    return {"status": "working"}


@app.post("/songs/{job_id}/lyrics/redo", dependencies=[Depends(auth)])
def redo_lyrics(job_id: str, body: RedoLyrics):
    meta = _job(job_id)
    if not (jobs.job_dir(job_id) / "vocals.wav").exists():
        raise HTTPException(409, "Song is not processed yet")
    if body.source not in ("whisper", "lrclib"):
        raise HTTPException(400, "source must be whisper or lrclib")
    jobs.redo_lyrics(job_id, body.source, body.language)
    return meta


@app.get("/songs/{job_id}/pitch", dependencies=[Depends(auth)])
def get_pitch(job_id: str):
    _job(job_id)
    p = jobs.job_dir(job_id) / "pitch.json"
    if not p.exists():
        raise HTTPException(404, "Pitch not ready")
    return json.loads(p.read_text())


@app.post("/songs/{job_id}/recordings", dependencies=[Depends(auth)])
def add_recording(job_id: str, voice: UploadFile = File(...), song_start: float = Form(0.0),
                  semitones: float = Form(0.0), latency_ms: float = Form(150.0),
                  score: float | None = Form(None), effect: str = Form("studio"),
                  part: str | None = Form(None), partner_gain: float = Form(0.9),
                  singer: str | None = Form(None)):
    """Upload the singer's voice (WAV); returns an MP3 mixed with the karaoke track."""
    _job(job_id)
    d = jobs.job_dir(job_id)
    music = d / "no_vocals.wav"
    if not music.exists():
        raise HTTPException(409, "Song is not processed yet")
    rec_dir = d / "recordings"
    rec_dir.mkdir(exist_ok=True)
    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    voice_path = rec_dir / f"{rid}-voice.wav"
    with open(voice_path, "wb") as out:
        shutil.copyfileobj(voice.file, out)
    mp3 = rec_dir / f"{rid}.mp3"
    try:
        partner = None
        if part in ("M", "F") and (d / "pitch.json").exists():
            partner = _part_wav(job_id, "F" if part == "M" else "M")  # the other singer keeps singing
        media.mix_recording(music, voice_path, mp3, song_start=song_start,
                            semitones=max(-12.0, min(12.0, semitones)), latency_ms=latency_ms,
                            effect=effect, partner=partner, partner_gain=max(0.0, min(1.5, partner_gain)))
    except RuntimeError as e:
        raise HTTPException(500, f"Mixing failed: {e}") from e
    info = {"id": rid, "song_start": song_start, "semitones": semitones, "score": score,
            "latency_ms": latency_ms, "effect": effect, "part": part,
            "singer": (singer or "").strip()[:40] or None,
            "created": time.time(), "file": mp3.name}
    (rec_dir / f"{rid}.json").write_text(json.dumps(info))
    if score is not None:
        jobs.add_score(job_id, score, singer, rid)
    return info


@app.get("/songs/{job_id}/recordings", dependencies=[Depends(auth)])
def list_recordings(job_id: str):
    _job(job_id)
    rec_dir = jobs.job_dir(job_id) / "recordings"
    if not rec_dir.exists():
        return []
    items = []
    for p in rec_dir.glob("*.json"):
        if p.name.endswith(".video.json"):
            continue
        info = json.loads(p.read_text())
        info["has_video"] = (rec_dir / f"{info['id']}.mp4").exists()
        items.append(info)
    return sorted(items, key=lambda r: r["created"], reverse=True)


@app.get("/songs/{job_id}/recordings/{rid}.mp3", dependencies=[Depends(auth)])
def get_recording(job_id: str, rid: str):
    _job(job_id)
    if not rid.replace("-", "").isalnum():
        raise HTTPException(404)
    p = jobs.job_dir(job_id) / "recordings" / f"{rid}.mp3"
    if not p.exists():
        raise HTTPException(404, "Recording not found")
    return FileResponse(p, media_type="audio/mpeg", filename=f"karaoke-{rid}.mp3")


def _rec_dir(job_id: str, rid: str) -> Path:
    _job(job_id)
    if not rid.replace("-", "").isalnum():
        raise HTTPException(404)
    d = jobs.job_dir(job_id) / "recordings"
    if rid.startswith(share_video.KARAOKE_ID):  # the karaoke video itself (karaoke, karaoke-M, karaoke-F)
        if not (jobs.job_dir(job_id) / "no_vocals.wav").exists():
            raise HTTPException(409, "Song is not processed yet")
        d.mkdir(exist_ok=True)
        return d
    if not (d / f"{rid}.mp3").exists():
        raise HTTPException(404, "Recording not found")
    return d


@app.post("/songs/{job_id}/recordings/{rid}/video", dependencies=[Depends(auth)])
def make_video(job_id: str, rid: str, body: VideoRequest):
    """Start making a shareable MP4 (song video + your voice + karaoke lyrics)."""
    meta = _job(job_id)
    _rec_dir(job_id, rid)
    p = jobs.job_dir(job_id) / "lyrics.json"
    lyr = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"lines": []}
    singer = (body.singer or "").strip()[:60] or None
    if rid.startswith(share_video.KARAOKE_ID) and rid != share_video.KARAOKE_ID:
        you = rid.split("-")[1] if "-" in rid else ""
        if you not in ("M", "F"):
            raise HTTPException(404)
        d = jobs.job_dir(job_id)
        partner = _part_wav(job_id, "F" if you == "M" else "M")
        media.run([config.FFMPEG, "-y", "-i", str(d / "no_vocals.wav"), "-i", str(partner), "-filter_complex",
                   "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]", "-map", "[a]",
                   str(d / "recordings" / f"{rid}.wav")])
    return share_video.start(jobs.job_dir(job_id), rid, lyr, title=meta.get("title") or "Karaoke",
                             singer=singer, roman=body.roman, offset=max(-30.0, min(30.0, body.offset)))


@app.get("/songs/{job_id}/recordings/{rid}/video", dependencies=[Depends(auth)])
def video_status(job_id: str, rid: str):
    return share_video.read_status(_rec_dir(job_id, rid), rid)


@app.get("/songs/{job_id}/recordings/{rid}.mp4", dependencies=[Depends(auth)])
def get_video(job_id: str, rid: str):
    d = _rec_dir(job_id, rid)
    p = d / f"{rid}.mp4"
    if not p.exists():
        raise HTTPException(404, "Video not made yet")
    title = (_job(job_id).get("title") or "karaoke")
    safe = "".join(c for c in title if c.isalnum() or c in " -_")[:60].strip() or "karaoke"
    if rid == share_video.KARAOKE_ID:
        name = f"{safe} - karaoke.mp4"
    elif rid.startswith(share_video.KARAOKE_ID):
        name = f"{safe} - duet karaoke ({'male' if rid.endswith('M') else 'female'} part).mp4"
    else:
        name = f"{safe} - my singing {rid}.mp4"
    return FileResponse(p, media_type="video/mp4", filename=name)


# The web app. Mounted last so the API routes above take priority.
WEB_DIR = Path(__file__).resolve().parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
