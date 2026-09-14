"""Only writer of library metadata and review choices."""
import hashlib
import json
import os
import stat
import threading
from pathlib import Path
from .. import db, storage

CHOICES = {'keep', 'maybe', 'pass', 'unreviewed'}

def local_file(path):
    p = Path(path)
    if not p.is_absolute() or any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError('Preview must be a local regular file without links')
    s = p.stat()
    if not stat.S_ISREG(s.st_mode) or storage.is_cloud_only(s):
        raise ValueError('File is not locally available')
    return s

class Store:
    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser().absolute()
        if self.workspace.resolve() == (Path.home()/'SavvySelects').resolve():
            raise ValueError('Use a separate preview workspace')
        if any(p.is_symlink() for p in (self.workspace,*self.workspace.parents)):
            raise ValueError('Workspace cannot use links')
        self.workspace.mkdir(parents=True, exist_ok=True)
        if (self.workspace/'selects.db').is_symlink():
            raise ValueError('Database cannot be a link')
        self.lock = threading.RLock()
        self.con = db.connect({'work_dir':str(self.workspace)})
        self.con.executescript('''CREATE TABLE IF NOT EXISTS library_items
          (id TEXT PRIMARY KEY, source_path TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS library_choices
          (id TEXT PRIMARY KEY, choice TEXT NOT NULL CHECK(choice IN ('keep','maybe','pass','unreviewed')));''')
        self.con.commit()

    def upsert_items(self, items):
        with self.lock, self.con:
            for item in items:
                p = item['source_path']
                if not isinstance(p,str) or not Path(p).is_absolute() or '\0' in p:
                    raise ValueError('Invalid source path')
                ident = hashlib.sha256(p.encode()).hexdigest()[:24]
                old = self.get_item(ident)
                data = dict(item, id=ident, name=Path(p).name)
                for key in ('preview_path','poster_path','duration','preview_has_audio','preview_status','preview_provenance'):
                    if old and key in old: data[key]=old[key]
                data.pop('choice',None)
                self.con.execute('INSERT INTO library_items VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(ident,p,json.dumps(data)))

    def list_items(self):
        with self.lock:
            return [dict(json.loads(r['data']),choice=r['choice'] or 'unreviewed') for r in self.con.execute('SELECT data,choice FROM library_items LEFT JOIN library_choices USING(id) ORDER BY source_path')]

    def get_item(self, ident):
        with self.lock:
            r=self.con.execute('SELECT data,choice FROM library_items LEFT JOIN library_choices USING(id) WHERE id=?',(ident,)).fetchone()
            return dict(json.loads(r['data']),choice=r['choice'] or 'unreviewed') if r else None

    def set_choice(self, ident, choice):
        if not isinstance(choice,str) or choice not in CHOICES or not self.get_item(ident): raise ValueError('Unknown item or choice')
        with self.lock,self.con:
            self.con.execute('INSERT INTO library_choices VALUES(?,?) ON CONFLICT(id) DO UPDATE SET choice=excluded.choice',(ident,choice))
        return self.get_item(ident)

    def set_preview(self, ident, preview_path, poster_path=None, duration=None, has_audio=None, provenance='generated'):
        if provenance not in {'generated','verified_reuse'}:
            raise ValueError('Unknown preview provenance')
        item=self.get_item(ident)
        if not item: raise ValueError('Unknown item')
        for p in filter(None,(preview_path,poster_path)):
            local_file(p)
            if self.con.execute('SELECT 1 FROM library_items WHERE source_path=?',(str(p),)).fetchone() or Path(p).resolve()==Path(item['source_path']).resolve(): raise ValueError('Original cannot be served as preview')
            if provenance!='verified_reuse' and not Path(p).resolve().is_relative_to((self.workspace/'previews').resolve()): raise ValueError('Preview outside workspace')
        item.update(preview_path=str(preview_path),poster_path=str(poster_path) if poster_path else None,duration=duration,preview_has_audio=has_audio,preview_status='ready',preview_provenance=provenance)
        item.pop('choice',None)
        with self.lock,self.con:
            self.con.execute('UPDATE library_items SET data=? WHERE id=?',(json.dumps(item),ident))
        return self.get_item(ident)

    def media_path(self, ident, poster=False):
        item=self.get_item(ident)
        if not item: raise ValueError('Unknown item')
        p=item.get('poster_path' if poster else 'preview_path')
        if not p: raise ValueError('No preview yet')
        local_file(p)
        if self.con.execute('SELECT 1 FROM library_items WHERE source_path=?',(str(p),)).fetchone() or Path(p).resolve()==Path(item['source_path']).resolve(): raise ValueError('Original serving denied')
        if item.get('preview_provenance')!='verified_reuse' and not Path(p).resolve().is_relative_to((self.workspace/'previews').resolve()): raise ValueError('Outside preview workspace')
        return Path(p)

    def close(self): self.con.close()
