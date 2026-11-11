"""Server settings, read from environment variables (or a .env file)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # optional
    pass

DATA_DIR = Path(os.getenv("KARAOKE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
JOBS_DIR = DATA_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# Shared secret the desktop app sends in the X-API-Key header. Empty = no auth (local use only).
API_KEY = os.getenv("KARAOKE_API_KEY", "")

# Demucs model: htdemucs (good, fast) or htdemucs_ft (best, ~4x slower)
DEMUCS_MODEL = os.getenv("DEMUCS_MODEL", "htdemucs")

# faster-whisper model used when no synced lyrics are found online.
# small / medium / large-v3. large-v3 gives the best Tamil results but needs a GPU to be quick.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto / cpu / cuda

# Highest video resolution to download.
MAX_VIDEO_HEIGHT = int(os.getenv("MAX_VIDEO_HEIGHT", "1080"))

# Parallel workers for vocal removal (more = faster on multi-core CPUs, uses more memory).
DEMUCS_JOBS = int(os.getenv("DEMUCS_JOBS", str(max(1, min(4, (os.cpu_count() or 2) // 2)))))

# Whisper speed: int8 on CPU is ~2-3x faster with almost the same accuracy.
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "auto")

FFMPEG = os.getenv("FFMPEG", "ffmpeg")
