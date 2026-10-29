"""`savvy watch`: Gemini watches one video, sound included, and writes it up.

Claude reads still frames; it can't take in a whole video with its audio. Gemini
can, so the /watch skill hands a video to Gemini and reads the report back.

This sends footage to Google, which nothing else in the pipeline does. So it
takes exactly one file per run, never a folder, and uploads a small trimmed copy
built in work_dir, never the original. It can't quietly sweep the archive the
way a loop over `sources` would.
"""
import os
import re
import time
from pathlib import Path

from .. import config, gemini, media, storage
from ..rubric import AUDIENCE, TASTE

# Gemini's published rates at default media resolution. Used only to print an
# estimate up front and to keep a long video inside the context window.
TOKENS_PER_FRAME = 258
AUDIO_TOKENS_PER_SEC = 32
TOKEN_BUDGET = 900_000

GROUND_RULES = """Ground rules:
- Describe only what is actually on screen or audible. Never invent names,
  speakers, narrators, dialogue or brands. If nobody speaks, say so.
- Name a brand, venue or artist only if it is legible on screen or clearly said
  aloud, and say which.
- Timestamps are MM:SS from the start of this video.
- When you're unsure, say what you're unsure of instead of guessing.
- Plain, specific language. No hype."""

REPORT = """Watch this whole video with its sound and write a report in markdown.

## Summary
Three to five sentences: what this is, where it seems to be, what happens.

## Scene by scene
One line per scene or run of similar shots: `MM:SS-MM:SS` then what's on screen.

## Audio
The music (genre, energy, whether it's live, a DJ set, or laid in during the
edit), crowd sound, and any speech transcribed verbatim with timestamps. If
there's no speech, write "No speech."

## On-screen text and signage
Every legible caption, title card, logo, banner or sign, with timestamps.

## Key moments
The three to five strongest moments, with timestamps and why they stand out."""

TEARDOWN = """This is an ad, reel or promo. Break down how it's built so an editor
could make something that works the same way for this company:
{audience}

## Hook
The first three seconds exactly: what's on screen, what's heard, and whether it
would stop someone scrolling. Be honest.

## Structure
The beats of the edit in order, with timestamps: setup, build, payoff, ask.

## Pacing and cuts
{cuts}
Say where the edit speeds up or breathes, and whether cuts land on the beat.

## Sound
The track (genre, tempo feel, drops), any voiceover or dialogue verbatim, and
sound design.

## Text and graphics
Every on-screen caption and graphic, verbatim, with timestamps.

## The ask
What it wants the viewer to do, and how it asks.

## What to take from it
Three specific moves Savvy Sounds could use in its own reel or ads, each tied to
a moment in this video. No generic advice."""

SELECTS = """This is raw event footage from the company's own archive.
{audience}

Find the moments worth cutting into the reel or an ad, judged by this:

{taste}

Write markdown:

## Selects
A table, best first, columns: In | Out | Score 0-10 | What it is | Tags.
Each select is one continuous shot, {min_s:g} to {max_s:g} seconds long. Only
include moments that score 6 or higher. If there are none, say so plainly.

## Skip
The unusable stretches and why, one line each (e.g. 00:40-02:10 load-in).

## Sound worth keeping
Moments where crowd noise, a mic moment or a peak in the music would carry an
edit, with timestamps."""

# Frames per second Gemini samples. Teardowns are short and cut fast, so they
# get more; the measured cut list covers what sampling still misses.
MODES = {"report": 1.0, "teardown": 2.0, "selects": 1.0}

YOUTUBE = re.compile(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", re.I)
YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]{11})")


def parse_time(s):
    """'90', '1:30' or '0:01:30' -> seconds. None stays None."""
    if s in (None, ""):
        return None
    try:
        secs = 0.0
        for part in str(s).split(":"):
            secs = secs * 60 + float(part)
        return secs
    except ValueError:
        raise SystemExit(f"Can't read the time {s!r}. Use seconds or M:SS.")


