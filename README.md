# Savvy Selects

Turns a footage archive into a short list of clips worth using.

Point it at your Dropbox folders and external drives. It builds proxies, splits
everything into shots, throws out the shaky and dark and soft ones for free,
grades what survives against a rubric written for premium corporate marketing,
and hands you trimmed clips cut from the original files at full quality.

Then you audit the results in a local review deck — play, rate, advance, the way
you'd go through a crate — and export what you picked.

Nothing leaves your machine except downsampled contact strips during grading.

## Setup

```bash
brew install ffmpeg
git clone <your-repo> && cd savvy-selects
make setup
export ANTHROPIC_API_KEY="sk-ant-..."   # add to ~/.zshrc so it sticks
```

`make setup` creates a venv, installs the package, and copies
`config.example.json` to `config.json`. Edit that with your real paths:

```json
{
  "sources": [
    "/Users/miles/Dropbox/Content by Event",
    "/Volumes/EXTERNAL/Footage"
  ],
  "work_dir": "/Users/miles/SavvySelects",
  "hwaccel": true
}
```

The API key is from console.anthropic.com — separate from a Claude subscription.
Twenty dollars of credit covers a first pass at a large archive.

## Run

```bash
savvy check                      # what's actually on this Mac
savvy fetch --event "DELTA REEL" # pull one event down from Dropbox
savvy proxy                      # overnight, free
savvy scan                       # 1-2 hours, free
savvy score --limit 200          # the only paid stage
savvy review                     # rate them yourself
savvy export --use-ratings --min-rating 4
savvy assemble --track "/path/to/track.mp3"  # beat-synced rough cut EDL
```

Every stage is resumable. Close the laptop, plug the drive back in tomorrow, run
the same command. `savvy status` shows where things stand.

Start narrow. Point `sources` at one event folder and run all six steps before
turning it loose on the whole archive.

## Footage that lives in the cloud

Most of a Dropbox folder usually isn't on your Mac. The file shows up in Finder
with its real size, but the video itself is still on Dropbox's servers. Opening
one is what makes it download.

That matters here because a full run would quietly pull down everything at once
and fill your drive overnight while you're asleep.

So nothing downloads unless you say so. `savvy check` shows you, event by event,
what's on the Mac and what isn't:

```
event                                on disk   in cloud  files
OFF ON SUNDAY CONTENT                 2.0 GB    91.2 GB  1/149 local
DELTA REEL                            1.4 GB        0 B  23/23 local
```

Anything in the cloud gets parked — indexed so it's counted, but never touched.
When you want to work on an event, bring it down first:

```bash
savvy fetch --event "New Balance LA MARATHON"
savvy fetch --event "SPE HOLIDAY 2025" --dry-run    # just show me the damage
savvy fetch --max-gb 20                             # stop after 20 GB
```

`fetch` checks it'll fit before it starts and leaves 5 GB spare. Then run `proxy`
again and the new footage gets picked up. Work one event at a time, and when
you're done, set that folder back to online-only in Finder to get the space back.

`export` skips anything whose original has gone back to the cloud and tells you
which `fetch` command to run.

## The stages

**proxy** walks your sources and builds a 720p copy of every video. Two terabytes
becomes roughly forty gigabytes. Everything downstream reads proxies, which is
what makes this run overnight instead of over a weekend.

**scan** finds shot boundaries, then rejects anything soft, black, or blown out
using local math. Free, and it's where most of the archive dies — before a single
API call.

**score** builds a three-frame contact strip per surviving shot and grades it:
full floor, real crowd energy, DJ visible and in command, clean rig, venue and
brand signage, intentional lighting. Penalises thin crowds, load-in mess,
phone-in-face shots, anything that reads like a house party. The rubric is plain
English in `savvy/rubric.py`.

**review** opens a deck at `localhost:8420`. Clips loop between their in and out
points. A filmstrip under the player scrubs the shot — click any frame to jump.

| key | does |
|---|---|
| `1`–`5` | rate and advance |
| `X` | cut it |
| `F` | reel crate |
| `T` | your own tags |
| `←` `→` | move without rating |
| `space` | hold |

**export** cuts the winners out of the original files at full quality, in 16:9
and 9:16, and writes `catalog.csv`.

**assemble** is the rough cut. Point it at a DJ track and it reads the BPM from
the file's tags (or estimates it and says so), walks the beat grid in segments
of `--bars` bars, and hands each segment the next best clip from your reel
crate. The output is a CMX3600 EDL — plain text that references your original
files, importable into Premiere, Resolve, or Avid. No render, no new footage.
If you know the tempo, `--bpm` and `--offset` skip the guesswork entirely.

## Output

```
~/SavvySelects/SELECTS/
    16x9/         10_DELTA_REEL_00341.mp4     <- filename leads with the rating
    9x16/         10_DELTA_REEL_00341.mp4
    catalog.csv   your rating, grader score, tags, source file, timecode
```

Sort by name and the best material is at the top. The CSV makes the whole archive
searchable — filter for "packed floor", "rooftop", "brand signage".

## Teaching it your taste

```bash
savvy calibrate
```

After twenty or more human ratings this reports where you and the grader diverge:
which tags it overrates, which shots it keeps missing, whether it's harsh or
generous overall. Edit `savvy/rubric.py` accordingly and re-run `score`. Each pass
the automatic picks land closer to yours.

## Insta360 footage

`.insv` files are 360 source and aren't usable video until reframed, which is a
creative camera-path decision no grader can make. They're indexed and parked, not
processed.

To bring them in: batch-export flat MP4s from Insta360 Studio, drop them in a
folder, add it to `sources`, re-run `savvy proxy`.

## Notes

- Keep `work_dir` on the internal SSD. Proxies get read constantly.
- External drives must be mounted for `proxy` and `export`. `scan`, `score`, and
  `review` only touch proxies, so drives can be unplugged.
- Nothing is ever written to or deleted from your source folders.

## Tests

```bash
make test
```

Builds synthetic footage with ffmpeg and runs the pipeline end to end. No API key,
no network.
