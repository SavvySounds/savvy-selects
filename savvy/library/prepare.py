"""One attended preview at a time. Source files are never written or downloaded."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading

from savvy import media, storage

FREE_FLOOR = 20 << 30
CACHE_BUDGET = 5 << 30
_LOCK = threading.Lock()
_PHOTOS = {'.jpg', '.jpeg', '.png', '.heic', '.heif'}
_VIDEOS = {'.mov', '.mp4', '.m4v', '.avi', '.mkv', '.mts', '.m2ts', '.webm'}


class Parked(ValueError):
    def __init__(self, state, message):
        self.state = state
        super().__init__(message)


def _no_links(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for part in (path, *path.parents):
        try:
            if stat.S_ISLNK(part.lstat().st_mode):
                raise Parked('error', 'A linked folder or file cannot be used here')
        except FileNotFoundError:
            continue
    return path


def _source(path):
    path = _no_links(path)
    try:
        st = path.lstat()
    except FileNotFoundError:
        raise Parked('missing', 'Connect the source drive; the original is unavailable')
    if not stat.S_ISREG(st.st_mode) or not st.st_size:
        raise Parked('unsupported', 'This is not a nonempty regular media file')
    if storage.is_cloud_only(st):
        raise Parked('cloud_only', 'Original is online only; no download was started')
    return {'bytes': st.st_size, 'mtime_ns': st.st_mtime_ns,
            'device': st.st_dev, 'inode': st.st_ino}


def _cache_size(root):
    total = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = Path(folder) / name
            _no_links(p)
            if p.is_file():
                total += p.stat().st_size
    return total


def _room(root):
    allowance = min(CACHE_BUDGET - _cache_size(root), storage.free_bytes(root) - FREE_FLOOR)
    if allowance < 2 << 20:
        raise Parked('error', 'Preview cache is full or less than 20 GiB would remain free')
    return allowance


def _verify(path, expected, photo):
    _source(path)
    info = media.preview_info(path)
    if not photo:
        if info['codec'] != 'h264' or expected['duration'] <= 0:
            raise ValueError('Preview is not playable H.264 video')
        if abs(info['duration'] - expected['duration']) > max(.25, expected['duration'] * .005):
            raise ValueError('Preview does not contain the full recording')
        if info['has_audio'] != expected['has_audio']:
            raise ValueError('Preview audio does not match its input')
    return info


def prepare_item(workdir, item, *, dry_run=False, allow_proxy_paths=()):
    """Return media paths/status only; the library store alone saves item state."""
    try:
        if item.get('reuse_proxy_path'):
            return _reuse_preview(workdir, item['reuse_proxy_path'], allow_proxy_paths, dry_run)
        src = _no_links(item.get('canonical_source_path') or item['source_path'])
        suffix = src.suffix.lower()
        if suffix in {'.insv', '.insp', '.lrv'} or src.name.upper().startswith('LRV_'):
            raise Parked('needs_reframe', '360 originals and camera companions are preserved for framing')
        lane = str(item.get('lane', '')).lower()
        protected = ('protect:', 'preserve:', 'separate:')
        if lane.startswith(protected) or any(p in str(src) for p in ('/Volumes/Insta360 X5', '/_BACKUP/')) or any(p.lower() in {'pioneer', 'contents', 'engine library', 'music', 'djay', '_serato_'} or p.lower().endswith('.photoslibrary') for p in src.parts):
            raise Parked('protected', 'This card, library, companion or backup material is preserved; use the primary media copy')
        if suffix not in _PHOTOS | _VIDEOS:
            raise Parked('unsupported', 'No preview for this format; original is preserved')
        before = _source(src)
        expected_size = item.get('source_size', item.get('bytes'))
        if expected_size is not None and int(expected_size) != before['bytes']:
            raise Parked('source_changed', 'Original size changed since the archive map; refresh it first')
        if item.get('source_mtime_ns') is not None and int(item['source_mtime_ns']) != before['mtime_ns']:
            raise Parked('source_changed', 'Original changed since the archive map; refresh it first')
        root = _no_links(Path(workdir).expanduser() / 'previews')
        if src == root or root in src.parents:
            raise Parked('error', 'A preview cannot be used as an original')
        if dry_run:
            return {'preview_state': 'available', 'message': 'Local source checked; no media opened or outputs written'}
        root.mkdir(parents=True, exist_ok=True)
        _no_links(root)
        if not _LOCK.acquire(blocking=False):
            raise Parked('error', 'Another preview is being prepared; try again when it finishes')
        try:
            lockpath = root / '.prepare.lock'
            _no_links(lockpath)
            with lockpath.open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Parked('error', 'Another preview is being prepared')
                return _prepare_locked(root, src, before, item, allow_proxy_paths)
        finally:
            _LOCK.release()
    except Parked as e:
        return {'preview_state': e.state, 'message': str(e)}
    except (OSError, ValueError, KeyError, TimeoutError) as e:
        return {'preview_state': 'error', 'message': str(e)[:300]}
    except Exception as e:
        # Includes subprocess timeout: leave an actionable state, never a ready badge.
        return {'preview_state': 'error', 'message': str(e)[:300]}


def _prepare_locked(root, src, before, item, allow_proxy_paths):
    photo = src.suffix.lower() in _PHOTOS
    key = hashlib.sha256((str(src) + json.dumps(before, sort_keys=True)).encode()).hexdigest()[:24]
    dest = root / key
    _no_links(dest)
    if dest.exists():
        receipt_path = dest / 'receipt.json'
        _source(receipt_path)
        receipt = json.loads(receipt_path.read_text())
        if receipt['source'] != before:
            raise ValueError('Existing preview refers to a different original')
        info = _verify(dest / ('poster.jpg' if photo else 'preview.mp4'), receipt['input_info'], photo)
        _verify(dest / 'poster.jpg', {}, True)
        if _source(src) != before:
            raise Parked('source_changed', 'Original changed during preview validation')
        return _result(dest, info, photo, receipt['provenance'])
    allowance = _room(root)
    render_src = src
    provenance = 'generated'
    input_before = _source(render_src)
    input_info = media.preview_info(render_src)
    if not photo and input_info['duration'] <= 0:
        raise Parked('unsupported', 'No playable recording duration was found')
    with tempfile.TemporaryDirectory(prefix='.preparing-', dir=root) as temp:
        temp = Path(temp)
        if _source(render_src) != input_before:
            raise Parked('source_changed', 'Source changed before decoding; refresh it first')
        if photo:
            media.build_library_poster(render_src, temp / 'poster.jpg', photo=True)
            info = _verify(temp / 'poster.jpg', input_info, True)
        else:
            media.build_library_video(render_src, temp / 'preview.mp4', max_bytes=allowance-(1 << 20))
            info = _verify(temp / 'preview.mp4', input_info, False)
            media.build_library_poster(temp / 'preview.mp4', temp / 'poster.jpg')
        if _source(src) != before or _source(render_src) != input_before:
            raise Parked('source_changed', 'Source changed during preparation; preview was discarded')
        if storage.free_bytes(root) < FREE_FLOOR or _cache_size(root) > CACHE_BUDGET:
            raise Parked('error', 'Preview exceeded the cache allowance and was discarded')
        (temp / 'receipt.json').write_text(json.dumps({'source': before, 'input_info': input_info,
                                                     'provenance': provenance}, sort_keys=True))
        if dest.exists():
            raise ValueError('Preview destination already exists')
        temp.rename(dest)
    return _result(dest, info, photo, provenance)


def _result(dest, info, photo, provenance):
    return {'preview_state': 'ready', 'preview_path': str(dest / ('poster.jpg' if photo else 'preview.mp4')),
            'poster_path': str(dest / 'poster.jpg'), 'duration': None if photo else info['duration'],
            'width': info['width'], 'height': info['height'],
            'has_audio': False if photo else info['has_audio'], 'provenance': provenance}


def _reuse_preview(workdir, path, allowed, dry_run):
    """Make only a poster for a specifically approved existing local preview."""
    src = _no_links(path)
    if src not in {_no_links(p) for p in allowed}:
        raise Parked('error', 'Existing preview was not explicitly allowed')
    before = _source(src)
    if dry_run:
        return {'preview_state': 'available', 'message': 'Existing local preview checked; no media opened'}
    root = _no_links(Path(workdir).expanduser() / 'previews')
    root.mkdir(parents=True, exist_ok=True)
    if not _LOCK.acquire(blocking=False):
        raise Parked('error', 'Another preview is being prepared')
    try:
        lockpath = _no_links(root / '.prepare.lock')
        with lockpath.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = media.preview_info(src)
            if info['duration'] <= 0 or info['codec'] != 'h264':
                raise Parked('unsupported', 'Existing preview is not playable H.264 video')
            key = 'reuse-' + hashlib.sha256((str(src) + json.dumps(before, sort_keys=True)).encode()).hexdigest()[:24]
            dest = _no_links(root / key)
            if dest.exists():
                _verify(dest / 'poster.jpg', {}, True)
            else:
                _room(root)
                with tempfile.TemporaryDirectory(prefix='.preparing-', dir=root) as temp:
                    temp = Path(temp)
                    media.build_library_poster(src, temp / 'poster.jpg')
                    _verify(temp / 'poster.jpg', {}, True)
                    if _source(src) != before:
                        raise Parked('source_changed', 'Existing preview changed; poster discarded')
                    if storage.free_bytes(root) < FREE_FLOOR or _cache_size(root) > CACHE_BUDGET:
                        raise Parked('error', 'Preview cache allowance exceeded')
                    if dest.exists():
                        raise Parked('error', 'Poster destination already exists')
                    temp.rename(dest)
            if _source(src) != before:
                raise Parked('source_changed', 'Existing preview changed during validation')
            result = _result(dest, info, False, 'verified_reuse')
            result['preview_path'] = str(src)
            return result
    finally:
        _LOCK.release()
