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

WINDOWS = os.name == 'nt'

try:
    from zoneinfo import ZoneInfo
    PHOTO_TZ = ZoneInfo('America/New_York')
except Exception:
    # Windows has no built-in time zone database unless the 'tzdata' package is
    # installed. Fall back to the computer's own local time for photo names.
    PHOTO_TZ = None


def find_desktop():
    """Return the real Desktop folder, including OneDrive-redirected ones on Windows."""
    if WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                            ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]

            desktop_id = GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                              (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
            path_ptr = ctypes.c_wchar_p()
            shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
            if shell32.SHGetKnownFolderPath(ctypes.byref(desktop_id), 0, None,
                                            ctypes.byref(path_ptr)) == 0:
                try:
                    return Path(path_ptr.value)
                finally:
                    ole32.CoTaskMemFree(path_ptr)
        except Exception:
            pass
    return Path.home() / 'Desktop'


ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = ROOT.parent
DATA_DIR = PACKAGE_ROOT / 'data'
PHOTOS = PACKAGE_ROOT / 'Photos'
DATA_DIR.mkdir(exist_ok=True)
PHOTOS.mkdir(exist_ok=True)
DESKTOP = find_desktop()
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
    # One row per building scan: its size, the square photo-frame size and the grid it makes.
    # Frames are numbered slot = row * cols + col (row 0 at the top, col 0 at the left).
    # scan_x / scan_y is the last known scanner position (metres); finished = scan reached the bottom.
    db.execute('''CREATE TABLE IF NOT EXISTS map_sessions (
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
        width_m REAL NOT NULL, height_m REAL NOT NULL, frame_m REAL NOT NULL,
        cols INTEGER NOT NULL, rows INTEGER NOT NULL, next_slot INTEGER NOT NULL DEFAULT 0)''')
    session_columns = {row[1] for row in db.execute('PRAGMA table_info(map_sessions)')}
    for column, kind in (('scan_x', 'REAL NOT NULL DEFAULT 0'), ('scan_y', 'REAL NOT NULL DEFAULT 0'),
                         ('finished', 'INTEGER NOT NULL DEFAULT 0')):
        if column not in session_columns:
            db.execute(f'ALTER TABLE map_sessions ADD COLUMN {column} {kind}')
    # Frames the scanner has moved across. A passed frame with no photo is shown grey.
    db.execute('''CREATE TABLE IF NOT EXISTS map_passed (
        session_id TEXT NOT NULL, slot INTEGER NOT NULL, PRIMARY KEY (session_id, slot))''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(photos)')}
    if 'session_id' not in columns:
        db.execute('ALTER TABLE photos ADD COLUMN session_id TEXT')
    if 'slot' not in columns:
        db.execute('ALTER TABLE photos ADD COLUMN slot INTEGER')
    return db


MAX_FRAMES = 20000


def photo_record(row):
    record = dict(row)
    record['url'] = '/api/photos/' + record['id'] + '/image'
    return record


def session_payload(db, row):
    if row is None:
        return None
    session = dict(row)
    session['passed'] = [r[0] for r in db.execute(
        'SELECT slot FROM map_passed WHERE session_id=? ORDER BY slot', (session['id'],))]
    return session


def latest_session(db):
    return session_payload(db, db.execute(
        'SELECT * FROM map_sessions ORDER BY created_at DESC, rowid DESC LIMIT 1').fetchone())


def slot_for(session, x, y):
    """The frame containing position (x, y) in metres, or None when it is outside the drawn map."""
    f, eps = session['frame_m'], 1e-9
    if not (-eps <= x <= session['cols'] * f + eps and -eps <= y <= session['rows'] * f + eps):
        return None
    # +1e-9 so a strip edge such as 3 * 0.61 m lands in column 4, not column 3, despite rounding
    col = min(max(math.floor(x / f + 1e-9), 0), session['cols'] - 1)
    row = min(max(math.floor(y / f + 1e-9), 0), session['rows'] - 1)
    return row * session['cols'] + col


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

    def read_json(self, limit=4096):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= limit:
            raise ValueError('Invalid request size')
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object')
        return data

    def allowed_host(self):
        return self.headers.get('Host') == '127.0.0.1:8767'

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403, {'error': 'Local viewer only'})
        if self.path.split('?')[0] in ('/', '/index.html'):
            html = (ROOT / 'index.html').read_text(encoding='utf-8').replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        if self.path.split('?')[0] == '/map.html':
            html = (ROOT / 'map.html').read_text(encoding='utf-8').replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        if self.path == '/player.js':
            script = (ROOT / 'player.js').read_text(encoding='utf-8').replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, script.encode(), 'text/javascript; charset=utf-8')
        if self.path == '/api/photos':
            with database() as db:
                rows = db.execute('SELECT * FROM photos ORDER BY captured_at, id').fetchall()
            return self.respond(200, [photo_record(row) for row in rows])
        if self.path == '/api/photos/export':
            with database() as db:
                rows = db.execute('SELECT * FROM photos ORDER BY captured_at, id').fetchall()
                sessions = [session_payload(db, r) for r in db.execute('SELECT * FROM map_sessions ORDER BY created_at')]
            return self.respond(200, {'version': 2,
                                     'coordinate_units': 'metres for simulated/encoder positions; encoder counts for manual ones',
                                     'slot_order': 'left to right, then top to bottom; slot = row * cols + col',
                                     'sessions': sessions,
                                     'photos': [photo_record(row) for row in rows]})
        if self.path == '/api/session':
            with database() as db:
                return self.respond(200, {'session': latest_session(db)})
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
                return self.respond(403, {'error': 'Use the Stop launcher to stop this app'})
            self.respond(200, {'stopping': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if (not self.allowed_host() or self.headers.get('Origin') != ORIGIN
                or not secrets.compare_digest(self.headers.get('X-Photo-Token', ''), TOKEN)):
            return self.respond(403, {'error': 'Use the local viewer to take photos'})
        if self.path == '/api/session':
            try:
                payload = self.read_json()
                width, height, frame = (float(payload[k]) for k in ('width_m', 'height_m', 'frame_m'))
            except (ValueError, TypeError, KeyError, AttributeError):
                return self.respond(400, {'error': 'Enter numeric width, height and frame size in metres'})
            if not (all(math.isfinite(n) for n in (width, height, frame))
                    and 0.1 <= width <= 1000 and 0.1 <= height <= 1000 and 0.05 <= frame <= 10):
                return self.respond(400, {'error': 'Width and height must be 0.1-1000 m; frame size 0.05-10 m'})
            cols, rows = math.ceil(width / frame - 1e-9), math.ceil(height / frame - 1e-9)
            if cols * rows > MAX_FRAMES:
                return self.respond(400, {'error': f'That makes {cols * rows} frames (limit {MAX_FRAMES}); use a larger frame size'})
            session_id = uuid.uuid4().hex
            with database() as db:
                db.execute('INSERT INTO map_sessions (id,created_at,width_m,height_m,frame_m,cols,rows) VALUES (?,?,?,?,?,?,?)',
                           (session_id, datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            width, height, frame, cols, rows))
                session = session_payload(db, db.execute('SELECT * FROM map_sessions WHERE id=?', (session_id,)).fetchone())
            return self.respond(201, session)
        if self.path == '/api/session/progress':
            # Scanner progress: its current position and the frames it has moved across.
            try:
                payload = self.read_json(512 * 1024)
                session_id = str(payload['session_id'])
                x, y = float(payload['x']), float(payload['y'])
                passed = payload.get('passed', [])
                finished = bool(payload.get('finished', False))
                if (not isinstance(passed, list) or len(passed) > MAX_FRAMES
                        or not all(isinstance(s, int) and not isinstance(s, bool) for s in passed)
                        or not all(math.isfinite(n) and abs(n) <= 1e6 for n in (x, y))):
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError):
                return self.respond(400, {'error': 'Invalid progress update'})
            with database() as db:
                row = db.execute('SELECT * FROM map_sessions WHERE id=?', (session_id,)).fetchone()
                if not row:
                    return self.respond(404, {'error': 'Map not found'})
                total = row['cols'] * row['rows']
                db.executemany('INSERT OR IGNORE INTO map_passed (session_id, slot) VALUES (?,?)',
                               [(session_id, s) for s in passed if 0 <= s < total])
                db.execute('UPDATE map_sessions SET scan_x=?, scan_y=?, finished=MAX(finished, ?) WHERE id=?',
                           (x, y, int(finished), session_id))
                session = session_payload(db, db.execute('SELECT * FROM map_sessions WHERE id=?', (session_id,)).fetchone())
            return self.respond(200, session)
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
            map_session = None
            if 'X-Map-Session' in self.headers:
                # The photo's frame is worked out here from its X, Y position.
                if metadata is None:
                    return self.respond(400, {'error': 'Mapped photos need X, Y and a label'})
                with database() as db:
                    found = db.execute('SELECT * FROM map_sessions WHERE id=?',
                                       (self.headers['X-Map-Session'],)).fetchone()
                if not found:
                    return self.respond(404, {'error': 'Map not found; set the building size again'})
                map_session = dict(found)
            position_source = self.headers.get('X-Position-Source', 'manual')
            if position_source not in ('manual', 'simulated', 'encoder'):
                return self.respond(400, {'error': 'Invalid position source'})
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
            stamp = (datetime.datetime.now(PHOTO_TZ) if PHOTO_TZ else datetime.datetime.now()).strftime('%Y-%m-%d_%H-%M-%S-%f')
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
                    slot = slot_for(map_session, metadata[0], metadata[1]) if map_session else None
                    with database() as db:
                        db.execute('''INSERT INTO photos
                            (id,name,captured_at,x,y,status,width,height,position_source,session_id,slot)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                            (photo_id, name, datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             *metadata, width, height, position_source,
                             map_session['id'] if map_session else None, slot))
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


class LocalServer(ThreadingHTTPServer):
    # On Windows, address reuse would let a second copy share port 8767, so it is off there.
    allow_reuse_address = not WINDOWS
    daemon_threads = True


if __name__ == '__main__':
    threading.Thread(target=probe, daemon=True).start()
    print(f'Bridge viewer: {ORIGIN}', flush=True)
    with LocalServer(ADDRESS, Handler) as server:
        server.serve_forever()
