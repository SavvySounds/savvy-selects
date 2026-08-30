"""Which files are really on this disk, and which are cloud placeholders.

Dropbox and iCloud leave "online-only" files behind: the name and size are on
disk, the bytes are not. macOS calls these dataless. Reading one is what makes
the provider download it, so everything here works off stat() alone. Nothing in
this module opens a file except `materialize`, whose entire job is to.

Kept separate from media.py on purpose: no ffmpeg, no images, just the
filesystem.
"""
import os
import shutil
from pathlib import Path

from . import media

# macOS sets this on File Provider placeholders whose contents are not local.
SF_DATALESS = 0x40000000

MB = 1 << 20
GB = 1 << 30


def is_cloud_only(st) -> bool:
    """True if the bytes live in the cloud rather than on this disk.

    Two independent signals, because either can be missing depending on how the
    provider implemented itself: no allocated blocks behind a non-zero size, and
    the dataless flag. An empty file has no blocks either, hence the size check.
    """
    if getattr(st, "st_flags", 0) & SF_DATALESS:
        return True
    return st.st_size > 0 and st.st_blocks == 0


def is_cloud_only_path(path) -> bool:
    try:
        return is_cloud_only(Path(path).lstat())
    except OSError:
        return False


def probe(path):
    """(size_in_bytes, cloud_only) without reading a single byte.

    Goes through is_cloud_only_path so there is exactly one place in the
    codebase that decides whether a file is really here.
    """
    p = Path(path)
    return p.lstat().st_size, is_cloud_only_path(p)


def free_bytes(path) -> int:
    return shutil.disk_usage(Path(path)).free


def survey(sources, exts):
    """Per-event tally of what is local and what is still in the cloud."""
    sources = [Path(s).expanduser() for s in sources]
    exts = {e.lower() for e in exts}
    events = {}

    for root in sources:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in exts:
                continue
            try:
                size, cloudy = probe(p)
            except OSError:
                continue
            name = media.event_name_from(p, sources)
            e = events.setdefault(name, {"event": name, "local_files": 0,
                                         "local_bytes": 0, "cloud_files": 0,
                                         "cloud_bytes": 0})
            if cloudy:
                e["cloud_files"] += 1
                e["cloud_bytes"] += size
            else:
                e["local_files"] += 1
                e["local_bytes"] += size

    return sorted(events.values(), key=lambda e: -e["cloud_bytes"])


def materialize(path, chunk=8 * MB) -> int:
    """Pull a placeholder down by reading it. Returns bytes read.

    There is no supported API to ask Dropbox for a file; reading it is the
    documented behaviour and what Finder does when you open one.
    """
    n = 0
    with open(path, "rb") as f:
        while buf := f.read(chunk):
            n += len(buf)
    return n


def human(n) -> str:
    if n >= GB:
        return f"{n / GB:.1f} GB"
    if n >= MB:
        return f"{n / MB:.0f} MB"
    return f"{n} B"
