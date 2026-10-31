from __future__ import annotations

import json
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer

from .workbook import Workbook


class _Server(HTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)  # skip HTTPServer's reverse-DNS lookup: it hangs on macOS CI
        self.server_name, self.server_port = self.server_address[:2]


def create_server(wb: Workbook, port: int = 0) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == "/api/cells":
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                changed = wb.set(data["ref"], data["raw"])
                self._send(200, {"cells": {r: wb.get(r) for r in changed}})
            else:
                self._send(404, {"error": "not found"})

        def do_GET(self):
            if self.path.startswith("/api/cells/"):
                ref = self.path.rsplit("/", 1)[1]
                self._send(200, {"ref": ref, "value": wb.get(ref)})
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, *args):
            pass

    return _Server(("127.0.0.1", port), Handler)
