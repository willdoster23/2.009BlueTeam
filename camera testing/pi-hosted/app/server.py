#!/usr/bin/env python3
"""Shared inspection dashboard on the rooftop Pi's Ethernet network."""
import datetime
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import network
from network import ROBOT_IP as PI_IP

ROOT = Path(__file__).resolve().parent
ADDRESS = (network.ROOF_IP, 80)
HOSTS = {'brick-mapper.local', '192.168.2.4', 'brick-mapper.local:80', '192.168.2.4:80'}
MEDIA_PATHS = {'video': '/usbtest/whep', 'audio': '/usbaudio/whep'}
MEDIA_ROUTE = re.compile(r'/media/(video|audio)/whep(?:/([a-f0-9-]{36}))?')
TOKEN = secrets.token_urlsafe(32)
health = {'reachable': False, 'checkedAt': None, 'latencyMs': None}
lock = threading.Lock()


def probe():
    while True:
        start = time.monotonic()
        conn = network.EthernetConnection(PI_IP, 8889, timeout=0.8)
        reachable = False
        try:
            conn.request('GET', '/usbtest/')
            reachable = conn.getresponse().status == 200
        except (OSError, http.client.HTTPException):
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

    def respond(self, status, data, content_type='application/json', headers=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def allowed_host(self):
        return self.headers.get('Host', '').lower() in HOSTS

    def authorized(self):
        return (self.allowed_host()
                and self.headers.get('Origin') == 'http://' + self.headers['Host'].lower().removesuffix(':80')
                and secrets.compare_digest(self.headers.get('X-Photo-Token', ''), TOKEN))

    def media(self, match):
        kind, session = match.groups()
        path = MEDIA_PATHS[kind]
        body = None
        if self.command == 'POST' and not session:
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 < length <= 65536:
                    raise ValueError('Invalid media offer length')
                self.connection.settimeout(5)
                body = self.rfile.read(length)
            except (OSError, ValueError):
                return self.respond(400, {'error': 'Invalid media offer'})
        elif self.command == 'DELETE' and session:
            path += '/' + session
        else:
            return self.respond(405, {'error': 'Unsupported media request'})
        upstream = network.EthernetConnection(PI_IP, 8889, timeout=4)
        try:
            upstream.request(self.command, path, body, {'Content-Type': 'application/sdp'})
            response = upstream.getresponse()
            content = response.read(65537)
            if len(content) > 65536:
                return self.respond(502, {'error': 'Invalid media response'})
            headers = {}
            if response.status == 201:
                location = urlsplit(response.getheader('Location', '')).path
                session_path = re.fullmatch(re.escape(path) + r'/([a-f0-9-]{36})', location)
                if not session_path:
                    return self.respond(502, {'error': 'Invalid media session'})
                headers['Location'] = f'/media/{kind}/whep/{session_path[1]}'
            return self.respond(response.status, content, response.getheader('Content-Type', 'application/sdp'), headers)
        except (OSError, ValueError, http.client.HTTPException):
            return self.respond(503, {'error': 'Robot media is unavailable'})
        finally:
            upstream.close()

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403, {'error': 'Use the inspection LAN address'})
        if self.path.split('?')[0] == '/diagnostics.html':
            html = (ROOT / 'index.html').read_text().replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        if self.path.split('?')[0] in ('/', '/index.html', '/map.html'):
            html = (ROOT / 'map.html').read_text().replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        if self.path in ('/player.js', '/dashboard.js', '/storage.js'):
            script = (ROOT / self.path[1:]).read_text().replace('__PHOTO_TOKEN__', TOKEN)
            return self.respond(200, script.encode(), 'text/javascript; charset=utf-8')
        if self.path == '/api/app':
            return self.respond(200, {'app': 'blue-team-brick-map', 'hostedOn': 'roof'})
        if self.path == '/api/health':
            with lock:
                return self.respond(200, dict(health))
        if self.path == '/api/network':
            return self.respond(200, network.status())
        self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.authorized():
            return self.respond(403, {'error': 'Use the Brick Mapper page'})
        media = MEDIA_ROUTE.fullmatch(self.path)
        if media:
            return self.media(media)
        if self.path in ('/api/winch', '/api/tap'):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if self.headers.get('Transfer-Encoding') or not 0 < length <= 1024:
                    raise ValueError('Invalid request length')
                self.connection.settimeout(3)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('Expected an object')
                command = network.command if self.path == '/api/winch' else network.tap
                return self.respond(*command(payload))
            except (OSError, ValueError):
                return self.respond(400, {'error': 'Invalid control request'})
        self.respond(404, {'error': 'Not found'})

    def do_DELETE(self):
        # Fetch may omit Origin on a same-origin request when the page unloads.
        origin = self.headers.get('Origin')
        if (not self.allowed_host() or (origin and origin != 'http://' + self.headers['Host'].lower().removesuffix(':80'))
                or not secrets.compare_digest(self.headers.get('X-Photo-Token', ''), TOKEN)):
            return self.respond(403, {'error': 'Use the Brick Mapper page'})
        media = MEDIA_ROUTE.fullmatch(self.path)
        if media:
            return self.media(media)
        self.respond(404, {'error': 'Not found'})


def listener():
    if int(os.environ.get('LISTEN_PID', '0')) != os.getpid() or os.environ.get('LISTEN_FDS') != '1':
        return ThreadingHTTPServer(ADDRESS, Handler)
    server = ThreadingHTTPServer(ADDRESS, Handler, bind_and_activate=False)
    server.socket.close()
    server.socket = socket.socket(fileno=3)
    server.server_address = server.socket.getsockname()
    return server


if __name__ == '__main__':
    network.start()
    threading.Thread(target=probe, daemon=True).start()
    print('Inspection dashboard on http://brick-mapper.local/', flush=True)
    with listener() as server:
        server.serve_forever()
