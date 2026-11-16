"""Fake cloud server for integration tests: localhost ThreadingHTTPServer on port 0.

serve(routes) -> (url, stop). routes maps (METHOD, path) to a JSON body, a (status, body) pair, or a
list of event dicts for an SSE path ({"id", "kind", "data"}); the stream honours ?after= and
Last-Event-ID and closes after a terminal status. Unknown routes give 404 {"error": "not found"}.
stop.requests lists (method, path, json body) seen, in order.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

TERMINAL = ("done", "failed", "cancelled")


def serve(routes: dict):
    seen: list = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _handle(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = None
            seen.append((self.command, u.path, body))
            hit = routes.get((self.command, u.path))
            if hit is None:
                return self._send(404, {"error": "not found"})
            if isinstance(hit, list) and u.path.endswith("/events"):
                q = parse_qs(u.query)
                after = int((q.get("after") or [self.headers.get("Last-Event-ID") or 0])[0])
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for ev in hit:
                    if ev["id"] <= after:
                        continue
                    self.wfile.write(f"id: {ev['id']}\nevent: {ev['kind']}\ndata: {json.dumps(ev['data'])}\n\n".encode())
                    self.wfile.flush()
                    if ev["kind"] == "status" and ev["data"].get("to") in TERMINAL:
                        break
                return
            status, out = hit if isinstance(hit, tuple) else (200, hit)
            self._send(status, out)

        do_GET = do_POST = _handle

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def stop():
        srv.shutdown()
        srv.server_close()
        t.join(5)

    stop.requests = seen
    return f"http://127.0.0.1:{srv.server_address[1]}", stop
