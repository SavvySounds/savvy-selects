# VERIFY.md — how to prove Savvy Selects still works

This is the proof recipe. One section per thing the tool does: what it is, how
to run it, and the exact steps that show it working. When you change something,
re-run the sections it touches. If a section can't pass, the tool is broken
even if nothing looks wrong on screen.

Written to be followed by hand, in order, without reading the code.

---

## How to run it

```bash
cd ~/savvy-selects
make setup          # first time only: builds the venv, copies config.example.json
```

**The full proof, in one go:**

```bash
cd ~/savvy-selects && make test && .venv/bin/savvy check && .venv/bin/savvy status
```

`make test` should end in `passed` and take about twenty seconds. It builds its
own fake footage with ffmpeg — it never opens your real files and never needs an
API key or the internet. `savvy check` and `savvy status` read only.

Last run of the full proof: **42 passed in 18.7s** (2026-08-27).

If `make test` fails, stop. Nothing below is trustworthy until it's green
again.

---

## Safety facts — read this before running anything

**Your footage is never written to.** No part of this tool moves, renames,
deletes, or edits anything inside a folder listed under `sources` in
`config.json`. Export, the older proxy stage, and explicitly requested local preview preparation read available originals. Fetch can deliberately download cloud media. The new preview library checks availability first and never downloads cloud-only originals; its archive import reads saved lists only.

**Most of your archive is not on this Mac.** As measured on 2026-08-27:

| | |
|---|---|
| Files in `Content by Event` | 1,811 |
| Actually on the Mac | **1** (2.0 GB) |
| Still on Dropbox's servers | **1,810** (788.3 GB) |
| Free space on the Mac | **27.8 GB** |

Those Dropbox files show up in Finder at full size but the video isn't here.
**Opening one is what makes it download.** That's the whole danger: a careless
run would start pulling 788 GB onto a drive with 28 GB free. So the tool checks
whether a file is really here before touching it, and parks it if it isn't.

**What "dry run" means here.** `savvy fetch --dry-run` prints the list of files
it *would* bring down and then stops. Nothing is read, nothing is downloaded,
your free space doesn't move. Proven below.

> **Known wart:** the dry run prints `Downloading 44 MB. Free space 27.8 GB.`
> *before* it prints `Dry run. Nothing downloaded.` It reads like it's about to
> download. It isn't — that line is a size estimate. Left alone deliberately;
> it's a wording problem, not a behaviour problem.

**What does get written:** everything the tool makes goes into `work_dir`
(`~/SavvySelects`) — the proxies, the filmstrips, the database, the exported
clips. That folder is fair game. Your source folders are not.

**Testing without touching your real setup:** point `SAVVY_CONFIG` at a
different config file and the tool uses that instead, including a different
`work_dir`. Every real-footage step below was run that way, so Miles's own
database at `~/SavvySelects/selects.db` was never opened.

```bash
SAVVY_CONFIG=/tmp/scratch-config.json .venv/bin/savvy check
```

---

## `make test` — the whole tool against fake footage

**What it is.** Builds small test videos with ffmpeg and runs the pipeline end
to end on them. Free, offline, about twenty seconds.

**How to run it.**

```bash
cd ~/savvy-selects && make test
```

**Proving it works.**

1. Run it. It ends `42 passed`.
2. Prove the tests are actually reading this code and not something stale.
   Open `savvy/media.py`, find the line `return float(lap.var())` at the end of
   `laplacian_variance`, and change it to `return 9999.0`.
3. Run `make test` again. It must FAIL, and the failures must name
   `test_laplacian_separates_sharp_from_blurred` and
   `test_scan_rejects_black_and_blurred_shots`.
4. Put the line back and confirm `42 passed` again.

Seen red 2026-08-27: `2 failed, 37 passed`. That's what "the tests are wired to
the real code" looks like. A suite that stays green through step 2 is reading
something stale and is worthless.

---

## `savvy check` — what's on the Mac and what's still on Dropbox

**What it is.** A read-only tally, event by event, of how much footage is
actually on this machine versus still sitting on Dropbox's servers. It never
opens a file, so running it can't start a download.

**How to run it.**

```bash
cd ~/savvy-selects && .venv/bin/savvy check
```

