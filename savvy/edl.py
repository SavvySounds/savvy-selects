"""CMX3600 EDL writer for `savvy assemble`.

An EDL is just formatted text, so this needs zero new dependencies and every
NLE reads it. Timecodes are frame-based (HH:MM:SS:FF): seconds are converted
at a frame rate. Record (timeline) timecodes use the sequence fps; source
timecodes use each original's own fps when the caller provides it, which keeps
in/out points on exact frame boundaries of the original.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Event:
    reel: str             # source label, <=8 chars per CMX3600 convention
    source_path: Path     # the ORIGINAL file - never a proxy or an export
    src_in: float         # seconds, on the original
    src_out: float
    rec_in: float         # seconds, on the timeline being built
    rec_out: float
    src_fps: float = 0.0  # the original's own rate; 0 = use the sequence fps


def _tc(seconds, fps) -> str:
    """Seconds -> HH:MM:SS:FF, rounded to the nearest frame."""
    rate = round(fps)
    frames = round(seconds * fps)
    ff = frames % rate
    total = frames // rate
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def write_cmx3600(events, fps, out_path) -> None:
    """One V event per clip, in order, plus the relink comment per event."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"TITLE: {out_path.stem.upper()}", "FCM: NON-DROP FRAME", ""]
    for i, e in enumerate(events, 1):
        src_rate = e.src_fps if e.src_fps else fps
        lines.append(
            f"{i:03d}  {e.reel:<8}  V     C        "
            f"{_tc(e.src_in, src_rate)} {_tc(e.src_out, src_rate)} "
            f"{_tc(e.rec_in, fps)} {_tc(e.rec_out, fps)}")
        # Reels are truncated to 8 chars, so NLEs relink via this comment.
        lines.append(f"* FROM CLIP NAME: {e.source_path.name}")
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n")
