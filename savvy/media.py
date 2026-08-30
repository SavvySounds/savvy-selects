"""ffmpeg and image helpers. Everything that shells out lives here."""
import io
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def probe_duration(path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def probe_fps(path) -> float:
    """ffprobe r_frame_rate, parsed from the 'num/den' string it returns."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of",
             "default=nw=1:nk=1", str(path)])
    try:
        num, den = r.stdout.strip().split("/")
        return float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return 0.0


def audio_samples(path, sr=11025) -> np.ndarray:
    """Decode to mono PCM at a low sample rate via ffmpeg, piped to stdout.

    Returned as float32 in [-1, 1]. The low sample rate is plenty for tempo and
    onset estimation, and keeps the pipe cheap even for a full-length track.
    """
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1",
                        "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return np.zeros(1, dtype=np.float32)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def event_name_from(path, sources) -> str:
    """Event label = the folder directly beneath whichever source root matched."""
    p = Path(path)
    for s in sources:
        s = Path(s)
        try:
            rel = p.relative_to(s)
        except ValueError:
            continue
        return rel.parts[0] if len(rel.parts) > 1 else s.name
    return p.parent.name


def laplacian_variance(img) -> float:
    """Focus proxy. Higher is sharper. Pure numpy so there's no OpenCV dependency."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    lap = (-4 * g[1:-1, 1:-1]
           + g[:-2, 1:-1] + g[2:, 1:-1]
           + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def mean_brightness(img) -> float:
    return float(np.asarray(img.convert("L"), dtype=np.float32).mean())


def frame_at(video, t, height=360):
    """One frame as a PIL image, or None if the seek failed."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale=-2:{height}", "-f", "image2pipe",
         "-vcodec", "png", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return Image.open(io.BytesIO(r.stdout)).convert("RGB")
    except Exception:
        return None


def contact_strip(video, start, end, height=360):
    """Three frames across a shot, tiled left to right. Input for the grader."""
    span = end - start
    frames = [f for f in (frame_at(video, start + span * x, height)
                          for x in (0.2, 0.5, 0.8)) if f]
    if not frames:
        return None
    w = sum(f.width for f in frames)
    h = max(f.height for f in frames)
    strip = Image.new("RGB", (w, h), "black")
    x = 0
    for f in frames:
        strip.paste(f, (x, 0))
        x += f.width
    return strip


def filmstrip(out_path, proxy, start, end, count=14):
    """Wide frame strip for the review scrubber. Cached on disk."""
    out_path = Path(out_path)
    if out_path.exists():
        return out_path
    fps = count / max(end - start, 0.1)
    r = run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
             "-i", str(proxy), "-vf", f"fps={fps:.4f},scale=-2:80,tile={count}x1",
             "-frames:v", "1", "-q:v", "5", "-y", str(out_path)])
    return out_path if (r.returncode == 0 and out_path.exists()) else None


def detect_scenes(proxy, threshold, duration):
    """Shot boundaries as (start, end) pairs covering the whole file."""
    r = run(["ffmpeg", "-v", "info", "-i", str(proxy),
             "-filter:v", f"select='gt(scene,{threshold})',showinfo",
             "-f", "null", "-"])
    times = []
    for line in r.stderr.splitlines():
        if "pts_time:" in line:
            try:
                times.append(float(line.split("pts_time:")[1].split()[0]))
            except (IndexError, ValueError):
                pass
    bounds = [0.0] + sorted(times) + [duration]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def transcode(src, out, vf, hw, bitrate="1800k", crf=30, audio=False):
    """Encode with VideoToolbox, falling back to libx264 when it isn't available."""
    base = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
    if vf:
        base += ["-vf", vf]
    tail = ["-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]
    if hw:
        r = run(base + ["-c:v", "h264_videotoolbox", "-b:v", bitrate] + tail + [str(out)])
        if r.returncode == 0 and Path(out).exists():
            return True, ""
    r = run(base + ["-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast"]
            + tail + [str(out)])
    return r.returncode == 0, r.stderr[-300:]


def cut(src, start, end, out, vf=None, hw=True, bitrate="12000k"):
    """Trim a range out of an original file at full quality."""
    base = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}", "-i", str(src)]
    if vf:
        base += ["-vf", vf]
    tail = ["-c:a", "aac", "-b:a", "192k"]
    if hw:
        r = run(base + ["-c:v", "h264_videotoolbox", "-b:v", bitrate] + tail + [str(out)])
        if r.returncode == 0 and Path(out).exists():
            return True, ""
    r = run(base + ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]
            + tail + [str(out)])
    return r.returncode == 0, r.stderr[-300:]
