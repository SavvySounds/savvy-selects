import csv
import http.client
import json
import threading
import sqlite3
from pathlib import Path
import pytest
from savvy.library.store import Store
from savvy.library.server import make_server
from savvy.library.import_catalog import import_catalog

@pytest.fixture
def library(tmp_path):
    s=Store(tmp_path/'work');yield s;s.close()

def add(s,**kw):
    s.upsert_items([dict(source_path='/abs/original.mov',event='Example',kind='video',source_status='unverified',**kw)])
    return s.list_items()[0]['id']

def test_choices_survive_import_and_reopen(library,tmp_path):
    i=add(library);library.set_choice(i,'keep');add(library)
    other=Store(library.workspace)
    assert other.get_item(i)['choice']=='keep';other.close()
    assert library.con.execute('SELECT count(*) FROM media').fetchone()[0]==0
    with pytest.raises(ValueError):library.set_choice(i,[])
    with pytest.raises(ValueError):library.set_choice('missing','keep')

def test_preview_boundaries_and_rerun(library,tmp_path):
    i=add(library);outside=tmp_path/'outside.mp4';outside.write_bytes(b'0123456789')
    with pytest.raises(ValueError):library.set_preview(i,outside)
    library.set_preview(i,outside,provenance='verified_reuse');add(library)
    assert library.media_path(i)==outside
    link=library.workspace/'link';link.symlink_to(outside)
    with pytest.raises(ValueError):library.set_preview(i,link,provenance='verified_reuse')
    library.upsert_items([dict(source_path=str(outside),event='Original')])
    with pytest.raises(ValueError):library.media_path(i)

def test_import_never_opens_sources(library,tmp_path,monkeypatch):
    inventory=tmp_path/'inventory.csv'
    with inventory.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['path','kind','cloud_only']);w.writeheader();w.writerow({'path':'/abs/cloud.mov','kind':'video','cloud_only':'True'})
    assert import_catalog(library,inventory)=={'imported':1,'reused':0}
    assert library.list_items()[0]['source_status']=='cloud_only'

def test_http_ranges_origin_and_source_denial(library,tmp_path):
    i=add(library);p=library.workspace/'previews'/'one.mp4';p.parent.mkdir();p.write_bytes(b'0123456789');library.set_preview(i,p)
    srv=make_server(library,0);t=threading.Thread(target=srv.serve_forever,daemon=True);t.start();port=srv.server_address[1]
    def req(method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=3);c.request(method,path,body,headers or {});r=c.getresponse();v=(r.status,dict(r.getheaders()),r.read());c.close();return v
    try:
        health=req('GET','/api/health')
        assert health[0]==200 and json.loads(health[2])=={'app':'savvy-preview-library','workspace':str(library.workspace.resolve())}
        assert req('GET','/api/health',headers={'Host':'evil.test'})[0]==403
        r=req('GET','/preview/'+i,headers={'Range':'bytes=-3'});assert r[0]==206 and r[2]==b'789' and r[1]['Content-Range']=='bytes 7-9/10'
        assert req('GET','/preview/'+i,headers={'Range':'bytes=10-'})[0]==416
        assert req('GET','/preview/'+i,headers={'Range':'bytes=1-2,4-5'})[0]==416
        assert req('GET','/abs/original.mov')[0]==404
        assert req('GET','/api/library',headers={'Host':'evil.test'})[0]==403
        payload=json.dumps({'id':i,'choice':'keep'})
        assert req('POST','/api/choice',payload)[0]==403
        assert req('POST','/api/choice',payload,{'Origin':f'http://127.0.0.1:{port}'})[0]==200
        assert library.get_item(i)['choice']=='keep'
        assert req('POST','/api/choice','[]',{'Origin':f'http://127.0.0.1:{port}'})[0]==400
        assert req('GET','/api/keeps')[2].decode().count('/abs/original.mov')==1
    finally:srv.shutdown();srv.server_close();t.join()

def test_cloud_guard_rejects_before_open(library,tmp_path,monkeypatch):
    from savvy.library import store
    i=add(library);p=tmp_path/'proxy.mp4';p.write_bytes(b'content')
    monkeypatch.setattr(store.storage,'is_cloud_only',lambda st:True)
    with pytest.raises(ValueError):library.set_preview(i,p,provenance='verified_reuse')

@pytest.mark.parametrize('mutation', ['choices','confinement','rerun'])
def test_protection_checks_reject_isolated_mutants(tmp_path,mutation):
    """Run changed writer code only against throwaway databases, never live data."""
    from savvy.library import store as module
    source=Path(module.__file__).read_text()
    replacements={
        'choices':("if not isinstance(choice,str) or choice not in CHOICES or not self.get_item(ident): raise ValueError('Unknown item or choice')", "if False: raise ValueError('Unknown item or choice')"),
        'confinement':("if provenance!='verified_reuse' and not Path(p).resolve().is_relative_to((self.workspace/'previews').resolve()):", "if False:"),
        'rerun':("if old and key in old: data[key]=old[key]", "if False: data[key]=old[key]")}
    old,new=replacements[mutation];assert source.count(old)==1
    def proof(cls,folder):
        s=cls(folder)
        try:
            ident=add(s)
            if mutation=='choices':
                # Rejected at boundary, not by a SQL constraint after the fact.
                with pytest.raises(ValueError):s.set_choice(ident,'invalid')
            elif mutation=='confinement':
                outside=tmp_path/'outside.mov';outside.write_bytes(b'preview')
                with pytest.raises(ValueError):s.set_preview(ident,outside)
            else:
                p=s.workspace/'previews'/'p.mp4';p.parent.mkdir();p.write_bytes(b'preview');s.set_preview(ident,p);add(s)
                assert s.get_item(ident).get('preview_path')==str(p)
        finally:s.close()
    proof(Store,tmp_path/'green')
    namespace={'__package__':'savvy.library'};exec(compile(source.replace(old,new),'<isolated-library-mutant>','exec'),namespace)
    with pytest.raises((AssertionError,pytest.fail.Exception,sqlite3.IntegrityError)):proof(namespace['Store'],tmp_path/'mutant')

@pytest.mark.parametrize('disconnect', [BrokenPipeError, ConnectionResetError, OSError])
def test_stream_disconnect_never_sends_second_response(library,disconnect):
    """Navigation can cancel a video after its successful response starts."""
    ident=add(library)
    p=library.workspace/'previews'/'cancel.mp4';p.parent.mkdir();p.write_bytes(b'preview bytes')
    library.set_preview(ident,p)
    server=make_server(library,0)
    try:
        handler=object.__new__(server.RequestHandlerClass)
        handler.server=server;handler.command='GET';handler.path='/preview/'+ident
        handler.headers={'Host':f'127.0.0.1:{server.server_address[1]}'}
        statuses=[]
        handler.send_response=lambda code:statuses.append(code)
        handler.send_header=lambda *args:None
        handler.end_headers=lambda:None
        class Gone:
            def write(self,chunk):raise disconnect('viewer navigated away')
        handler.wfile=Gone();handler.close_connection=False
        handler.do_GET()
        assert statuses==[200]
        assert handler.close_connection
    finally:server.server_close()
