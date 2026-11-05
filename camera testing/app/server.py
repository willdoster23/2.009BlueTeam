#!/usr/bin/env python3
"""Local bridge viewer, Ethernet reachability probes, and Desktop snapshots."""
import datetime
import http.client
import json
import math
import os
import shutil
from pathlib import Path
import secrets
import sqlite3
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = ROOT.parent
DATA_DIR = PACKAGE_ROOT / 'data'
PHOTOS = PACKAGE_ROOT / 'Photos'
DATA_DIR.mkdir(exist_ok=True)
PHOTOS.mkdir(exist_ok=True)
DESKTOP = Path.home() / 'Desktop'
INSTANCE = os.environ.get('BRICK_MAP_INSTANCE', '')
CONTROL_TOKEN = os.environ.get('BRICK_MAP_CONTROL_TOKEN', secrets.token_urlsafe(32))
ADDRESS = ('127.0.0.1', 8767)
ORIGIN = 'http://127.0.0.1:8767'
PI_IP = '192.168.2.3'
TOKEN = secrets.token_urlsafe(32)
DATABASE = DATA_DIR / 'brick-map.sqlite3'
health = {'reachable': False, 'checkedAt': None, 'latencyMs': None}
lock = threading.Lock()


def database():
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS photos (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, captured_at TEXT NOT NULL,
        x REAL NOT NULL, y REAL NOT NULL, status TEXT NOT NULL,
        width INTEGER NOT NULL, height INTEGER NOT NULL,
        position_source TEXT NOT NULL DEFAULT 'manual',
        classification_source TEXT NOT NULL DEFAULT 'manual')''')
    return db


def photo_record(row):
    record = dict(row)
    record['url'] = '/api/photos/' + record['id'] + '/image'
    return record


def probe():
    while True:
        start = time.monotonic()
        conn = http.client.HTTPConnection(PI_IP, 8090, timeout=0.8)
        reachable = False
        try:
            conn.request('HEAD', '/')
            reachable = conn.getresponse().status == 200
        except OSError:
            pass
        finally:
            conn.close()
        with lock:
            health.update(reachable=reachable,
                          checkedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          latencyMs=round((time.monotonic() - start) * 1000, 1) if reachable else None)
        time.sleep(max(0.1, 1 - (time.monotonic() - start)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, data, content_type='application/json'):
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.end_headers()
        self.wfile.write(data)

    def allowed_host(self):
        return self.headers.get('Host') == '127.0.0.1:8767'

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403, {'error': 'Local viewer only'})
        if self.path.split('?')[0] in ('/', '/index.html'):
            html = (ROOT / 'index.html').read_text().replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        if self.path.split('?')[0] == '/map.html':
            html = (ROOT / 'map.html').read_text().replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        if self.path == '/player.js':
            script = (ROOT / 'player.js').read_text().replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, script.encode(), 'text/javascript; charset=utf-8')
        if self.path == '/api/photos':
            with database() as db:
                rows = db.execute('SELECT * FROM photos ORDER BY captured_at, id').fetchall()
            return self.respond(200, [photo_record(row) for row in rows])
        if self.path == '/api/photos/export':
            with database() as db:
                rows = db.execute('SELECT * FROM photos ORDER BY captured_at, id').fetchall()
            return self.respond(200, {'version': 1, 'coordinate_units': 'encoder counts',
                                     'position_source': 'manual',
                                     'photos': [photo_record(row) for row in rows]})
        parts = self.path.split('/')
        if len(parts) == 5 and parts[1:3] == ['api', 'photos'] and parts[4] == 'image':
            with database() as db:
                row = db.execute('SELECT name FROM photos WHERE id=?', (parts[3],)).fetchone()
            if row:
                path = PHOTOS / row['name']
                if path.is_file() and path.resolve().parent == PHOTOS.resolve():
                    return self.respond(200, path.read_bytes(), 'image/png')
            return self.respond(404, {'error': 'Photo file not found in the Photos folder'})
        if self.path == '/api/app':
            return self.respond(200, {'app': 'blue-team-brick-map', 'instance': INSTANCE, 'pid': os.getpid(), 'folder': str(PACKAGE_ROOT)})
        if self.path == '/api/health':
            with lock:
                return self.respond(200, dict(health))
        self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        if self.path == '/api/shutdown':
            if (not self.allowed_host() or self.headers.get('Origin') != ORIGIN
                    or not secrets.compare_digest(self.headers.get('X-Control-Token', ''), CONTROL_TOKEN)):
                return self.respond(403, {'error': 'Use Stop.command to stop this app'})
            self.respond(200, {'stopping': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if (not self.allowed_host() or self.headers.get('Origin') != ORIGIN
                or not secrets.compare_digest(self.headers.get('X-Photo-Token', ''), TOKEN)):
            return self.respond(403, {'error': 'Use the local viewer to take photos'})
        if self.path.startswith('/api/photos/') and self.path.endswith('/status'):
            parts = self.path.split('/')
            if len(parts) != 5:
                return self.respond(404, {'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024:
                    return self.respond(400, {'error': 'Invalid label request'})
                payload = json.loads(self.rfile.read(length))
                status = payload.get('status')
                if status not in ('good', 'bad'):
                    return self.respond(400, {'error': 'Choose good or bad'})
                with database() as db:
                    result = db.execute('UPDATE photos SET status=? WHERE id=?', (status, parts[3]))
                    if not result.rowcount:
                        return self.respond(404, {'error': 'Photo not found'})
                    row = db.execute('SELECT * FROM photos WHERE id=?', (parts[3],)).fetchone()
                return self.respond(200, photo_record(row))
            except (ValueError, AttributeError):
                return self.respond(400, {'error': 'Invalid label request'})
        if self.path != '/api/snapshot':
            return self.respond(404, {'error': 'Not found'})
        try:
            metadata = None
            if 'X-Encoder-X' in self.headers or 'X-Encoder-Y' in self.headers:
                try:
                    x, y = float(self.headers['X-Encoder-X']), float(self.headers['X-Encoder-Y'])
                except (ValueError, TypeError):
                    return self.respond(400, {'error': 'Enter numeric X and Y positions'})
                status = self.headers.get('X-Brick-Status')
                if not all(math.isfinite(n) and abs(n) <= 1e12 for n in (x, y)):
                    return self.respond(400, {'error': 'X and Y must be finite numbers within ±1 trillion'})
                if status not in ('good', 'bad'):
                    return self.respond(400, {'error': 'Choose good or bad'})
                metadata = (x, y, status)
            length = int(self.headers.get('Content-Length', '0'))
            if not 24 <= length <= 12 * 1024 * 1024:
                return self.respond(400, {'error': 'Invalid image size'})
            self.connection.settimeout(10)
            data = self.rfile.read(length)
            if len(data) != length or data[:8] != b'\x89PNG\r\n\x1a\n' or data[12:16] != b'IHDR':
                return self.respond(400, {'error': 'Expected a PNG photo'})
            width, height = struct.unpack('>II', data[16:24])
            if not (1 <= width <= 8192 and 1 <= height <= 8192):
                return self.respond(400, {'error': 'Invalid photo dimensions'})
            stamp = datetime.datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d_%H-%M-%S-%f')
            name = f'bridge-photo_{stamp}_{uuid.uuid4().hex[:8]}.png'
            with (PHOTOS / name).open('xb') as photo:
                photo.write(data)
            desktop_saved = False
            try:
                with (DESKTOP / name).open('xb') as desktop_photo:
                    desktop_photo.write(data)
                desktop_saved = True
            except OSError:
                pass
            result = {'name': name, 'width': width, 'height': height, 'desktop_saved': desktop_saved}
            if metadata is not None:
                photo_id = uuid.uuid4().hex
                try:
                    with database() as db:
                        db.execute('''INSERT INTO photos
                            (id,name,captured_at,x,y,status,width,height) VALUES (?,?,?,?,?,?,?,?)''',
                            (photo_id, name, datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             *metadata, width, height))
                        row = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
                    result['record'] = photo_record(row)
                    result['record']['desktop_saved'] = desktop_saved
                except sqlite3.Error:
                    (PHOTOS / name).unlink(missing_ok=True)
                    if desktop_saved: (DESKTOP / name).unlink(missing_ok=True)
                    return self.respond(500, {'error': 'Could not save photo position; retry the capture'})
            self.respond(201, result)
        except PermissionError:
            self.respond(500, {'error': 'Cannot write to this app folder. Move it to a writable location and try again.'})
        except (OSError, ValueError) as error:
            self.respond(500, {'error': str(error)})


if __name__ == '__main__':
    threading.Thread(target=probe, daemon=True).start()
    print(f'Bridge viewer: {ORIGIN}', flush=True)
    with ThreadingHTTPServer(ADDRESS, Handler) as server:
        server.serve_forever()
