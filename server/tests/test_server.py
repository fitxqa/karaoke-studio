import json
import os
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


from app import jobs, lyrics, media, pitch  # noqa: E402

LRC = """[ar:Test]
[offset:+0]
[00:10.00]வணக்கம் நண்பா
[00:14.50]Hello world again
[00:18.00]
[00:20.00]<00:20.00>one <00:20.50>two <00:21.20>three
"""


def test_parse_lrc():
    lines = lyrics.parse_lrc(LRC, duration=30)
    assert [l["start"] for l in lines] == [10.0, 14.5, 20.0]
    assert lines[0]["end"] == 14.5 and lines[1]["end"] == 18.0  # blank line ends previous
    assert lines[2]["words"][1] == {"start": 20.5, "end": 21.2, "text": "two"}
    assert len(lines[0]["words"]) == 2 and lines[0]["words"][0]["start"] == 10.0


def test_tamil_romanize():
    out = lyrics.finalize(lyrics.parse_lrc(LRC, 30), "manual")
    assert out["language"] == "ta"
    print(out["lines"][0]["roman"])
    assert out["lines"][0]["roman"] == "vanakkam nanbaa"
    assert lyrics.to_lrc(out).startswith("[00:10.00]வணக்கம்")


def test_title_candidates():
    c = lyrics.title_candidates("Arabic Kuthu - Video Song | Beast | Thalapathy Vijay | Anirudh (4K)")
    assert c[0] == "Arabic Kuthu"
    assert "Arabic Kuthu Beast" in c


def _tone(path, hz, secs, sr=44100, stereo=True):
    t = np.arange(int(secs * sr)) / sr
    y = 0.4 * np.sin(2 * np.pi * hz * t)
    sf.write(path, np.stack([y, y], 1) if stereo else y, sr)


def test_pitch_and_mix(tmp_path: Path):
    v = tmp_path / "vocals.wav"
    _tone(v, 220.0, 3)
    ref = pitch.vocal_pitch(str(v))
    voiced = [m for m in ref["midi"] if m is not None]
    assert len(voiced) > 100 and abs(np.median(voiced) - 57) < 0.3  # A3 = midi 57

    music, voice, out = tmp_path / "m.wav", tmp_path / "voice.wav", tmp_path / "out.mp3"
    _tone(music, 330, 10)
    _tone(voice, 440, 4, sr=16000, stereo=False)
    media.mix_recording(music, voice, out, song_start=2.0, semitones=2, latency_ms=150)
    d = media.ffprobe_duration(out)
    assert d and 3.8 < d < 4.3


def test_mux_audio_only(tmp_path: Path):
    music, orig, out = tmp_path / "m.wav", tmp_path / "o.wav", tmp_path / "k.mp4"
    _tone(music, 330, 3)
    _tone(orig, 440, 3)
    media.mux_karaoke(None, music, orig, out)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type:stream_tags", "-of", "json", str(out)],
                           capture_output=True, text=True)
    streams = json.loads(probe.stdout)["streams"]
    assert [s["codec_type"] for s in streams] == ["video", "audio", "audio"]
    assert streams[1]["tags"]["handler_name"] == "Karaoke"


def test_api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(jobs, "submit", lambda job_id: None)
    c = TestClient(app)
    assert c.get("/songs").status_code == 401
    h = {"X-API-Key": "secret"}
    r = c.post("/songs", json={"url": "https://youtu.be/abc", "language": "ta"}, headers=h)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert c.get("/songs", headers=h).json()[0]["id"] == sid
    jobs.update(sid, duration=30)
    r = c.put(f"/songs/{sid}/lyrics", json={"lrc": LRC}, headers=h)
    assert r.status_code == 200 and r.json()["source"] == "manual"
    assert c.get(f"/songs/{sid}/lyrics", headers=h).json()["lines"][0]["text"] == "வணக்கம் நண்பா"
    assert c.get(f"/songs/{sid}/files/meta.json", headers=h).status_code == 404
    assert c.get("/songs/..%2f..%2fetc/lyrics", headers=h).status_code == 404
    assert c.delete(f"/songs/{sid}", headers=h).status_code == 409

    # recordings
    _tone(jobs.job_dir(sid) / "no_vocals.wav", 330, 8)
    _tone(tmp_path / "v.wav", 440, 3, sr=16000, stereo=False)
    with open(tmp_path / "v.wav", "rb") as f:
        r = c.post(f"/songs/{sid}/recordings", headers=h,
                   data={"song_start": "1.0", "semitones": "-3", "score": "77"},
                   files={"voice": ("v.wav", f, "audio/wav")})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert c.get(f"/songs/{sid}/recordings/{rid}.mp3", headers=h).status_code == 200
    assert c.get(f"/songs/{sid}/recordings", headers=h).json()[0]["score"] == 77


