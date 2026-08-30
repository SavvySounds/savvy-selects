"""BPM and first-beat detection for `savvy assemble`.

Priority (from ASSEMBLE_PLAN.md): a tag written by Mixed In Key / rekordbox /
Serato wins, then the --bpm / --offset CLI overrides, then a numpy estimate
over the track's low-sample-rate mono waveform. The estimate path calls
media.audio_samples() - no direct ffmpeg here (invariant #7).
"""
import numpy as np

from . import media

SR = 11025      # audio_samples() default, kept explicit here for the math
FRAME = 512     # envelope window, ~46 ms at SR
HOP = 256       # envelope hop, ~23 ms - fine enough lag resolution
MIN_BPM, MAX_BPM = 60.0, 200.0   # autocorrelation search window
CLAMP = (40.0, 240.0)            # never report an absurd number as bpm


def _envelope(x):
    """RMS energy per short window - the onset envelope the tempo lives in."""
    n_frames = (len(x) - FRAME) // HOP + 1
    if n_frames < 2:
        return np.zeros(1)
    view = np.lib.stride_tricks.as_strided(
        x, shape=(n_frames, FRAME),
        strides=(x.strides[0] * HOP, x.strides[0]))
    return np.sqrt(np.mean(view * view, axis=1))


def _autocorr(env):
    env = env - env.mean()
    n = len(env)
    corr = np.correlate(env, env, mode="full")[n - 1:]
    if corr[0] == 0:
        return corr
    return corr / corr[0]


def _tag_bpm(path):
    """The BPM a DJ tool already wrote into the file, or None."""
    try:
        from mutagen import File as mfile
        tag = mfile(str(path))
    except Exception:
        return None
    if tag is None:
        return None
    try:
        if "TBPM" in tag:                            # MP3 / ID3
            values = tag["TBPM"].text
        elif "BPM" in tag:                           # FLAC / Vorbis comments
            values = tag["BPM"]
        elif getattr(tag, "tags", None) and "tmpo" in tag.tags:  # MP4/M4A
            values = tag.tags["tmpo"]
        else:
            return None
    except (AttributeError, KeyError, TypeError):
        return None
    for v in values or []:
        try:
            return float(str(v).split()[0])
        except (ValueError, IndexError):
            continue
    return None


def detect_bpm(track_path, override=None) -> tuple[float, str]:
    """Returns (bpm, source) where source is 'override', 'tag', or 'estimated'."""
    if override is not None:
        return float(override), "override"
    bpm = _tag_bpm(track_path)
    if bpm:
        return bpm, "tag"
    return _estimate_bpm(track_path), "estimated"


def detect_offset(track_path, bpm, override=None) -> tuple[float, str]:
    """Returns (offset_seconds, source) - 'override' or 'estimated'."""
    if override is not None:
        return float(override), "override"
    return _estimate_offset(track_path, bpm), "estimated"


def beat_grid(bpm, offset, duration, beats_per_bar=4) -> list[float]:
    """Every beat timestamp in the track, offset -> duration."""
    if bpm <= 0 or duration <= 0:
        return []
    step = 60.0 / bpm
    times = []
    t = offset
    while t <= duration + 1e-9:
        times.append(round(t, 6))
        t += step
    return times


def _estimate_bpm(path):
    """Onset-envelope autocorrelation, peak lag -> BPM."""
    x = media.audio_samples(path, SR)
    corr = _autocorr(_envelope(x))
    lo = int(SR / HOP * 60.0 / MAX_BPM)
    hi = int(SR / HOP * 60.0 / MIN_BPM)
    seg = corr[lo:hi + 1]
    if len(seg) == 0 or seg.max() <= 0:
        return CLAMP[0]
    k = int(np.argmax(seg)) + lo
    # Parabolic interpolation: the coarse lag grid would otherwise quantise
    # the tempo to the envelope hop (~23 ms -> a few BPM of error).
    if 1 <= k < len(corr) - 1:
        denom = corr[k - 1] - 2 * corr[k] + corr[k + 1]
        if denom != 0:
            k += 0.5 * (corr[k - 1] - corr[k + 1]) / denom
    if k <= 0:
        return CLAMP[0]
    bpm = 60.0 / (k * HOP / SR)
    return min(max(bpm, CLAMP[0]), CLAMP[1])


def _estimate_offset(path, bpm):
    """First strong transient in the first ~8s, snapped to the nearest beat."""
    x = media.audio_samples(path, SR)
    env = _envelope(x[:int(SR * 8.0)])
    peak = env.max()
    if peak <= 0:
        return 0.0
    thr = peak * 0.5
    beat = 60.0 / bpm
    for i in range(len(env)):
        if env[i] < thr:
            continue
        prev = env[i - 1] if i > 0 else 0.0
        nxt = env[i + 1] if i < len(env) - 1 else 0.0
        if env[i] >= prev and env[i] >= nxt:
            return round(i * HOP / SR / beat) * beat
    return 0.0
