"""Bring songs made by older Karaoke Studio versions into the current library.

Older builds kept songs next to the program (…\\server\\data\\jobs) in several
places; 2.0 keeps them in %LOCALAPPDATA%\\KaraokeStudio\\data.  On start we look
in all the old places and copy (never move or delete) any song that is missing.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from .config import JOBS_DIR

log = logging.getLogger("karaoke.migrate")


def _candidates() -> list[Path]:
    home = Path.home()
    here = Path(__file__).resolve().parent.parent  # …\server
    roots = [
        here / "data",
        home / "karaoke-studio" / "server" / "data",
        home / "ksb" / "KaraokeStudio" / "server" / "data",
        home / "Downloads" / "karaoke-studio" / "server" / "data",
        home / "Desktop" / "karaoke-studio" / "server" / "data",
        home / "Documents" / "karaoke-studio" / "server" / "data",
    ]
    for base in (home / "Downloads", home / "Desktop", home / "Documents"):
        try:
            for d in base.iterdir():
                if d.is_dir() and "karaoke" in d.name.lower():
                    roots += [d / "server" / "data", d / "karaoke-studio" / "server" / "data",
                              d / "KaraokeStudio" / "server" / "data"]
        except OSError:
            pass
    local = os.getenv("LOCALAPPDATA")
    if local:
        roots += [Path(local) / "Programs" / "KaraokeStudio" / "server" / "data",
                  Path(local) / "KaraokeStudio" / "data"]
    out, seen = [], set()
    for r in roots:
        j = r / "jobs"
        try:
            key = j.resolve()
        except OSError:
            continue
        if key in seen or key == JOBS_DIR.resolve():
            continue
        seen.add(key)
        if j.is_dir():
            out.append(j)
    return out


def import_old_songs() -> int:
    copied = 0
    for jobs in _candidates():
        for src in jobs.iterdir():
            dst = JOBS_DIR / src.name
            if not src.is_dir() or not (src / "meta.json").exists() or dst.exists():
                continue
            try:
                shutil.copytree(src, dst)
                copied += 1
                log.info("imported old song %s from %s", src.name, jobs)
            except OSError as e:
                log.warning("could not import %s: %s", src, e)
                shutil.rmtree(dst, ignore_errors=True)
    return copied
