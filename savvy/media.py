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


def preview_info(path, timeout=20):
    """Probe real streams; a suffix or successful ffprobe exit isn't a video."""
    import json
    if Path(path).suffix.lower() in {".heic", ".heif"}:
        return _heic_info(path, timeout)
    r = run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)], timeout=timeout)
    if r.returncode:
        raise ValueError("Media could not be read")
    data = json.loads(r.stdout)
    video = next((s for s in data.get("streams", [])
                  if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if not video or not video.get("width") or not video.get("height"):
        raise ValueError("No playable video or image stream")
    return {"width": video["width"], "height": video["height"],
            "duration": float(data.get("format", {}).get("duration", 0) or 0),
            "has_audio": any(s.get("codec_type") == "audio"
                             for s in data.get("streams", [])),
            "codec": video.get("codec_name")}


def build_library_video(src, out, max_bytes, timeout=120):
    """Whole-file, aspect-preserving preview, with actual source audio if any."""
    import time
    out = Path(out)
    if out.exists():
        raise ValueError("Preview output already exists")
    deadline = time.monotonic() + timeout
    # Bound both dimensions, keeping portrait material portrait and avoiding upscaling.
    vf = "scale=w='min(854,iw)':h='min(480,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
    base = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-threads", "2",
            "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
            "-map_metadata", "-1", "-movflags", "+faststart", "-fs", str(max_bytes)]
    for codec in (["-c:v", "h264_videotoolbox", "-b:v", "750k"],
                  ["-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "29"]):
        try:
            r = run(base + codec + [str(out)], timeout=max(.1, deadline-time.monotonic()))
            if r.returncode == 0 and out.is_file():
                return
        except subprocess.TimeoutExpired:
            out.unlink(missing_ok=True)
            raise
        out.unlink(missing_ok=True)
    raise ValueError("Preview could not be encoded")


def build_library_poster(src, out, *, photo=False, timeout=30):
    """Make a browser JPEG without modifying the input or adding image metadata."""
    out = Path(out)
    if out.exists():
        raise ValueError("Poster output already exists")
    if photo and Path(src).suffix.lower() in {".heic", ".heif"}:
        _heic_poster(src, out, timeout)
        return
    base = ["ffmpeg", "-v", "error", "-nostdin", "-n"]
    if not photo:
        base += ["-ss", "0"]
    r = run(base + ["-i", str(src), "-map", "0:v:0", "-frames:v", "1",
                    "-vf", "scale=w='min(854,iw)':h='min(480,ih)':force_original_aspect_ratio=decrease",
                    "-q:v", "4", "-map_metadata", "-1", str(out)], timeout=timeout)
    if r.returncode or not out.is_file():
        out.unlink(missing_ok=True)
        raise ValueError("Photo or poster could not be decoded")
    with Image.open(out) as img:
        img.verify()


def _local_photo(path):
    import stat
    from . import storage
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Linked photo paths are not allowed")
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or not st.st_size or storage.is_cloud_only(st):
        raise ValueError("Photo is unavailable locally; no download started")
    return st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino


def _heic_info(path, timeout):
    import re
    before = _local_photo(path)
    result = run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], timeout=timeout)
    values = dict(re.findall(r"(pixelWidth|pixelHeight):\s*(\d+)", result.stdout))
    if result.returncode or len(values) != 2 or _local_photo(path) != before:
        raise ValueError("Could not read the complete HEIC image dimensions")
    width, height = int(values['pixelWidth']), int(values['pixelHeight'])
    if min(width, height) <= 0:
        raise ValueError("HEIC image has no usable dimensions")
    return {'width': width, 'height': height, 'duration': 0.0,
            'has_audio': False, 'codec': 'heic-primary-image'}


def _heic_poster(src, out, timeout):
    import tempfile
    from PIL import ImageOps
    before = _local_photo(src)
    # HEIC can expose many tiles as video streams. ImageIO via sips assembles the
    # primary image; ffmpeg's first stream can successfully decode only one tile.
    with tempfile.TemporaryDirectory(prefix='.heic-', dir=Path(out).parent) as tmp:
        jpeg = Path(tmp) / 'primary.jpg'
        result = run(['/usr/bin/sips', '-s', 'format', 'jpeg', str(src), '--out', str(jpeg)], timeout=timeout)
        if result.returncode or not jpeg.is_file() or _local_photo(src) != before:
            raise ValueError('HEIC conversion failed or source changed')
        with Image.open(jpeg) as full:
            img = ImageOps.exif_transpose(full).convert('RGB')
            img.thumbnail((854, 480), Image.Resampling.LANCZOS)
            with Path(out).open('xb') as target:
                img.save(target, format='JPEG', quality=88)
    if _local_photo(src) != before:
        Path(out).unlink(missing_ok=True)
        raise ValueError('Photo changed during preview preparation')