def test_web_login_and_key_audio(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    assert c.get("/").status_code == 200 and "Karaoke Studio" in c.get("/").text
    assert c.get("/app.js").status_code == 200
    assert c.post("/login", json={"key": "nope"}).status_code == 401
    assert c.post("/login", json={"key": "secret"}).status_code == 200  # sets cookie
    sid = c.post("/songs", json={"url": "https://youtu.be/x"}).json()["id"]  # cookie auth works
    _tone(jobs.job_dir(sid) / "no_vocals.wav", 330, 3)
    r = c.get(f"/songs/{sid}/audio/music.m4a?key=2")
    assert r.status_code == 200 and len(r.content) > 1000
    assert (jobs.job_dir(sid) / "cache" / "music_k+2.m4a").exists()
    assert c.get(f"/songs/{sid}/audio/other.m4a").status_code == 404
    r = c.get(f"/songs/{sid}/audio/music.m4a?key=0", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def test_edit_lines_keeps_or_respreads_words():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    h = {"X-API-Key": "secret"}
    sid = c.post("/songs", json={"url": "https://youtu.be/y"}, headers=h).json()["id"]
    jobs.update(sid, duration=30)
    lines = [
        {"start": 5.0, "text": "two words", "words": [{"start": 5.0, "end": 5.5, "text": "two"}, {"start": 5.5, "end": 6.0, "text": "words"}]},
        {"start": 1.0, "text": "கண்ணே   கலைமானே", "words": [{"start": 1, "end": 2, "text": "old"}]},
        {"start": 9.0, "text": "   "},
    ]
    r = c.put(f"/songs/{sid}/lyrics/lines", json={"lines": lines}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()["lines"]
    assert [l["start"] for l in out] == [1.0, 5.0]  # sorted, blank removed
    assert out[0]["text"] == "கண்ணே கலைமானே" and out[0]["roman"] == "kannae kalaimaanae"
    assert [w["text"] for w in out[0]["words"]] == ["கண்ணே", "கலைமானே"]  # text changed -> respread
    assert out[1]["words"][1] == {"start": 5.5, "end": 6.0, "text": "words"}  # kept
    assert c.put(f"/songs/{sid}/lyrics/lines", json={"lines": []}, headers=h).status_code == 400


def test_build_ass_karaoke_subtitles():
    from app.share_video import build_ass

    lyr = {"lines": [
        {"start": 2.0, "end": 4.0, "text": "கண்ணே கலைமானே", "roman": "kannae kalaimaanae",
         "words": [{"start": 2.0, "end": 2.6, "text": "கண்ணே"}, {"start": 2.8, "end": 3.6, "text": "கலை{மானே}"}]},
        {"start": 50.0, "end": 52.0, "text": "outside", "words": []},
    ]}
    ass = build_ass(lyr, 1.0, 10.0, title="Song", singer="Muhsin", roman=True)
    assert "Sung by Muhsin" in ass
    assert "Dialogue: 1,0:00:01.00,0:00:03.00,Main" in ass  # shifted by the 1 s start
    assert "{\\kf60}கண்ணே {\\k20}{\\kf80}கலை(மானே)" in ass  # word fill + gap, braces escaped
    assert "Roman" in ass and "Main,,0,0,0,,{\\kf" in ass and ass.count("outside") == 1  # only as the next-line preview


def test_align_pasted_lyrics_keeps_timing_fixes_words():
    from app import lyrics

    ai = {"lines": [
        {"start": 2, "end": 5, "text": "கண்ணே கலைமாணே", "words": [
            {"start": 2, "end": 2.6, "text": "கண்ணே"}, {"start": 2.7, "end": 3.5, "text": "கலைமாணே"}]},
        {"start": 6, "end": 9, "text": "கண்டேன் உண்ணை", "words": [
            {"start": 6, "end": 6.8, "text": "கண்டேன்"}, {"start": 7, "end": 7.6, "text": "உண்ணை"}]},
    ]}
    pasted = "Movie: Test\nMale : கண்ணே கலைமானே\nகண்டேன் உன்னை நான் (x2)\n"
    lines, frac = lyrics.align_text(ai, pasted, 30)
    assert [l["text"] for l in lines] == ["கண்ணே கலைமானே", "கண்டேன் உன்னை நான்"]
    assert lines[0]["start"] == 2.0 and lines[1]["start"] == 6.0
    assert lines[1]["words"][1]["start"] == 7.0  # உன்னை took உண்ணை's time
    assert lines[1]["end"] <= 10.5  # not stretched to the 12 s cap
    assert frac >= 0.75