def clock(secs):
    secs = int(round(secs))
    h, m, s = secs // 3600, secs % 3600 // 60, secs % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def estimate_tokens(duration, fps):
    return int(duration * (fps * TOKENS_PER_FRAME + AUDIO_TOKENS_PER_SEC))


def pick_fps(duration, wanted, budget=TOKEN_BUDGET):
    """The asked-for fps, or the highest lower one that fits. None if none does."""
    if estimate_tokens(duration, wanted) <= budget:
        return wanted
    fps = (budget / max(duration, 1) - AUDIO_TOKENS_PER_SEC) / TOKENS_PER_FRAME
    return round(fps, 2) if fps >= 0.1 else None


def cut_summary(cuts):
    """Measured shot lengths, so the teardown's pacing claims aren't guesses.

    None means nothing was measured (a YouTube link), which is not the same as
    a video with no cuts, and the prompt has to say which.
    """
    if cuts is None:
        return ("No measured cut list for this one. Estimate shot lengths from "
                "what you see and say they are estimates.")
    lengths = [b - a for a, b in cuts if b - a > 0.05]
    if len(lengths) < 2:
        return "Measured locally from the file: one continuous shot, no cuts."
    marks = ", ".join(f"{a:.1f}s" for a, _ in cuts[1:])
    return (f"Measured locally from the file, use these rather than estimating: "
            f"{len(lengths)} shots, average {sum(lengths) / len(lengths):.1f}s, "
            f"shortest {min(lengths):.1f}s, longest {max(lengths):.1f}s. "
            f"Cuts at: {marks}.")


def build_prompt(mode, ask, cfg, cuts=None):
    if ask:
        body = ask
    elif mode == "teardown":
        body = TEARDOWN.format(audience=AUDIENCE, cuts=cut_summary(cuts))
    elif mode == "selects":
        body = SELECTS.format(audience=AUDIENCE, taste=TASTE,
                              min_s=cfg["min_clip_seconds"],
                              max_s=cfg["max_clip_seconds"])
    else:
        body = REPORT
    return f"{body}\n\n{GROUND_RULES}"


def _refuse_if_unwatchable(path, cfg):
    """Every reason not to open this file, checked before anything opens it."""
    if not path.exists():
        raise SystemExit(f"No such file: {path}")
    if path.is_dir():
        raise SystemExit("One video at a time. Point watch at a file, not a folder.")
    if path.suffix.lower() in cfg["insta360_ext"]:
        raise SystemExit("That's 360 source. Reframe it in Insta360 Studio and "
                         "watch the flat export.")
    # Before ffmpeg or anything else reads it: opening a placeholder is what
    # makes Dropbox download it.
    if storage.is_cloud_only_path(path):
        raise SystemExit(
            f"{path.name} is online-only in Dropbox; opening it would download it.\n"
            f"Make it available offline in Finder first, or if it's in your "
            f"sources:\n  savvy fetch --event \"NAME\"")