**Proving it works.**

1. Run it. You get a table, then a summary. Real output, 2026-08-27:

   ```
   OFF ON SUNDAY CONTENT                      2.0 GB    91.2 GB  1/149 local
   ARmazing SF                                   0 B    88.0 GB  0/172 local
   ...
   1 files on disk (2.0 GB)
   1810 files online-only (788.3 GB)
   27.8 GB free on the drive holding /Users/milesdipaola/SavvySelects
   ```

2. **Sanity check against physical reality.** Count the same files a completely
   different way and see if the answer matches:

   ```bash
   cd "$HOME/Dropbox/Content by Event"
   find . -type f \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' \
     -o -iname '*.avi' -o -iname '*.mkv' -o -iname '*.mts' -o -iname '*.lrv' \
     -o -iname '*.insv' -o -iname '*.insp' \) ! -name '.*' | wc -l
   ```

   Must equal the total from step 1 (on disk + online-only). Got **1811**
   against 1 + 1810. Match.

3. **The impossibility test.** The "on disk" total must be *smaller than the
   drive*. A Mac with 27.8 GB free cannot be holding 790 GB of footage. If
   `check` ever claims most of the archive is on disk while `df -h ~` says the
   drive is nearly full, the cloud detector is broken — believe the drive, not
   the tool.

4. Confirm which files are really here, using macOS's own flag. Dropbox marks
   files that aren't downloaded as `dataless`:

   ```bash
   stat -f '%b blocks  %Sf flags  %N' "$HOME/Dropbox/Content by Event/Yelp Teleferic Barcelona Content/"*.MOV
   ```

   A parked file reads `0 blocks  compressed,dataless`. A real one reads a big
   block count and `- flags`.

Seen red 2026-08-27: with the cloud detector broken on purpose, `check` reported
`1811 files on disk (790.3 GB)` and `0 files online-only` — on a Mac with 27.8 GB
free. Step 3 catches exactly that. `make test` caught it too
(`test_zero_blocks_with_real_size_is_cloud_only`,
`test_dataless_flag_is_cloud_only_even_when_blocks_look_fine`).

---

## `savvy fetch` — deliberately bring one event down from Dropbox

**What it is.** The only thing in the tool that downloads footage, and it only
runs when you ask for it by name. It checks the event will fit before starting
and keeps 5 GB spare.

**How to run it.**

```bash
.venv/bin/savvy fetch --event "DELTA REEL" --dry-run   # show me the damage
.venv/bin/savvy fetch --event "DELTA REEL"             # actually pull it down
.venv/bin/savvy fetch --max-gb 20                      # stop after 20 GB
```

**Proving the dry run really downloads nothing.**

1. Pick a small event that's entirely in the cloud. Note its state first:

   ```bash
   EV="$HOME/Dropbox/Content by Event/Yelp Teleferic Barcelona Content"
   stat -f '%b blocks  %Sf flags  %N' "$EV"/*.MOV
   df -h ~ | tail -1
   ```

   Both files read `0 blocks  compressed,dataless`.

2. Run the dry run:

   ```bash
   .venv/bin/savvy fetch --event "Yelp Teleferic Barcelona Content" --dry-run
   ```

   It lists two files, 25 MB and 19 MB, under `Dry run. Nothing downloaded.`

3. Run the `stat` and `df` from step 1 again. **Both files must still read
   `0 blocks  compressed,dataless`, and free space must not have dropped by
   44 MB.** Confirmed 2026-08-27.

4. Ask for an event that isn't parked and confirm it tells you instead of
   guessing:

   ```bash
   .venv/bin/savvy fetch --event "NOT A REAL EVENT" --dry-run
   ```

   Prints `Nothing parked under 'NOT A REAL EVENT'.` and lists the real ones.

Seen red 2026-08-27: with the dry-run stop removed, the tool went straight for
both real files. It was caught by a stand-in that only printed
`TRIPWIRE: would have downloaded ...` and opened nothing, so no footage moved
during the test. That stop is the only thing between `--dry-run` and 44 MB.

---

## `savvy proxy` — index everything, build small copies

