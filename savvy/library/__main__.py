import argparse
import json
from .store import Store
from .import_catalog import import_catalog
from .server import make_server

def main():
    p=argparse.ArgumentParser(description='Local preview library')
    p.add_argument('--workspace',default='~/SavvyPreviewLibrary')
    sub=p.add_subparsers(dest='command',required=True)
    i=sub.add_parser('import');i.add_argument('--inventory',required=True);i.add_argument('--recovered');i.add_argument('--existing-previews')
    s=sub.add_parser('serve');s.add_argument('--port',type=int,default=8421)
    a=p.parse_args();store=Store(a.workspace)
    try:
        if a.command=='import':print(json.dumps(import_catalog(store,a.inventory,a.recovered,a.existing_previews)))
        else:
            server=make_server(store,a.port)
            print(f'Preview library: http://127.0.0.1:{server.server_address[1]}',flush=True)
            try:server.serve_forever()
            finally:server.server_close()
    finally:store.close()
if __name__=='__main__':main()
