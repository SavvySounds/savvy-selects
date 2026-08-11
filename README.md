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
savvy proxy                      # overnight, free
savvy scan                       # 1-2 hours, free
savvy score --limit 200          # the only paid stage
savvy review                     # rate them yourself
savvy export --use-ratings --min-rating 4
```

Every stage is resumable. Close the laptop, plug the drive back in tomorrow, run
the same command. `savvy status` shows where things stand.

Start narrow. Point `sources` at one event folder and run all five steps before
turning it loose on the whole archive.

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