**What it is.** Walks your source folders, writes down every video it finds,
and builds a small 720p copy of each one that's actually on the Mac. Everything
later reads those small copies, which is what makes a 2 TB archive workable.
Files still on Dropbox get parked, not opened. Insta360 `.insv` files get parked
too — they aren't usable video until you reframe them.

**How to run it.**

```bash
.venv/bin/savvy proxy
```

**Proving it works.**

1. On an event that's entirely in the cloud, `proxy` must index but build
   nothing:

   ```
   Indexed 2 files.
   Parked 2 online-only files (44 MB) - not on this disk, so not touched.
   Building 0 proxies -> .../proxies
   ```

   Confirmed on the real Yelp event, 2026-08-27. `Building 0 proxies` is the
   line that matters: it never opened a Dropbox placeholder.

2. Afterwards, re-run the `stat` check from the fetch section. The files must
   still be `0 blocks  compressed,dataless`.

3. Run `proxy` twice on the same footage. The second run is fast and doesn't
   rebuild anything (`test_proxy_is_idempotent`).

4. In `make test`: `test_proxy_never_transcodes_a_placeholder`,
   `test_proxy_parks_cloud_only_and_proxies_the_local_one`,
   `test_proxy_indexes_and_parks_insta360`, `test_proxy_is_smaller_than_source`.

---

## `savvy scan` — split into shots, bin the unusable ones for free

**What it is.** Finds where each shot starts and ends, then throws out anything
soft, black or blown out using local maths. Free, and it's where most of the
archive dies before anything costs money.

**How to run it.**

```bash
.venv/bin/savvy scan
```

**Proving it works.**

1. Run `make test`. `test_scan_rejects_black_and_blurred_shots` builds footage
   containing deliberately sharp shots and deliberately black/blurred ones and
   checks at least two of each land on the right side.
2. Break it on purpose per the `make test` section (make sharpness a constant).
   The rejection test must fail. Seen red 2026-08-27.
3. After a real run, `savvy status` shows the split. If `scan` is keeping almost
   everything, the gate isn't working and the next paid step will cost far more
   than it should.

---

## `savvy score` — the only step that costs money

**What it is.** Builds a three-frame strip per surviving shot and has Claude
grade it against the rubric in `savvy/rubric.py`. This is the one stage that
sends anything off the Mac, and the only one that spends money.

