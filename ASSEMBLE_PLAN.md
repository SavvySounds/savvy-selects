# `savvy assemble` — implementation plan

Written by Claude (Plan role) while Miles was away from his machine, for DeepSeek
V4 Flash (Act role, in Cline) to build. Grounded in the actual repo — schema,
stage pattern, and invariants below are read directly from `savvy/db.py`,
`savvy/stages/export.py`, `savvy/cli.py`, and `savvy/media.py`, not guessed.

## Before touching anything

There are uncommitted changes already sitting in this working tree (modified
`cli.py`, `db.py`, `export.py`, `proxy.py`, plus untracked `fetch.py`,
`storage.py`, `test_cloud.py` — looks like the cloud-fetch feature). Do not mix
that work with this feature. Either commit it as its own change first, or ask
Miles which state to build from. Don't silently build assemble on top of an
unreviewed diff.

## Goal

From `CLAUDE.md`'s backlog: *"`savvy assemble` — beat-synced rough cut from the
reel crate. Miles is a DJ and will supply the track; detect BPM, cut on the
downbeat, output an EDL or a Premiere/Resolve-compatible XML rather than a
baked render."*

Output target: **CMX3600 EDL** (plain text, industry-standard, imports into
Premiere/Resolve/Avid). Picked over FCPXML because it needs zero new
dependencies to write (it's just formatted text) and every target NLE reads
it. No baked render — the EDL references the ORIGINAL source files with
in/out timecodes, per invariant #1 in CLAUDE.md.

## New dependency

`mutagen>=1.47` — pure-Python, no C extension, reads ID3/Vorbis tags. Reason:
DJ tracks are almost always pre-analyzed by Mixed In Key / rekordbox / Serato,
which write an accurate BPM into the file's tags already. Reading that tag is
free and more accurate than any audio analysis we'd write ourselves. This is
the "fourth dependency needs a reason" bar in CLAUDE.md — the reason is: skip
unreliable signal-processing guesswork for the common case where the number
is already sitting in the file.

Add to `pyproject.toml` → `dependencies`.

## BPM / beat-grid detection — priority order

1. **Tag on the track file** (`mutagen`, `TBPM` frame / Vorbis `BPM` comment).
   Accurate, free, instant. This is the expected path for Miles's library.
2. **`--bpm` CLI override.** He may know it or want to try a different tempo
   than what's tagged.
3. **Fallback estimate** if no tag and no override: onset-envelope
   autocorrelation over the track's low-sample-rate mono waveform, done in
   numpy (already a dependency — no aubio/librosa). Print the estimate
   clearly labeled `(estimated)` in the CLI output so Miles knows to sanity
   check it, and tell him to re-run with `--bpm` if it's wrong. Do not treat
   an estimate silently as ground truth.

**First-beat offset** (where beat 1 actually lands, not just the tempo):
same onset-detection pass, find the first strong transient in the first ~8
seconds, snap to the nearest `60/bpm` interval. Add `--offset SECONDS` as a
manual override — DJ software usually shows this number directly and it's
worth letting Miles just paste it in rather than trust an estimate.

## New files

### `savvy/beatgrid.py`
```python
def detect_bpm(track_path) -> tuple[float, str]:
    """Returns (bpm, source) where source is 'tag', 'override', or 'estimated'."""

def detect_offset(track_path, bpm) -> tuple[float, str]:
    """Returns (offset_seconds, source) — 'override' or 'estimated'."""

def beat_grid(bpm, offset, duration, beats_per_bar=4) -> list[float]:
    """Every beat timestamp in the track, offset -> duration."""
```
Calls into `media.audio_samples()` for the estimate path — it does NOT shell
out to ffmpeg directly (invariant #7: all ffmpeg calls live in `media.py`).

### `savvy/edl.py`
```python
@dataclass
class Event:
    reel: str        # source filename, no extension, <=8 chars per CMX3600 convention (truncate/hash if needed)
    source_path: Path
    src_in: float     # seconds, on the ORIGINAL
    src_out: float
    rec_in: float     # seconds, on the timeline being built
    rec_out: float

def write_cmx3600(events: list[Event], fps: float, out_path: Path) -> None:
    """Writes a CMX3600 EDL. Timecodes are frame-based (HH:MM:SS:FF), so seconds
    need converting at the given fps. One V (video) event per clip, in reel order."""
```
Pure string formatting + the existing `csv`/stdlib patterns already used in
`export.py`. No new dependency.

### `savvy/stages/assemble.py`
```python
def run(cfg, con, args):
    ...
```
Orchestrator, same shape as `export.run(cfg, con, args)`. Steps:

1. Reuse the crate query from `export._select()` for `flagged=1` (import it,
   don't duplicate the SQL — `from .export import _select`, called with an
   `argparse.Namespace(crate=True, ...)`-shaped stand-in, or refactor `_select`
   to accept just the ordering mode instead of the full `args` object if that's
   cleaner. Use judgment, but do not fork the query into a second copy that
   can drift from `export.py`'s.).
2. Skip any clip whose source is offline (`storage.is_cloud_only_path`) or
   missing on disk — same guard `export.run()` already does, reuse the
   pattern.
3. Compute `bpm`, `offset` via `beatgrid.py`. Print them clearly, labeled
   with their source (tag / override / estimated).
4. `probe_duration(args.track)` via `media.py` (already exists) for the
   track length. Build the beat grid to that duration.
5. Segment length = `args.bars * beats_per_bar` beats → seconds via bpm.
   Walk the beat grid in fixed segments. For each segment, pop the next
   unused clip off the ordered crate list (already ordered
   `my_rating DESC, score DESC` by the reused query) whose available
   duration (`end - start`) covers the segment length. Trim to
   `start .. start + segment_duration` — always take from the head of the
   flagged range, keep the MVP simple. Skip (and log) any clip shorter than
   one segment rather than trying to be clever about partial fills.
6. If clips run out before the track ends: stop the EDL there. Do NOT loop
   or repeat clips. Print how many seconds of track are uncovered and
   suggest flagging more clips or lowering `--bars`.
7. Need per-source frame rate for accurate timecodes — add
   `media.probe_fps(path)` (new, ffprobe-based, same pattern as
   `probe_duration`). Sequence/record frame rate is `args.fps` (a single
   project rate the EDL is conformed to).
8. Write the EDL via `edl.write_cmx3600()` to
   `work_dir/SELECTS/assemble_<track-stem>.edl` unless `--out` is given.

## `media.py` additions

```python
def probe_fps(path) -> float:
    """ffprobe r_frame_rate, parsed from the 'num/den' string it returns."""

def audio_samples(path, sr=11025) -> np.ndarray:
    """Decode to mono PCM at a low sample rate via ffmpeg, piped to stdout,
    returned as a numpy array. Low sample rate is plenty for tempo/onset
    estimation and keeps this cheap."""
```
Both are ffmpeg/ffprobe calls — belong in `media.py` per invariant #7, not in
`beatgrid.py`.

## CLI wiring (`cli.py`)

```python
a = sub.add_parser("assemble", help="beat-synced rough cut EDL from the reel crate")
a.add_argument("--track", required=True, help="path to the DJ track to cut to")
a.add_argument("--bpm", type=float, help="override detected BPM")
a.add_argument("--offset", type=float, help="override detected first-beat time, seconds")
a.add_argument("--bars", type=int, default=2, help="clip length in bars (4 beats/bar)")
a.add_argument("--fps", type=float, default=29.97, help="EDL sequence frame rate")
a.add_argument("--out", help="output .edl path (default: work_dir/SELECTS/)")
```
Import `assemble` alongside the other stages, add `"assemble": assemble.run` to
`COMMANDS`.

## Tests — `tests/test_assemble.py`

Follow `tests/test_pipeline.py`'s pattern exactly: synthetic ffmpeg-generated
fixtures, no API key, no network.

- Build a synthetic click track with `ffmpeg -f lavfi -i "sine=frequency=1000:duration=X"`
  gated into periodic beeps at a known BPM (e.g. 120) — `aevalsrc` or a
  volume-gate filter works for this.
- Assert `beatgrid.detect_bpm()` on it lands within a few BPM of 120 (fallback
  path — no tag on a synthetic file, so this exercises the estimator).
- Tag a copy with `mutagen` at a specific BPM and assert the tag path is
  preferred and exact.
- Build 3-4 fake flagged clips in a test db (same fixture style as
  `test_pipeline.py`'s footage fixture) and assert the EDL event count and
  timecodes match hand-calculated expected values for a known bpm/offset/bars
  combination.

## Docs to update once this lands

- `CLAUDE.md`: check off the backlog line, add `savvy assemble` to the
  Commands block and the `savvy/` architecture tree, and add a line to
  Invariants if warranted (e.g. "EDL timecodes always reference original
  sources, never proxies or exported cuts" — reinforcing invariant #1 in the
  new context).
- `README.md`: add `savvy assemble` to the `## Run` command list and
  `## The stages` section alongside the existing five.

## Open questions for Miles (don't guess silently — ask, or make the most
reasonable call and flag it clearly in the PR/commit message)

- Segment length default of 2 bars (8 beats) — reasonable starting point for
  a rough cut, but he may want this tighter or looser once he sees a real
  cut.
- CMX3600 `reel` names are conventionally ≤8 characters — need a stable,
  readable truncation/hash scheme for source filenames that can collide.
