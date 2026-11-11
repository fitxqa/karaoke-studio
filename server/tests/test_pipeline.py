import shutil

import numpy as np
import soundfile as sf

from app import jobs, lyrics, media


def test_full_pipeline_with_upload(monkeypatch, tmp_path):
    """Runs the real pipeline on an uploaded audio file, with Demucs/LRCLIB/Whisper stubbed."""
    def fake_separate(wav, out_dir):
        v, m = out_dir / "vocals.wav", out_dir / "no_vocals.wav"
        shutil.copy(wav, v)
        shutil.copy(wav, m)
        return v, m

    monkeypatch.setattr(media, "separate", fake_separate)
    monkeypatch.setattr(lyrics, "search_lrclib", lambda *a, **k: "[00:00.50]ஒரு நாள் போதுமா\n[00:02.00]line two")
    meta = jobs.create(upload_name="test", language="ta")
    d = jobs.job_dir(meta["id"])
    t = np.arange(44100 * 4) / 44100
    sf.write(d / "source.wav", 0.3 * np.sin(2 * np.pi * 220 * t), 44100)
    jobs.update(meta["id"], source_file="source.wav")
    jobs._build(meta["id"])
    m = jobs.load(meta["id"])
    assert m["status"] == "done", m.get("error")
    assert m["lyrics_source"] == "lrclib" and m["lyrics_language"] == "ta"
    assert (d / "karaoke.mp4").exists() and (d / "pitch.json").exists()
    assert not (d / "audio.wav").exists()