**How to run it.**

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
.venv/bin/savvy score --limit 200
```

**Proving it works.**

1. With no key set, it must refuse rather than half-run:
   `Set ANTHROPIC_API_KEY first.`
2. **Always run `--limit` first.** Run `--limit 5`, then `savvy status`, and
   confirm exactly five clips moved to `scored`. Only then run a bigger batch.
3. `savvy status` after a run: `grader 7+` should be a small fraction of the
   total. If nearly everything scores 7+, the rubric has stopped discriminating
   and needs `savvy calibrate`, not a bigger batch.

**Not proven automatically.** No test covers the live API call — the suite runs
offline by design. The `--limit 5` step above is the check, and it's a human
one. Treat every `score` run as spending money until you've seen the count move.

---

## `savvy review` — rate them yourself

**What it is.** A deck at `localhost:8420`. Clips loop between their in and out
points, a filmstrip under the player scrubs the shot. `1`–`5` rate and advance,
`X` cuts it, `F` sends it to the reel crate, `T` tags it.

**How to run it.**

```bash
.venv/bin/savvy review
```

**Proving it works.**

1. It refuses to open with nothing to show: `No clips yet. Run savvy scan first.`
2. Open it. A clip plays, and the filmstrip appears under the player.
3. **Drag the scrubber.** If the video won't seek, the byte-range support is
   broken — that's `test_video_supports_byte_ranges` and it is load-bearing.
4. Rate a clip, quit with ctrl-c, run `savvy review` again. The rating is still
   there. `savvy status` shows `you rated` going up.
5. In `make test`: `test_rating_persists`, `test_video_supports_byte_ranges`,
   `test_filmstrip_generated_on_demand`, `test_todo_filter_excludes_rated`.

**Before testing this against the real deck, check Miles isn't using it.** Two
browser tabs on the same address share one database and the last save wins. If
you see ratings you didn't make, stop — he's in there.

---

## `savvy calibrate` — where you and the grader disagree

**What it is.** Compares your star ratings against the grader's scores and
reports which tags it overrates and which shots it keeps missing. The output is
meant to be pasted into a chat so the rubric gets rewritten against real
disagreements instead of guesses.

**How to run it.**

```bash
.venv/bin/savvy calibrate
```

**Proving it works.**

1. With fewer than twenty rated clips it must refuse, not print noise:
   `Only 0 rated clips. Review at least 20 first.` Confirmed 2026-08-27.
2. With enough ratings, the average gap line must agree with your gut. If it
   says "well calibrated" while you're rejecting most of what it picked, the
   ratings and the scores aren't lining up on the same clips.

---

## `savvy export` — cut the winners out of the originals

**What it is.** Trims your picks out of the **original full-quality files**, in
16:9 and 9:16, and writes `catalog.csv`. Timecodes come from the small copies;
the pixels come from the originals. Filenames lead with the rating so sorting by
name puts the best material on top.

**How to run it.**

```bash
.venv/bin/savvy export --use-ratings --min-rating 4
.venv/bin/savvy export --crate
```

**Proving it works.**

1. **Against real footage: it must refuse to cut an original that's still on
   Dropbox.** With a clip pointing at a real parked file, export prints:

   ```
   ! original is online-only, skipping: IMG_2939.MOV
   1 clips skipped because the original is not on this disk. Bring the event
   down, then export again:
     savvy fetch --event "Yelp Teleferic Barcelona Content"
   ```

   Then check the file is still parked (`0 blocks  compressed,dataless`) and
   free space hasn't moved. Confirmed 2026-08-27 with a stand-in for the cutter
   that could not have opened a file even if the check had failed — it never
   fired, meaning export stopped before reaching it.

   This is the most dangerous thing in the tool. A missing check here means a
   long export quietly drags whole originals down from Dropbox overnight and
   fills the drive.

2. Your rating beats the grader's score in the filename ranking
   (`test_export_prefers_human_rating_over_grader_score`).
3. `--crate` exports only what you flagged with `F`
   (`test_crate_export_only_takes_flagged`).
4. `catalog.csv` carries both verdicts — yours and the grader's
   (`test_export_writes_catalog_with_both_verdicts`).
5. **Sanity check against physical reality.** Open two exported clips. They must
   look like full-quality footage, not soft 720p. If they look like the small
   copies, export is reading the wrong files and the whole point of the tool is
   gone.

---

## `savvy status` — where things stand

**What it is.** Counts by stage. The first thing to run when something feels
wrong.

**How to run it.**

```bash
.venv/bin/savvy status
```

**Proving it works.** Run it before and after any stage; the counts must move in
the direction that stage claims. Real output, 2026-08-27:

```
MEDIA
  cloud_only            2   0.0 GB
CLIPS
  scored                1
  grader 7+           1
  you rated           0
  reel crate          0
  2 files online-only (44 MB), not on this disk.
