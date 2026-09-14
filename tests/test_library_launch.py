import io
import json
from urllib.error import URLError, HTTPError
import pytest
from savvy.library import launch


def test_health_identifies_exact_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps({'app':'savvy-preview-library','workspace':str(tmp_path)}).encode()))
    assert launch.status('http://127.0.0.1:8421',tmp_path)=='ready'
    assert launch.status('http://127.0.0.1:8421',tmp_path/'different')=='occupied'
    def refused(*a, **k):raise URLError(ConnectionRefusedError())
    monkeypatch.setattr(launch,'urlopen',refused)
    assert launch.status('http://127.0.0.1:8421',tmp_path)=='stopped'


def test_existing_library_opens_without_starting_another(tmp_path, monkeypatch):
    monkeypatch.setattr(launch,'status',lambda *a:'ready')
    monkeypatch.setattr(launch.subprocess,'Popen',lambda *a,**k:pytest.fail('started duplicate server'))
    opened=[];monkeypatch.setattr(launch.webbrowser,'open',opened.append)
    assert launch.open_library(tmp_path) is None
    assert opened==['http://127.0.0.1:8421']


def test_wrong_server_is_left_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(launch,'status',lambda *a:'occupied')
    monkeypatch.setattr(launch.subprocess,'Popen',lambda *a,**k:pytest.fail('started server on occupied port'))
    monkeypatch.setattr(launch.webbrowser,'open',lambda *a:pytest.fail('opened wrong app'))
    with pytest.raises(RuntimeError,match='left untouched'):launch.open_library(tmp_path)


def test_stopped_library_starts_with_correct_workspace(tmp_path,monkeypatch):
    states=iter(['stopped','ready']);monkeypatch.setattr(launch,'status',lambda *a:next(states))
    class Child:
        def poll(self):return None
    calls=[]
    def start(args,**kwargs):calls.append(args);return Child()
    monkeypatch.setattr(launch.subprocess,'Popen',start)
    monkeypatch.setattr(launch.webbrowser,'open',lambda *a:True)
    assert isinstance(launch.open_library(tmp_path),Child)
    assert calls[0][-5:]==['--workspace',str(tmp_path),'serve','--port','8421']
