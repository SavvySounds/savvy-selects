# CLAUDE.md

Context for Claude Code working in this repo.

## What this is

A local pipeline that turns a ~2 TB event-footage archive into a small set of
ranked, trimmed clips for marketing and paid ads. Owner is Miles DiPaola, founder
of Savvy Sounds Collective, a DJ and event-production company in Los Angeles. The
immediate goal is a corporate capabilities reel plus ad creative aimed at event
producers and brand agencies.

Everything runs on his Mac. Footage never leaves the machine except single
downsampled JPEG contact strips sent to the Anthropic API during `score`.

## Architecture

Four sequential stages plus a review deck. Each stage is resumable and writes its
state to one SQLite database, so any stage can be killed and restarted.

```
sources (Dropbox, external drives)
  │
  ├─ proxy   index files, build 720p proxies          local, free
  ├─ scan    shot detection + sharpness/exposure gate local, free
  ├─ score   Claude vision grades contact strips      PAID
  ├─ review  local web deck, human rates and tags     local, free
  └─ export  cut winners from ORIGINALS               local, free
```

```
savvy/
  config.py          config.json loading, DEFAULTS
  db.py              schema, migrations, state vocabularies
  storage.py         on-disk vs cloud placeholder, free space, fetch
  beatgrid.py        BPM/beat detection and the beat grid
  edl.py             CMX3600 EDL writer
  media.py           every ffmpeg/PIL call in the codebase
  rubric.py          RUBRIC prompt — this file is the product's taste
  stages/            proxy, scan, score, export, assemble
  review/
    server.py        stdlib HTTP server, JSON + byte-range media API
    index.html       the review deck UI
    calibrate.py     human vs grader disagreement report
  cli.py             argparse entry point → `savvy <stage>`
tests/               pytest, synthetic footage, no API key needed
```

## Invariants — do not break these

1. **Analysis reads proxies. Export reads originals.** Timecodes are computed on
   720p proxies and applied to full-res sources at cut time. This is the whole
   reason a 2 TB archive is tractable. Never point export at a proxy.
2. **`scan` gates `score`.** The local sharpness and exposure filter exists to
   kill most of the archive before anything costs money. Any change that sends
   more clips to the API needs to justify the spend.
3. **Sources are read-only.** The pipeline never writes to, renames, or deletes
   anything under a configured source path. All output goes to `work_dir`.
4. **`.insv` / `.insp` files are parked, never transcoded.** 360 source is not
   usable video until a human reframes it in Insta360 Studio. They get
   `state='needs_reframe'` and are skipped.
4b. **Online-only files are parked, never opened.** The archive lives in Dropbox
   and most of it is not on disk: as measured, 1747 of 1811 files, 770 GB against
   47 GB free. Opening a placeholder is what makes Dropbox download it, so
   `proxy` and `export` check `storage.is_cloud_only_path` first and skip. Note
   `Path.exists()` is TRUE for a placeholder and is not a substitute for this
   check. Downloads happen only via `savvy fetch`, which is budgeted against free
   space. Everything in `storage.py` works off `stat()` except `materialize`.
5. **Byte-range support in `review/server.py` is load-bearing.** Without HTTP 206
   responses the browser cannot seek and the filmstrip scrubber breaks. There is
   a test for this; keep it passing.
6. **Human ratings outrank grader scores.** `export --use-ratings` and
   `--crate` reflect what Miles picked. Filenames rank by his rating when present.
7. **All ffmpeg calls live in `media.py`.** Stages should not shell out directly.
8. **VideoToolbox is attempted first, libx264 is the fallback.** Apple Silicon
   gets hardware encode; CI and Linux quietly fall back. Never assume either.
9. **EDL timecodes always reference original sources, never proxies or
   exported cuts.** `assemble` reads shot ranges off proxies but writes in/out
   points on the ORIGINAL files at each original's own frame rate, so the EDL
   relinks cleanly in Premiere/Resolve/Avid.

## Commands

```bash
make setup             # venv + editable install + config.json from example
make test              # pytest, ~60s, builds synthetic footage with ffmpeg

savvy check            # what is on disk vs still in the cloud, read-only
savvy fetch --event "DELTA REEL" [--max-gb N] [--dry-run]
savvy proxy            # overnight on the full archive
savvy scan             # 1-2 hours
savvy score --limit 200
savvy review           # localhost:8420
savvy calibrate        # after 20+ human ratings
savvy export --use-ratings --min-rating 4
savvy export --crate
savvy assemble --track "/path/to/track.mp3"  # beat-synced rough cut EDL
savvy status
```

`SAVVY_CONFIG=/path/to/other.json` overrides which config file is loaded, which
is how the tests and smoke runs stay isolated.

## Conventions

- Python 3.11+, standard library first. Current third-party deps are `anthropic`,
  `numpy`, `pillow`, `mutagen` — adding another needs a reason. No OpenCV: the
  sharpness metric is a hand-rolled Laplacian in numpy specifically to avoid
  that install.
- The review UI is one HTML file with inline CSS and vanilla JS. No build step,
  no framework, no npm. Keep it that way.
- Comments explain *why*, not *what*. Skip the ones that restate the code.
- Every stage prints progress as it goes. Long silent runs feel broken.
- Tests use synthetic ffmpeg-generated footage and never require an API key or
  network access.

## Tuning the grader

`savvy/rubric.py` is where taste lives. The loop is: review clips, run
`savvy calibrate`, read which tags the grader over- and under-rates, edit the
rubric, re-run `score`. Do not tune it by guessing — use the calibrate output.

## Backlog

- [x] `savvy assemble` — beat-synced rough cut from the reel crate. Miles is a DJ
      and will supply the track; detect BPM, cut on the downbeat, output an EDL or
      a Premiere/Resolve-compatible XML rather than a baked render.
- [ ] Batch the `score` stage — several contact strips per request to cut cost.
- [ ] Parallelise `proxy` across cores. It is the longest wall-clock stage and is
      currently strictly sequential.
- [ ] Face/logo detection pass so brand signage (Delta, Sony, New Balance,
      Marvell, Yelp, Grammy Museum) can be tagged and filtered explicitly.
- [ ] Duplicate detection via perceptual hash — the archive has the same event
      dumped under multiple folder names.
- [ ] `savvy stills` — pull the best single frames as high-res JPEGs for the
      website and one-pager, not just video.
- [ ] Resume-safe `score` that survives API rate limits with proper backoff
      instead of the current flat 2-second sleep.
