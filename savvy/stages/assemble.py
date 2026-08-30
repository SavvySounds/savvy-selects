"""Stage 6: beat-synced rough cut EDL from the reel crate.

Miles is a DJ and supplies the track. `assemble` detects its BPM (tag, --bpm,
or estimate), walks the beat grid in fixed segments, and hands each segment
the next best unused crate clip. Output is a CMX3600 EDL referencing the
ORIGINAL source files - no baked render - so the edit relinks in
Premiere/Resolve/Avid instead of shipping pixels.
"""
import argparse
import hashlib
from pathlib import Path

from .. import beatgrid, config, edl, media, storage
from .export import _select

BEATS_PER_BAR = 4


def _reel_name(path, used):
    """Stable, readable CMX3600 reel name (<=8 chars) that never collides.

    Prefer the head of the sanitised filename - it is what Miles would
    recognise in an EDL. Only when two different sources would land on the
    same 8 chars do we fold a short path hash in, so a clash can never
    silently misreference a clip.
    """
    stem = "".join(c if c.isalnum() else "_" for c in path.stem.upper()) or "CLIP"
    base = stem[:8]
    if base not in used:
        used.add(base)
        return base
    h = hashlib.sha1(str(path).encode()).hexdigest()
    for cut in (4, 6, 8):
        candidate = (base[:8 - cut] + h[:cut])[:8]
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError("reel namespace exhausted for this cut")


def run(cfg, con, args):
    # 1. The same crate query export uses for --crate. Imported, never forked.
    sql, params, banner = _select(con, argparse.Namespace(crate=True))
    print(banner)
    rows = con.execute(sql, params).fetchall()

    # 2. Same guard export.run() applies: a placeholder passes exists().
    usable = []
    for r in rows:
        src = Path(r["src_path"])
        if not src.exists():
            print(f"  ! source offline, skipping: {src.name}")
            continue
        if storage.is_cloud_only_path(src):
            print(f"  ! original is online-only, skipping: {src.name}")
            continue
        usable.append(r)
    print(f"{len(usable)} of {len(rows)} flagged clips are on disk.\n")

    # 3. Tempo, labelled so an estimate is never mistaken for ground truth.
    bpm, bpm_src = beatgrid.detect_bpm(args.track, args.bpm)
    offset, off_src = beatgrid.detect_offset(args.track, bpm, args.offset)
    print(f"BPM    {bpm:7.2f}   ({bpm_src})")
    print(f"offset {offset:7.3f}s  ({off_src})")
    if bpm_src == "estimated":
        print("  (estimated - sanity check it. Re-run with --bpm if it's wrong.)")

    # 4. The track, then the beat grid on it.
    duration = media.probe_duration(args.track)
    if duration <= 0:
        raise SystemExit(f"Could not read a duration from {args.track}. "
                         "Is it a playable audio file?")
    print(f"\nTrack: {Path(args.track).name}  ({duration:.1f}s)")
    grid = beatgrid.beat_grid(bpm, offset, duration)

    # 5. Walk the grid in fixed segments of args.bars bars.
    seg_beats = args.bars * BEATS_PER_BAR
    seg_dur = seg_beats * (60.0 / bpm)
    seg_starts = [t for t in grid[::seg_beats] if t >= 0]
    print(f"Segments: {len(seg_starts)} x {seg_dur:.2f}s "
          f"({args.bars} bars = {seg_beats} beats)")

    events = []
    used_ids, used_reels = set(), set()
    rec_in = 0.0
    stop = None  # why the walk stopped: "track" or "clips"
    for start in seg_starts:
        if start + seg_dur > duration + 1e-6:
            stop = "track"
            break  # a partial final segment is worse than none
        row = next((r for r in usable
                    if r["id"] not in used_ids
                    and (r["end"] - r["start"]) >= seg_dur - 1e-6), None)
        if row is None:
            stop = "clips"  # 6. out of clips; stop. Never loop or reuse.
            break
        used_ids.add(row["id"])
        src = Path(row["src_path"])
        events.append(edl.Event(
            reel=_reel_name(src, used_reels),
            source_path=src,
            src_in=row["start"],
            src_out=row["start"] + seg_dur,
            rec_in=rec_in,
            rec_out=rec_in + seg_dur,
            src_fps=media.probe_fps(src),  # 7. per-source frame rate
        ))
        rec_in += seg_dur
        print(f"  [{len(events)}] {src.name}  {row['start']:.2f}-"
              f"{row['start'] + seg_dur:.2f}s  ->  {rec_in - seg_dur:.2f}-"
              f"{rec_in:.2f}s")

    # 6 (continued). Say plainly what was left uncovered.
    if rec_in < duration:
        uncovered = duration - rec_in
        if stop == "clips":
            print(f"\nClips ran out after {rec_in:.1f}s of {duration:.1f}s "
                  f"({uncovered:.1f}s uncovered).")
            print("Flag more clips in review, or re-run with --bars "
                  f"{max(1, args.bars - 1)} to shorten each segment.")
        else:
            print(f"\nStopped at the last full segment: {rec_in:.1f}s of "
                  f"{duration:.1f}s ({uncovered:.1f}s of the track left "
                  "over). Lower --bars to use more of it.")
    if not events:
        raise SystemExit("No clip covered a full segment - nothing to write.")

    # 8. CMX3600 EDL.
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = (config.work_dir(cfg) / "SELECTS"
                    / f"assemble_{Path(args.track).stem}.edl")
    edl.write_cmx3600(events, args.fps, out_path)
    print(f"\nWrote {len(events)} events -> {out_path}")
