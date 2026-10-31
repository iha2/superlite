"""Fixture HTTP service: GET/POST /echo; GET /crash closes the listener (a simulated server crash)."""
import json
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/crash"):
            self._send(200, {"crashing": True})
            self.server.crash()
            return
        self._send(200, {"ok": True, "path": self.path[:50]})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": "bad length"})
        if n < 0 or n > 5_000_000:
            return self._send(413, {"error": "too large"})
        body = self.rfile.read(n)
        try:
            json.loads(body or b"null")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        self._send(200, {"ok": True, "bytes": len(body)})


class Server(HTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)  # skip HTTPServer's reverse-DNS lookup: it hangs on macOS CI
        self.server_name, self.server_port = self.server_address[:2]

    def crash(self):
        self.socket.close()  # the serve loop dies on its next select


def create_server(addr=("127.0.0.1", 0)):
    return Server(addr, Handler)
