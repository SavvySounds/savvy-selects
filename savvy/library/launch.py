"""Open the attended local library without disturbing another app on its port."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser


def status(url, workspace):
    try:
        with urlopen(url + '/api/health', timeout=1) as response:
            data = json.load(response)
        if data == {'app': 'savvy-preview-library', 'workspace': str(Path(workspace).expanduser().resolve())}:
            return 'ready'
        return 'occupied'
    except HTTPError:
        return 'occupied'
    except URLError as error:
        return 'stopped' if isinstance(error.reason, ConnectionRefusedError) else 'occupied'
    except (OSError, ValueError):
        return 'occupied'


def open_library(workspace, port=8421):
    workspace = str(Path(workspace).expanduser().resolve())
    url = f'http://127.0.0.1:{port}'
    state = status(url, workspace)
    if state == 'occupied':
        raise RuntimeError('Another app is using the library address. It was left untouched.')
    child = None
    if state == 'stopped':
        child = subprocess.Popen([sys.executable, '-m', 'savvy.library', '--workspace', workspace,
                                  'serve', '--port', str(port)], cwd=Path(__file__).resolve().parents[2])
        for _ in range(40):
            if child.poll() is not None:
                raise RuntimeError('The library could not open. See the message in this window.')
            if status(url, workspace) == 'ready':
                break
            time.sleep(.2)
        else:
            child.terminate()  # Only the child this click started; never another server.
            child.wait(timeout=5)
            raise RuntimeError('The library took too long to open. Try opening it again.')
    webbrowser.open(url)
    return child


def main():
    parser = argparse.ArgumentParser(description='Open your private footage library')
    parser.add_argument('--workspace', default='~/SavvyPreviewLibrary')
    parser.add_argument('--port', type=int, default=8421)
    args = parser.parse_args()
    try:
        child = open_library(args.workspace, args.port)
        if child:
            print('Your footage library is open. Keep this window open while reviewing.', flush=True)
            try:
                child.wait()
            except KeyboardInterrupt:
                child.terminate()
                child.wait(timeout=5)
        else:
            print('Opened your already-running footage library.')
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
