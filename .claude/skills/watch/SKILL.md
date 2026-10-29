---
name: watch
description: Watch a video, sound included, by having Gemini view it and reading back a timestamped report. Use whenever the user wants to know what happens in a video file or YouTube link, wants a transcript, wants an ad or reel broken down shot by shot, or wants the usable moments pulled out of raw event footage. Claude can't watch video on its own; this is how.
argument-hint: <video path or YouTube URL> [what you want to know]
---

# Watch a video

`savvy watch` makes a 720p copy of one video (trimmed if asked), sends it to
Gemini, deletes it from Google once answered, and saves a markdown report to
`~/SavvySelects/watch/`. Your job is to pick the right mode, run it, and then
answer what the user actually asked, using the report.

## Run it

```bash
savvy watch "<path or YouTube URL>" [--mode report|teardown|selects] [--ask "..."] [--start M:SS --end M:SS]
```

If `savvy` isn't on PATH, use `~/savvy-selects/.venv/bin/savvy`.
Quote the path, since event folders have spaces in their names.

| The user wants | Use |
|---|---|
| What's in it, a transcript, a recap | default (`--mode report`) |
| How an ad, reel or promo is built: hook, pacing, text, the ask | `--mode teardown` |
| The best moments in raw event footage, with in/out points | `--mode selects` |
| One specific thing ("is the Delta logo legible?", "when does the drop hit?") | `--ask "the question"` |

- Long file and they only care about part of it: pass `--start` / `--end`.
  Less is sent, it costs less, and it's faster.
- A short reel with very fast cuts: `--fps 4`. Teardown already measures the
  real cuts locally, so this is only for reading fast on-screen text.

## Before you run it

- **One file per run.** Never loop it over a folder or the archive. It's paid
  and it sends footage to Google, so it runs only on the video the user asked about.
- Instagram, TikTok and other social links don't work. Only local files and
  public YouTube URLs. Ask the user to save the video (or screen-record it) and
  give you the file path.
- `.insv` 360 files have to be reframed in Insta360 Studio first.

## When it refuses

| Message | What to tell the user |
|---|---|
| `Set GEMINI_API_KEY first` | Get a key at https://aistudio.google.com/apikey, then `echo 'export GEMINI_API_KEY="..."' >> ~/.zshrc` and open a new terminal. For client footage, use a key from a project with billing turned on. On the free tier Google can use what's uploaded to train its models. |
| `is online-only in Dropbox` | The file isn't on the Mac, and opening it would download it. Make it available offline in Finder, or `savvy fetch --event "NAME"`. Don't work around this. |
| `isn't available on this key` | Rerun with `--model` set to one of the models it listed. |

## After it runs

The report is printed and saved; the last line is its path. Don't paste the
whole report back. Answer the question, cite timestamps, and give the
saved path. If the run used `--start`, timestamps count from that point, and the
report header says so. Add the start time back before quoting source times.
