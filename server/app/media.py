"""Media steps: YouTube download, audio extraction, vocal removal, muxing, recording mix."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import config


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {tail}")


def ffprobe_duration(path: Path) -> float | None:
    probe = shutil.which("ffprobe") or "ffprobe"
    proc = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def has_video_stream(path: Path) -> bool:
    probe = shutil.which("ffprobe") or "ffprobe"
    proc = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    return "video" in proc.stdout


# ---------------------------------------------------------------- download

def download_youtube(url: str, out_dir: Path, progress: Callable[[str], None]) -> dict:
    """Download the video (max 720p by default). Returns metadata."""
    import yt_dlp

    h = config.MAX_VIDEO_HEIGHT

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(f"Downloading {d.get('downloaded_bytes', 0) * 100 // total}%")

    opts = {
        # best video up to 1080p, preferring H.264 (plays everywhere), plus best audio
        "format": (f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
                   f"bv*[height<={h}]+ba/b[height<={h}]/b"),
        "format_sort": [f"res:{h}", "fps"],
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "writethumbnail": True,
        "progress_hooks": [hook],
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}],
        "ffmpeg_location": shutil.which(config.FFMPEG) or None,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    src = next((p for p in out_dir.glob("source.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm")), None)
    if src is None:
        raise RuntimeError("Download finished but no video file was found")
    thumb = next(out_dir.glob("source.jpg"), None)
    if thumb:
        thumb.rename(out_dir / "thumb.jpg")
    return {
        "title": info.get("track") or info.get("title") or "Untitled",
        "full_title": info.get("title"),
        "artist": info.get("artist") or info.get("creator") or info.get("uploader"),
        "track": info.get("track"),
        "duration": info.get("duration"),
        "source_file": src.name,
    }


def extract_audio(src: Path, wav: Path) -> None:
    run([config.FFMPEG, "-y", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100", str(wav)])


def make_thumbnail(src: Path, jpg: Path) -> None:
    try:
        run([config.FFMPEG, "-y", "-ss", "20", "-i", str(src), "-frames:v", "1", "-vf",
             "scale=480:-2", str(jpg)])
    except RuntimeError:
        pass


# ---------------------------------------------------------------- vocal removal

QUALITY_MODELS = {"fast": "htdemucs", "best": "htdemucs_ft"}


def separate(wav: Path, out_dir: Path, quality: str | None = None) -> tuple[Path, Path]:
    """Demucs two-stem split. Returns (vocals.wav, no_vocals.wav).
    quality: "fast" (htdemucs) or "best" (htdemucs_ft, cleaner, ~4x slower); default from config."""
    model = QUALITY_MODELS.get(quality or "", config.DEMUCS_MODEL)
    run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", model,
         "-j", str(config.DEMUCS_JOBS), "-o", str(out_dir), str(wav)])
    stem_dir = out_dir / model / wav.stem
    vocals, music = stem_dir / "vocals.wav", stem_dir / "no_vocals.wav"
    if not vocals.exists() or not music.exists():
        raise RuntimeError("Demucs did not produce stems")
    final_v, final_m = out_dir / "vocals.wav", out_dir / "no_vocals.wav"
    shutil.move(str(vocals), final_v)
    shutil.move(str(music), final_m)
    shutil.rmtree(out_dir / model, ignore_errors=True)
    return final_v, final_m


# ---------------------------------------------------------------- karaoke video

def mux_karaoke(video_src: Path | None, music: Path, original: Path, out: Path) -> None:
    """karaoke.mp4: video + audio track 1 = karaoke (no vocals), track 2 = original song."""
    cmd = [config.FFMPEG, "-y"]
    if video_src is not None:
        cmd += ["-i", str(video_src)]
        vmap, vcodec = "0:v:0", ["-c:v", "copy"]
    else:  # audio-only upload: plain dark background
        cmd += ["-f", "lavfi", "-i", "color=c=0x101018:s=1280x720:r=25"]
        vmap, vcodec = "0:v:0", ["-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
                                 "-pix_fmt", "yuv420p"]
    cmd += ["-i", str(music), "-i", str(original),
            "-map", vmap, "-map", "1:a:0", "-map", "2:a:0", *vcodec,
            "-c:a", "aac", "-b:a", "256k",
            "-metadata:s:a:0", "handler_name=Karaoke", "-metadata:s:a:1", "handler_name=Original",
            "-disposition:a:0", "default", "-disposition:a:1", "0",
            "-shortest", "-movflags", "+faststart", str(out)]
    run(cmd)


# ---------------------------------------------------------------- recording mix

EFFECTS = {
    "none": "",
    "studio": "aecho=0.8:0.7:28|46:0.14|0.09,",
    "hall": "aecho=0.8:0.85:60|120|190:0.28|0.2|0.14,",
    "echo": "aecho=0.8:0.9:320|640:0.32|0.16,",
}


def mix_recording(music: Path, voice: Path, out: Path, *, song_start: float, semitones: float,
                  latency_ms: float, voice_gain: float = 1.6, music_gain: float = 0.8,
                  effect: str = "studio", partner: Path | None = None, partner_gain: float = 0.9) -> None:
    """Mix the user's voice over the (key-shifted) karaoke track -> MP3.

    voice sample 0 was captured while the song was at `song_start` seconds; the mic chain
    lags by `latency_ms`, so the matching music starts at song_start - latency.
    partner: the duet partner's vocal track (same timeline as the music), mixed in under the voice.
    """
    music_start = max(0.0, song_start - latency_ms / 1000.0)
    ratio = 2 ** (semitones / 12.0)
    pitch_f = ""
    if abs(semitones) > 1e-3:
        # pitch shift without tempo change, plain ffmpeg (no rubberband needed)
        pitch_f = f"asetrate={44100 * ratio:.3f},aresample=44100,{_atempo(1 / ratio)},"
    fx = EFFECTS.get(effect, EFFECTS["studio"])
    inputs = ["-ss", f"{music_start:.3f}", "-i", str(music), "-i", str(voice)]
    if partner is not None:
        inputs += ["-ss", f"{music_start:.3f}", "-i", str(partner)]
        flt = (f"[0:a]aresample=44100,volume={music_gain}[m0];[2:a]aresample=44100,volume={partner_gain}[p];"
               f"[m0][p]amix=inputs=2:duration=first:normalize=0,{pitch_f}anull[m];")
    else:
        flt = f"[0:a]aresample=44100,{pitch_f}volume={music_gain}[m];"
    flt += (f"[1:a]aresample=44100,highpass=f=80,acompressor=threshold=0.1:ratio=3,"
            f"volume={voice_gain},{fx}anull[v];"
            f"[m][v]amix=inputs=2:duration=shortest:normalize=0,alimiter=limit=0.95[out]")
    run([config.FFMPEG, "-y", *inputs, "-filter_complex", flt, "-map", "[out]",
         "-c:a", "libmp3lame", "-q:a", "2", str(out)])


def _atempo(f: float) -> str:
    """ffmpeg atempo accepts 0.5-2.0 per stage; chain stages for anything outside."""
    parts = []
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    parts.append(f"atempo={f:.6f}")
    return ",".join(parts)


# ---------------------------------------------------------------- key-shifted audio for the web player

_HAS_RUBBERBAND: bool | None = None


def has_rubberband() -> bool:
    global _HAS_RUBBERBAND
    if _HAS_RUBBERBAND is None:
        proc = subprocess.run([config.FFMPEG, "-hide_banner", "-filters"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True)
        _HAS_RUBBERBAND = " rubberband " in proc.stdout
    return _HAS_RUBBERBAND


def shifted_audio(src: Path, out: Path, semitones: int, speed: float = 1.0) -> Path:
    """AAC copy of `src` moved by `semitones` and played at `speed` (pitch kept). Cached on disk."""
    if out.exists() and out.stat().st_size > 0:
        return out
    ratio = 2 ** (semitones / 12.0)
    if semitones == 0 and abs(speed - 1) < 1e-3:
        flt = "anull"
    elif has_rubberband():  # best quality
        flt = f"rubberband=pitch={ratio:.6f}:tempo={speed:.4f}:pitchq=quality:formant=preserved"
    elif semitones == 0:
        flt = _atempo(speed)
    else:
        flt = f"asetrate={44100 * ratio:.3f},aresample=44100,{_atempo(speed / ratio)}"
    tmp = out.with_suffix(".tmp.m4a")
    run([config.FFMPEG, "-y", "-i", str(src), "-af", f"aresample=44100,{flt}", "-ac", "2",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)])
    tmp.replace(out)
    return out
