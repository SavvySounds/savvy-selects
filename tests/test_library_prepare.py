"""Tiny synthetic media exercises the actual preview writer and refusal paths."""
from pathlib import Path
import pytest
from savvy import media
from savvy.library import prepare


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / 'portrait.mp4'
    r = media.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                   'color=c=blue:s=180x320:r=24:d=1.5', '-f', 'lavfi', '-i',
                   'sine=frequency=440:duration=1.5', '-c:v', 'libx264',
                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(p)], timeout=30)
    assert r.returncode == 0, r.stderr
    return p


def item(clip):
    return {'source_path': str(clip), 'bytes': clip.stat().st_size, 'kind': 'video'}


def test_full_portrait_audio_and_resumable_cache(tmp_path, clip):
    before = clip.stat()
    result = prepare.prepare_item(tmp_path / 'work', item(clip))
    assert result['preview_state'] == 'ready', result
    assert result['width'] < result['height'] <= 480
    assert result['has_audio'] is True
    assert abs(result['duration'] - 1.5) < .15
    assert Path(result['poster_path']).is_file()
    assert media.preview_info(result['preview_path'])['codec'] == 'h264'
    assert (clip.stat().st_size, clip.stat().st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    again = prepare.prepare_item(tmp_path / 'work', item(clip))
    assert again == result
    data = Path(result['preview_path']).read_bytes()
    assert data.index(b'moov') < data.index(b'mdat')


def test_no_media_open_for_cloud_missing_changed_or_360(tmp_path, clip, monkeypatch):
    monkeypatch.setattr(media, 'preview_info', lambda *a, **k: pytest.fail('opened protected media'))
    assert prepare.prepare_item(tmp_path/'w', {'source_path': str(tmp_path/'gone.mp4')})['preview_state'] == 'missing'
    assert prepare.prepare_item(tmp_path/'w', {'source_path': str(tmp_path/'x.insv')})['preview_state'] == 'needs_reframe'
    assert prepare.prepare_item(tmp_path/'w', {'source_path': str(tmp_path/'LRV_x.mp4')})['preview_state'] == 'needs_reframe'
    stale = item(clip); stale['bytes'] += 1
    assert prepare.prepare_item(tmp_path/'w', stale)['preview_state'] == 'source_changed'
    monkeypatch.setattr(prepare.storage, 'is_cloud_only', lambda st: True)
    assert prepare.prepare_item(tmp_path/'w', item(clip))['preview_state'] == 'cloud_only'
    assert not (tmp_path/'w').exists()


def test_failure_cleanup_and_source_change(tmp_path, clip, monkeypatch):
    def broken(src, out, **kw):
        Path(out).write_bytes(b'partial')
        raise ValueError('simulated failure')
    monkeypatch.setattr(media, 'build_library_video', broken)
    assert prepare.prepare_item(tmp_path/'w', item(clip))['preview_state'] == 'error'
    assert not list((tmp_path/'w'/'previews').glob('.preparing-*'))
    assert not list((tmp_path/'w'/'previews').glob('*/preview.mp4'))


def test_symlink_output_and_source_refused(tmp_path, clip):
    outside = tmp_path/'outside'; outside.mkdir()
    (tmp_path/'linked').symlink_to(outside, target_is_directory=True)
    assert prepare.prepare_item(tmp_path/'linked', item(clip))['preview_state'] == 'error'
    assert not list(outside.iterdir())
    alias = tmp_path/'alias.mp4'; alias.symlink_to(clip)
    assert prepare.prepare_item(tmp_path/'w', {'source_path': str(alias)})['preview_state'] == 'error'


def test_protected_raw_dry_run_and_space_floor(tmp_path, clip, monkeypatch):
    protected = item(clip); protected['lane'] = 'Protect: camera card'
    assert prepare.prepare_item(tmp_path/'w', protected)['preview_state'] == 'protected'
    assert prepare.prepare_item(tmp_path/'w', {'source_path': str(tmp_path/'raw.nef')})['preview_state'] == 'unsupported'
    assert prepare.prepare_item(tmp_path/'w', item(clip), dry_run=True)['preview_state'] == 'available'
    assert not (tmp_path/'w').exists()
    monkeypatch.setattr(prepare.storage, 'free_bytes', lambda root: 19 << 30)
    assert prepare.prepare_item(tmp_path/'w', item(clip))['preview_state'] == 'error'
    assert not list((tmp_path/'w'/'previews').glob('.preparing-*'))


def test_reuse_poster_only_no_original_open(tmp_path, clip, monkeypatch):
    monkeypatch.setattr(media, 'build_library_video', lambda *a, **k: pytest.fail('reencoded existing preview'))
    request = {'source_path': '/missing/original.mp4', 'reuse_proxy_path': str(clip)}
    assert prepare.prepare_item(tmp_path/'w', request)['preview_state'] == 'error'
    result = prepare.prepare_item(tmp_path/'w', request, allow_proxy_paths=[str(clip)])
    assert result['preview_state'] == 'ready', result
    assert result['preview_path'] == str(clip)
    assert result['provenance'] == 'verified_reuse'
    assert result['has_audio'] is True
    assert Path(result['poster_path']).is_file()


def test_audio_only_disguised_as_video_refused(tmp_path):
    src=tmp_path/'not-video.mp4'
    r=media.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=0.1','-c:a','aac',str(src)],timeout=20)
    assert r.returncode == 0
    assert prepare.prepare_item(tmp_path/'w',item(src))['preview_state']=='error'


def test_photo_gets_jpeg_preview(tmp_path):
    src = tmp_path/'still.png'
    r = media.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=80x120','-frames:v','1',str(src)],timeout=20)
    assert r.returncode == 0
    before = src.stat()
    result = prepare.prepare_item(tmp_path/'w', {'source_path':str(src),'kind':'photo'})
    assert result['preview_state'] == 'ready', result
    assert result['preview_path'] == result['poster_path']
    assert result['duration'] is None and result['has_audio'] is False
    assert result['width'] < result['height']
    assert (src.stat().st_size,src.stat().st_mtime_ns)==(before.st_size,before.st_mtime_ns)


def test_silent_reused_preview_reports_silence(tmp_path, clip):
    silent=tmp_path/'silent.mp4'
    r=media.run(['ffmpeg','-v','error','-i',str(clip),'-an','-c:v','copy',str(silent)],timeout=20)
    assert r.returncode == 0
    result=prepare.prepare_item(tmp_path/'w',{'source_path':'/missing/file','reuse_proxy_path':str(silent)},allow_proxy_paths=[silent])
    assert result['preview_state']=='ready', result
    assert result['has_audio'] is False


def test_changing_source_discards_preview(tmp_path, clip, monkeypatch):
    real = media.build_library_video
    def changed(src,out,**kw):
        real(src,out,**kw)
        import os
        st=Path(src).stat()
        os.utime(src,ns=(st.st_atime_ns,st.st_mtime_ns+1000000000))
    monkeypatch.setattr(media,'build_library_video',changed)
    result=prepare.prepare_item(tmp_path/'w',item(clip))
    assert result['preview_state']=='source_changed',result
    assert not list((tmp_path/'w'/'previews').glob('*/preview.mp4'))
    assert not list((tmp_path/'w'/'previews').glob('.preparing-*'))


def test_timeout_and_bad_cache_never_ready(tmp_path, clip, monkeypatch):
    import subprocess
    real=media.build_library_video
    monkeypatch.setattr(media,'build_library_video',lambda *a,**k: (_ for _ in ()).throw(subprocess.TimeoutExpired('ffmpeg',120)))
    assert prepare.prepare_item(tmp_path/'w',item(clip))['preview_state']=='error'
    assert not list((tmp_path/'w'/'previews').glob('.preparing-*'))
    monkeypatch.setattr(media,'build_library_video',real)
    ready=prepare.prepare_item(tmp_path/'w',item(clip))
    Path(ready['preview_path']).write_bytes(b'broken cached output')
    assert prepare.prepare_item(tmp_path/'w',item(clip))['preview_state']=='error'


def test_shared_media_can_be_privately_previewed(tmp_path, clip):
    shared = item(clip)
    shared['lane'] = 'Hold: shared footage'
    shared['source_label'] = 'Shared footage — public use unconfirmed'
    result = prepare.prepare_item(tmp_path/'w', shared)
    assert result['preview_state'] == 'ready', result