def run(cfg, con, args):
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise SystemExit("Set GEMINI_API_KEY first. Get one at "
                         "https://aistudio.google.com/apikey")

    mode = args.mode or "report"
    model = args.model or cfg["gemini_model"]
    start, end = parse_time(args.start), parse_time(args.end)
    if start and end and end <= start:
        raise SystemExit("--end has to be after --start.")

    out_dir = config.work_dir(cfg) / "watch"
    out_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    client = gemini.Client(key)
    video_meta, remote, copy = {}, None, None

    try:
        if YOUTUBE.match(args.video):
            # Gemini fetches public YouTube videos itself; nothing is uploaded.
            # Share links carry tracking params (?si=, ?is=) and come as
            # youtu.be; send the plain watch URL instead.
            m = YOUTUBE_ID.search(args.video)
            url = f"https://www.youtube.com/watch?v={m.group(1)}" if m else args.video
            source, name = url, "youtube-" + (m.group(1) if m else stamp)
            cuts = None
            fps = args.fps or MODES[mode]
            if start:
                video_meta["startOffset"] = f"{start:g}s"
            if end:
                video_meta["endOffset"] = f"{end:g}s"
            part = {"fileData": {"fileUri": url}}
            print(f"Asking {model} about {url}")
        else:
            path = Path(args.video).expanduser().resolve()
            _refuse_if_unwatchable(path, cfg)
            source, name = str(path), path.stem

            copy = out_dir / f"{name}-{stamp}.mp4"
            print("Making a 720p copy to send (the original isn't uploaded)")
            ok, err = media.watch_copy(path, copy, start, end, hw=cfg["hwaccel"])
            duration = media.probe_duration(copy) if ok else 0.0
            if not duration:
                raise SystemExit(f"ffmpeg couldn't read {path.name}. {err}".strip())

            fps = pick_fps(duration, args.fps or MODES[mode])
            if fps is None:
                raise SystemExit(f"{clock(duration)} is too long to send in one go. "
                                 f"Pick a range with --start and --end.")
            if fps != (args.fps or MODES[mode]):
                print(f"  Long video: sampling {fps:g} fps to fit Gemini's context.")

            cuts = (media.detect_scenes(copy, cfg["scene_threshold"], duration)
                    if mode == "teardown" and not args.ask else None)

            mb = copy.stat().st_size / 1e6
            print(f"  {clock(duration)}, {mb:.0f} MB, about "
                  f"{estimate_tokens(duration, fps):,} input tokens at {fps:g} fps")
            print("Uploading to Gemini")
            remote = client.upload(copy)
            print("  Gemini is processing the video")
            remote = client.wait_active(remote)
            part = {"fileData": {"fileUri": remote["uri"],
                                 "mimeType": remote.get("mimeType", "video/mp4")}}

        video_meta["fps"] = fps
        part["videoMetadata"] = video_meta
        prompt = build_prompt(mode, args.ask, cfg, cuts)
        print(f"Watching with {model}" + (f" ({mode})" if not args.ask else ""))
        try:
            text, usage = client.generate(model, [part, {"text": prompt}])
        except gemini.GeminiError as e:
            if e.code != 404:
                raise
            names = [m for m in client.models() if "flash" in m or "pro" in m]
            raise SystemExit(f"{model} isn't available on this key. Try one of:\n  "
                             + "\n  ".join(names[:12])
                             + "\nthen --model NAME, or set gemini_model in config.json.")
    except gemini.GeminiError as e:
        raise SystemExit(str(e))
    finally:
        # Gemini would expire the upload in 48 hours anyway; client footage
        # shouldn't sit on someone else's server longer than the question takes.
        if remote:
            try:
                client.delete(remote["name"])
            except gemini.GeminiError as e:
                print(f"  Couldn't delete {remote['name']} from Gemini ({e}). "
                      f"It expires on its own within 48 hours.")
        if copy and copy.exists():
            copy.unlink()

    header = [f"# {name}", "", f"- source: {source}"]
    if start or end:
        span = f"{clock(start or 0)}-{clock(end) if end else 'end'}"
        header.append(f"- range: {span} of the source." + (
            " Timestamps below count from the start of this range." if copy else ""))
    label = f"question: {args.ask}" if args.ask else f"mode: {mode}"
    tin, tout = usage.get("promptTokenCount"), usage.get("candidatesTokenCount")
    tokens = f" | tokens in {tin:,} out {tout:,}" if tin and tout else ""
    header.append(f"- {label} | {model} | {fps:g} fps{tokens}")
    report = "\n".join(header) + "\n\n" + text.strip() + "\n"

    out = out_dir / f"{name}-{'ask' if args.ask else mode}-{stamp}.md"
    out.write_text(report)
    print("\n" + report)
    print(f"Saved {out}")
    return out
