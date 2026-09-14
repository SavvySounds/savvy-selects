from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from savvy import media, storage


def test_heic_uses_primary_image_not_first_tile(tmp_path, monkeypatch):
    source = tmp_path/'image.HEIC';source.write_bytes(b'fake HEIC container')
    before=source.stat();calls=[]
    def native(cmd, **kwargs):
        calls.append(cmd)
        assert cmd[0]=='/usr/bin/sips', 'HEIC must not select ffmpeg first tile'
        if '-g' in cmd:return SimpleNamespace(returncode=0,stdout='pixelWidth: 1200\npixelHeight: 800\n')
        img=Image.new('RGB',(1200,800),'red')
        img.paste('blue',(600,0,1200,800));img.save(cmd[-1])
        return SimpleNamespace(returncode=0,stdout='')
    monkeypatch.setattr(media,'run',native)
    assert media.preview_info(source)['width']==1200
    out=tmp_path/'poster.jpg';media.build_library_poster(source,out,photo=True)
    with Image.open(out) as img:
        assert img.size==(720,480)
        assert img.getpixel((10,240))[0]>200
        assert img.getpixel((710,240))[2]>200
    assert (source.stat().st_size,source.stat().st_mtime_ns)==(before.st_size,before.st_mtime_ns)
    assert not list(tmp_path.glob('.heic-*'))
    with pytest.raises(ValueError,match='exists'):media.build_library_poster(source,out,photo=True)


def test_heic_placeholder_never_reaches_native_reader(tmp_path,monkeypatch):
    source=tmp_path/'image.heic';source.write_bytes(b'placeholder')
    monkeypatch.setattr(storage,'is_cloud_only',lambda st:True)
    monkeypatch.setattr(media,'run',lambda *a,**k:pytest.fail('opened placeholder'))
    with pytest.raises(ValueError,match='unavailable'):media.preview_info(source)
    with pytest.raises(ValueError,match='unavailable'):media.build_library_poster(source,tmp_path/'p.jpg',photo=True)


def test_failed_heic_conversion_cleans_temporary_files(tmp_path,monkeypatch):
    source=tmp_path/'image.heic';source.write_bytes(b'fake')
    monkeypatch.setattr(media,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=''))
    with pytest.raises(ValueError):media.build_library_poster(source,tmp_path/'p.jpg',photo=True)
    assert not (tmp_path/'p.jpg').exists()
    assert not list(tmp_path.glob('.heic-*'))
