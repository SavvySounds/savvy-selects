"""Import explicit inventories; never walk or open original sources."""
import csv
import json
from pathlib import Path
from .store import local_file

def import_catalog(store,inventory,recovered=None,existing_previews=None):
    local_file(inventory)
    with open(inventory,newline='') as f:
        rows=list(csv.DictReader(f))
    items=[dict(source_path=r['path'],event=r.get('event_folder','Unknown event'),kind=r.get('kind','unknown'),source_label=r.get('source',''),source_status='cloud_only' if r.get('cloud_only','').lower()=='true' else 'unverified',lane=r.get('lane',''),bytes=int(r.get('bytes',0))) for r in rows]
    store.upsert_items(items)
    reuse=[]
    if recovered:
        local_file(recovered)
        with open(recovered,newline='') as f:
            reuse.extend(dict(source_path=r.get('src_path'),preview_path=r.get('proxy_path'),duration=r.get('duration')) for r in csv.DictReader(f))
    if existing_previews:
        local_file(existing_previews)
        values=json.loads(Path(existing_previews).read_text())
        if not isinstance(values,list):raise ValueError('Preview manifest must be a list')
        if any(not isinstance(v,dict) or not v.get('source_path') or not v.get('preview_path') for v in values):
            raise ValueError('Each existing preview needs source_path and preview_path')
        reuse.extend(values)
    bypath={x['source_path']:x for x in store.list_items()}
    reused=0
    for r in reuse:
        item=bypath.get(r.get('source_path'))
        if item and r.get('preview_path'):
            try:
                store.set_preview(item['id'],r['preview_path'],r.get('poster_path'),r.get('duration'),r.get('has_audio'),provenance='verified_reuse');reused+=1
            except (ValueError,OSError):continue
    return {'imported':len(items),'reused':reused}
