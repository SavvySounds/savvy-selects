"""Loopback-only library. Originals are never HTTP resources."""
import csv
import io
import json
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .store import local_file
from .. import storage


def public_item(store,item):
    item=dict(item)
    item.update(preview_url=None,poster_url=None)
    item.setdefault('preview_status','not_prepared')
    for poster,key in ((False,'preview_url'),(True,'poster_url')):
        try:
            store.media_path(item['id'],poster)
            item[key]=('/poster/' if poster else '/preview/')+item['id']
        except (OSError,ValueError): pass
    if item.get('preview_path') and not item['preview_url']: item['preview_status']='unavailable'
    return item


def make_server(store,port=8421):
    prepare_lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args): pass
        def send(self,body,code=200,ctype='application/json',extra=None):
            if not isinstance(body,bytes): body=json.dumps(body).encode()
            self.send_response(code)
            for k,v in {'Content-Type':ctype,'Content-Length':str(len(body)),'X-Content-Type-Options':'nosniff','Cache-Control':'no-store',**(extra or {})}.items():self.send_header(k,v)
            self.end_headers()
            if self.command!='HEAD':self.wfile.write(body)
        def trusted(self,mutation=False):
            port=self.server.server_address[1]
            hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
            host=self.headers.get('Host')
            return host in hosts and (not mutation or self.headers.get('Origin')=='http://'+host)
        def do_HEAD(self):self.do_GET()
        def do_GET(self):
            if not self.trusted():return self.send({'error':'Local host required'},403)
            path=urlsplit(self.path).path
            if path=='/':return self.send((Path(__file__).parent/'index.html').read_bytes(),ctype='text/html; charset=utf-8')
            if path=='/api/health':
                return self.send({'app':'savvy-preview-library','workspace':str(store.workspace.resolve())})
            if path=='/api/library':
                items=[public_item(store,x) for x in store.list_items()]
                return self.send({'items':items,'events':sorted({x.get('event','') for x in items}),'summary':{'total':len(items),'ready':sum(bool(x['preview_url']) for x in items),**{c:sum(x['choice']==c for x in items) for c in ('keep','maybe','pass')}}})
            if path=='/api/keeps':
                f=io.StringIO();w=csv.writer(f);w.writerow(['event','name','source_path','choice'])
                for x in store.list_items():
                    if x['choice']=='keep':w.writerow(["'"+str(x.get(k,'')) if str(x.get(k,'')).startswith(('=','+','-','@','\t','\r')) else x.get(k,'') for k in ('event','name','source_path','choice')])
                return self.send(f.getvalue().encode(),ctype='text/csv; charset=utf-8',extra={'Content-Disposition':'attachment; filename="savvy-keeps.csv"'})
            m=re.fullmatch(r'/(preview|poster)/([a-f0-9]{24})',path)
            if not m:return self.send({'error':'Not found'},404)
            response_started=False
            try:
                p=store.media_path(m[2],m[1]=='poster');s=local_file(p)
                fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as f:
                    actual=os.fstat(f.fileno())
                    if storage.is_cloud_only(actual) or (actual.st_ino,actual.st_dev,actual.st_size)!=(s.st_ino,s.st_dev,s.st_size):raise ValueError('Preview changed')
                    start,end,code=0,s.st_size-1,200
                    if self.headers.get('Range'):
                        rng=re.fullmatch(r'bytes=(\d*)-(\d*)',self.headers['Range'])
                        if not rng or not any(rng.groups()):raise IndexError
                        if not rng[1]:
                            if int(rng[2])==0:raise IndexError
                            start=max(0,s.st_size-int(rng[2]))
                        else:
                            start=int(rng[1]);end=min(end,int(rng[2])) if rng[2] else end
                        if start>end or start>=s.st_size:raise IndexError
                        code=206
                    response_started=True
                    self.send_response(code)
                    headers={'Content-Type':mimetypes.guess_type(p)[0] or 'application/octet-stream','Content-Length':str(max(0,end-start+1)),'Accept-Ranges':'bytes','X-Content-Type-Options':'nosniff'}
                    if code==206:headers['Content-Range']=f'bytes {start}-{end}/{s.st_size}'
                    for k,v in headers.items():self.send_header(k,v)
                    self.end_headers()
                    if self.command=='HEAD':return
                    f.seek(start);remaining=end-start+1
                    while remaining>0:
                        chunk=f.read(min(262144,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
            except IndexError:return self.send({'error':'Invalid range'},416,extra={'Content-Range':f'bytes */{s.st_size}'})
            except (BrokenPipeError,ConnectionResetError):
                self.close_connection=True
                return
            except (OSError,ValueError):
                if response_started:
                    self.close_connection=True
                    return
                return self.send({'error':'Preview unavailable'},404)
        def do_POST(self):
            if not self.trusted(True):self.close_connection=True;return self.send({'error':'Same-origin local request required'},403)
            try:
                if self.headers.get('Transfer-Encoding'):raise ValueError('Unsupported request encoding')
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=4096:raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(n))
                if not isinstance(data,dict) or not isinstance(data.get('id'),str):raise ValueError('Invalid item')
                item=store.get_item(data['id'])
                if not item:raise ValueError('Unknown item')
                if self.path=='/api/choice':item=store.set_choice(data['id'],data.get('choice'))
                elif self.path=='/api/prepare':
                    if not prepare_lock.acquire(False):return self.send({'error':'A preview is already being prepared'},409)
                    try:
                        from .prepare import prepare_item
                        allowed=[]
                        if item.get('preview_provenance')=='verified_reuse':
                            store.media_path(item['id'])
                            item['reuse_proxy_path']=item['preview_path']
                            allowed=[item['preview_path']]
                        result=prepare_item(store.workspace,item,allow_proxy_paths=allowed)
                        if result.get('preview_state')!='ready':raise ValueError(result.get('error') or result.get('message') or result.get('preview_state','Preview unavailable'))
                        item=store.set_preview(item['id'],result['preview_path'],result.get('poster_path'),result.get('duration'),result.get('has_audio'),provenance='verified_reuse' if result['preview_path'] in allowed else 'generated')
                    finally:prepare_lock.release()
                else:return self.send({'error':'Not found'},404)
                return self.send({'item':public_item(store,item),'success':True})
            except (ValueError,TypeError,KeyError,OSError) as e:
                self.close_connection=True;return self.send({'error':str(e)},400)
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)