```

If a stage says it did work and `status` doesn't move, believe `status`.

---

## One writer per durable value

Every lasting thing here should have exactly one piece of code allowed to
change it. Where that isn't true, it's written down rather than quietly
restructured. `tests/test_one_writer.py` enforces the table below and fails if a
new writer appears.

| The lasting thing | Who writes it | Verdict |
|---|---|---|
| **Your footage** under `sources` | **nobody** | ✅ Clean. Nothing writes, renames or deletes source files. Export, local proxy/preview preparation, and explicit fetch read originals; no source writer is allowed. |
| **`config.json`** (your folder paths) | **nobody in the code** — `config.py:31 load()` only reads it | ⚠️ **Violated once, by a tool.** See below. |
| **`selects.db` → `media` table** | `stages/proxy.py:41,49,59,63,79,86,95,98`; `stages/fetch.py:74,80,92`; `stages/scan.py:19,52` | ⚠️ **Three writers.** Documented, not restructured. |
| **`selects.db` → `clips` table** | `stages/scan.py:47`; `stages/score.py:39,58,62`; `stages/export.py:73`; `review/server.py:135` | ⚠️ **Four writers**, but cleanly split by column — see below. |
| **`selects.db` schema** | `db.py:66-72` only | ✅ Clean. |
| **Your own ratings, tags and crate flags** (`my_rating`, `my_tags`, `flagged`) | `review/server.py:135` only | ✅ **Clean, and this is the one that matters.** |
| **Small copies** `work_dir/proxies/*.mp4` | `stages/proxy.py:91` | ✅ Clean. |
| **Filmstrips** `work_dir/filmstrips/*.jpg` | `media.py:80 filmstrip()`, called only from `review/server.py:101` | ✅ Clean. |
| **Exported clips + `catalog.csv`** | `stages/export.py:65,70,88` | ✅ Clean. |

**On the `media` and `clips` tables.** Four stages writing one table breaks the
rule by the letter. In practice each one owns a different column and a different
moment: `proxy` sets where a file is, `fetch` flips it once it's landed, `scan`
marks it done, `score` writes the grade, `export` records what was cut. They run
one at a time, never together. The review server is the only one that runs
threaded, and it takes a lock before writing (`review/server.py:30-33`). This is
noted as a real structural violation and left alone — splitting it would be a
rewrite with no safety gained today. What `tests/test_one_writer.py` buys is
that a *fifth* writer can't appear without someone deciding to add it.

**The one that actually protects Miles** is the bottom-but-three row. Proxies,
filmstrips and exports can all be rebuilt from the footage. His ratings cannot —
they're hours of his own listening. Exactly one file may write them, and the
test enforces it.

**The `config.json` violation, in full.** There is a
`config.json.backup-before-claude` sitting in the repo. A tool edited
`config.json` directly — a file no code in this project is allowed to write —
and left a backup behind. What changed:

```
before:  /Users/milesdipaola/Dropbox/Content by Event
after:   /Users/milesdipaola/Library/CloudStorage/Dropbox/Content by Event
```

**No harm done:** `~/Dropbox` is a symlink to `~/Library/CloudStorage/Dropbox`,
so both paths point at the same folder. The edit was cosmetic. But the boundary
was crossed, so `test_nothing_in_the_package_writes_config_json` now guards it.
Leave the backup file in place until Miles says otherwise — it's the only record
of what the path used to be.

**Also found in `~/SavvySelects/`, written by no code in this repo:**
`_claude_strips/` (38 jpegs, 2.8 MB), `_to_delete/` and
`selects.db.backup-before-claude-scoring`. Same pattern: a tool writing into the
work folder out of band. **`_to_delete/` was opened and checked before anything
was said about it — it holds one stale database journal file and no footage.**
Nothing here has been deleted. Deleting it wouldn't free space anyway while Time
Machine's local snapshots still pin it (`tmutil listlocalsnapshots /`).

---

## Trust ladder — how far this tool can be trusted

Three rungs before anything is allowed to run unattended:

1. Its proof recipe exists.
2. The recipe has caught at least one real problem.
3. A dry run against real footage has been checked by a person.

**Savvy Selects sits on rung 2, and only for the reading half of the tool.**

- **Rung 1 — done.** This file.
- **Rung 2 — reached, honestly but narrowly.** The one-writer check caught a
  real error on its first run: the map originally claimed `scan` didn't write
  the `media` table. It does, twice. That's a boundary nobody had noticed. It
  found a mistake in the write-up rather than a bug in the pipeline, so it
  counts, but it isn't a scare.
- **Rung 3 — half done.** `check`, `proxy`, `fetch --dry-run` and `export`'s
  refusal to cut a cloud file have all been run against Miles's real Dropbox and
  cross-checked against the drive. **A real `export` that actually cuts footage
  has not been run and proven here**, because only one file out of 1,811 is on
  the Mac and it has no scanned clips.

**What would promote it:**

- Bring one small event down with `fetch`, run the whole chain through to
  `export`, and confirm the exported clips are full quality and cut where the
  review deck said they were. That closes rung 3.
- Run `score --limit 5` against the real API and confirm the count moves and the
  spend matches. No test covers the paid path.
- Let the proof recipe catch a genuine pipeline bug, not just a documentation
  error.

Until then it stays a tool you sit in front of.

---

## Unattended rules

**Nothing here runs on a schedule, overnight, or unwatched until its section
above has caught a real problem.**

- `score` spends money and is never scheduled. It runs with `--limit`, watched,
  every time.
- `fetch` downloads footage onto a drive with 28 GB free. It runs with
  `--dry-run` first, watched, every time.
- `export` writes clips and reads originals. It runs watched until rung 3 is
  closed.
- `check`, `status` and `make test` are safe to run any time — they read only.
- Anything that graduates to running on its own must report in plain words what
  it did ("checked 214 clips, 2 need you") and must never do something it can't
  undo.
- **Nothing in this tool may ever move, rename or delete anything in your source
  folders.** That isn't a rung on the ladder, it's the floor.


## Private preview library — open, watch, and choose

This is the new private viewing room on port 8421. It uses `~/SavvyPreviewLibrary`, separate from the older `~/SavvySelects` review deck and its ratings. Keep, Maybe, and Pass save choices only. Pass does not remove anything.

Open **Open Savvy Preview Library.command** on the Desktop. It opens the same library if already running. If another app owns that address, it leaves that app alone and explains why it stopped. When it starts the library itself, leave its window open while reviewing. No scheduled or overnight task is installed.

### Checks against disposable footage

From this checkout:

```bash
PYTHONPATH=. /Users/milesdipaola/savvy-selects/.venv/bin/python -m pytest -q tests/test_library_prepare.py tests/test_library.py tests/test_library_launch.py tests/test_one_writer.py
node tests/test_library_ui_race.cjs
```

The producer currently has **12 checks**. They cover full recording duration, portrait framing, original audio, silent existing previews, photos, source size/date preservation, online-only refusal, missing and changed sources, 360 companions, shared footage for private viewing, insufficient free space, linked paths, partial-file cleanup, timeouts, and damaged saved previews. The output MP4 check proves the playback header precedes the media data so playback can start promptly. Small synthetic footage is created by the checks; no original career footage is used.

Cloud protection was deliberately removed **only in a disposable copy** after its normal check passed. The same check then failed with `opened protected media`. The working checkout was never broken. This proves the check catches a real attempt to open cloud media; it is not authorization to start downloads.

The launcher has four checks: exact library/workspace identity, reusing the correct running library, refusing another app on the same port, and starting with the correct workspace. Its identity check uses `/api/health`; a server without that check is not silently trusted.

### Saved archive import

The import reads the saved CSV lists; it does not open listed original footage. On a disposable workspace, run:

```bash
PYTHONPATH=. /Users/milesdipaola/savvy-selects/.venv/bin/python -m savvy.library --workspace /tmp/savvy-library-proof import --inventory '/absolute/path/to/media-inventory.csv'
```

Use a new disposable folder, and a small synthetic inventory when testing. Confirm items appear with their source and event, originals are not opened, unsupported material remains visible with a clear reason, and repeating the import does not erase Keep/Maybe/Pass choices. Existing previews may be reused only from the specifically approved local preview paths. Their actual audio is checked; a silent old preview is labeled silent rather than described as having original sound.

### Local preview preparation

`prepare_item(workdir, item, dry_run=True)` checks current file metadata and reports availability without opening media or creating output. A normal explicitly requested preparation creates a whole playable preview and poster under that workspace's `previews` folder. The store alone saves the resulting paths and choices.

Check one short local video and one photo first. Confirm the video runs from beginning to end, the sound button reflects actual audio, portrait material stays portrait, the photo opens, and the originals retain their size and modification date. Online-only files must remain online only. RAW photos and native 360 recordings must stay visible without a false ready badge. Shared material may be privately previewed; that does not grant public-use rights. Preview preparation retains a 20 GiB free-space floor and a 5 GiB cache allowance.

### Real viewing soundcheck

1. Open the Desktop launcher and confirm the page says **Preview library** with **Savvy Sounds** underneath. Open it again: it reuses the same library without starting a second one.
2. Search for a known event. Open a video, press play, then seek near its end. Playback must seek correctly; the server's byte-range check must also pass.
3. Open a portrait video and a photo. Confirm neither is cropped into a landscape frame. Verify any silent-preview label against the player's actual sound.
4. Choose Keep, Maybe, and Pass on three review items. Reload; the choices remain. Search and filters must not change those decisions. Confirm Pass changed only the choice, not the original file.
5. Export the Keeps list. It contains only Keep choices and retains original source references for later editing. It does not copy, move, or delete the footage.
6. Disconnect a source drive only after ongoing preparation finishes. Already saved previews remain playable; unprepared sources explain that the drive is unavailable.
7. Trigger preparation once, then click another item before it finishes. The completed result must update the correct item without taking over the newly selected one. The automated browser-function check separately covers a choice saved while a stale reload is in flight.

Final real-preview counts and completed screen checks are recorded by the lead after the bounded seed finishes. Do not treat the archive inventory count as a count of watched or ready clips.

### Checked in the real library — September 14, 2026

The lead completed a full 73-check run in 21.76 seconds before the later launcher checks were added. The actual browser-function race check passed; deliberately removing its guard in isolation caught a stale reload that lost a choice. In the real page, a Keep choice survived reload and the temporary review choice was then reset. A video played through 17 seconds with audio present. At 390-pixel width in the light appearance, the page had no horizontal overflow. An empty search and Clear restored the results. The bounded preview seed was still running when these checks were recorded; this paragraph makes no final ready-count claim.

### Canceling a preview during playback

Observed September 14, 2026: moving to another clip closed the video connection; the server attempted a second error response after already starting the video response. The regression check `tests/test_library.py::test_stream_disconnect_never_sends_second_response` reproduced all three failure forms (broken pipe, reset connection, and a later stream read/write error) before the fix. All three now close the connection without another response. Run `python -m pytest tests/test_library.py tests/test_one_writer.py -q`, then play a preview and switch clips several times; the server should remain usable without repeated disconnect tracebacks. These checks use throwaway preview bytes, not original footage.

### Final attended handoff — September 14, 2026

54 ready:23 reused Delta videos plus31 newly prepared local videos/photos. All31 original sizes and modification dates independently matched the pre-preview first-look record. New cache38.5MB; no cloud download, source writes, or deletion. All temporary UI choices reset to Unreviewed. Real Make preview button completed the54th item; native player reached readyState4. Photo decoded360x480; Maybe→Pass→Undo restored Maybe, then reset. Desktop launcher tested both existing-server reuse and fresh start. Final affected checks31passed2.39s after streaming fix; full earlier suite73passed. Browser load of20,959 records measured0.45s. Desktop dark and390px light reviewed; narrow viewport reset. Other clouds remain outside this catalog. Long encodes are bounded to120seconds and can return an actionable error. This is attended preview work, no scheduled processing.

### HEIC whole-photo regression — September 14, 2026

A real camera-roll review caught three HEIC previews falsely marked ready after only one encoded tile was selected by `ffmpeg -map 0:v:0`. A successful decode and square dimensions were insufficient evidence of a complete photo. HEIC now uses the Mac's native primary-image reader (`sips`/ImageIO), then applies orientation and makes the JPEG preview. Other formats keep their existing path. HEIC source dimensions come from the native full-image metadata; displayed preview dimensions come from the orientation-corrected JPEG.

```bash
PYTHONPATH=. /Users/milesdipaola/savvy-selects/.venv/bin/python -m pytest -q tests/test_heic_primary.py tests/test_library_prepare.py
```

All 15 checks passed. The dedicated check supplies a two-color complete picture and refuses a first-video-stream decoder; both sides must remain visible in the preview. Separate checks prove online-only refusal and temporary-file cleanup on native conversion failure. This check fails if the old first-stream HEIC route returns.

Real read-only proof used camera-roll DJ-gigs indices 277 (`IMG_2556.heic`), 101 (`IMG_2996.HEIC`), and 717 (`IMG_4438.HEIC`). Primary image dimensions were 3024×4032, 4032×3024, and 4032×3024, respectively. After orientation, previews were 360×480, 360×480, and 640×480. All three were visually checked and show the full photo rather than a tile. Source sizes and modification dates matched before/after. Evidence: `camera-roll-mitsubishi/heic-primary-proof/receipt.json` in the September 14 content-triage documents. Existing incorrect generated previews must be isolated and regenerated by the preview owner; source originals are never removed.

The HEIC check was also run green in a disposable copy, then the primary-image branch was deliberately disabled there. The same check failed on its `HEIC must not select ffmpeg first tile` assertion. The shared working checkout was never altered by that fault-injection run.
