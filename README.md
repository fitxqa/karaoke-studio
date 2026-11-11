# Karaoke Studio (web app)

Karaoke Studio turns a YouTube link (or an MP3/MP4 file) into a karaoke video, with lyrics that fill in word by word. Everything runs in your browser. You only need a small Python server to do the heavy work.

**What it does**
- **Vocal removal:** Demucs AI takes the singer out. The *Original vocals* switch plays the singer quietly when you're learning a song.
- **Lyrics:** it first looks for synced lyrics on LRCLIB. If none are found, Whisper AI writes timed lyrics from the singer's voice.
- **Tamil lyrics:** shown in Tamil script, in Tanglish (*kannae kalaimaanae*), or both.
- **Edit lyrics:** paste LRC text, or paste plain lyrics and press Space as each line starts to time them.
- **Key change** from −6 to +6 semitones while the song plays. The setting is saved per song.
- **Sing & record:** a melody guide shows the singer's notes and your voice. You get a live score from 0 to 100 and a grade (S/A/B/C/D).
- **Recordings:** your voice is mixed with the music in your chosen key and saved as an MP3. You can play or download it.

```
Browser (Chrome / Edge)               Server (Python FastAPI)
 video + lyrics + melody guide  ──>    yt-dlp download → Demucs vocal removal
 Web Audio player, key change          LRCLIB / Whisper lyrics, Tamil → Tanglish
 mic, live pitch scoring               key-shifted audio, recording mix (ffmpeg)
```

## Run it on your Windows PC
1. Install **Python 3.11** from python.org. During setup, tick "Add to PATH".
2. Install **ffmpeg**: `winget install Gyan.FFmpeg`
3. Double-click `server\setup_windows.bat`. You only do this once, and it takes about 10 minutes.
   - If you have an NVIDIA graphics card, edit the torch line in that file first (the comment explains how). Vocal removal then takes about 20 seconds instead of about 3 minutes.
4. Double-click `server\start_server.bat`. It opens **http://localhost:8000** in your browser.

The first song you add downloads the AI models (about 1.5 GB). They're cached after that.

## Run it on a server (open from any device)
```bash
cd server
cp .env.example .env              # set KARAOKE_API_KEY to a long secret, and DOMAIN
docker compose --profile https up -d --build
```
Then open `https://your-domain` and enter the key once.
- **Why HTTPS:** browsers only allow the microphone on `https://` or on `localhost`.
- **Server size:** use 8 GB of RAM or more. A GPU makes it much faster.

**Settings** (in `.env`)

| Variable | Default | Notes |
|---|---|---|
| `KARAOKE_API_KEY` | empty | Required when the server is on the internet |
| `DEMUCS_MODEL` | `htdemucs` | `htdemucs_ft` is cleaner but about 4× slower |
| `WHISPER_MODEL` | `medium` | `large-v3` is best for Tamil (needs a GPU) |
| `MAX_VIDEO_HEIGHT` | `720` | Download quality |

## Using it
- **Keys:** **Space** play/pause · **← →** seek 5 s · **↑ ↓** change key · **Esc** back to the library.
- **Changing key:** the first time you use a new key on a song, it takes a few seconds to prepare. After that it's instant.
- **Lyrics early or late?** Use the **Lyrics timing** « » buttons. The setting is saved per song.
- **Recording out of sync?** Raise **Mic delay** in ⚙ Settings. Bluetooth headsets need 200–350 ms.
- **Use headphones when you sing.** Otherwise the mic picks up the music and your score drops.
- **Tamil film songs** are often missing from LRCLIB. You can:
  - pick **Tamil** when you add the song, so the AI writes the lyrics in Tamil,
  - or paste the lyrics yourself and use **Tap along**.
- **Best browsers:** Chrome or Edge on a computer.

## Development
```bash
cd server
pip install -r requirements.txt
uvicorn app.main:app --reload       # web app files live in server/app/web/
pytest -q tests
```

## Notes
This is for personal use. Downloading from YouTube is against YouTube's terms, so don't share the downloaded songs.
