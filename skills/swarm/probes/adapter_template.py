"""PYTHON ONLY: the probe runner and this adapter (also the one `pt_review.py adapter` generates, which provides
start_server, request and close for `serve=` entries) import the project's Python code. Other languages skip the probe
stage until they have their own adapter.
Probe adapter template. Copy to .superlite/probes/adapter.py and fill in what applies.

Define only the entry kinds the project has; probes for a missing kind are skipped.

  call(text)                 library entry: feed text, return a value, or raise one of
                             EXPECTED_ERRORS to reject it. Any other exception is a crash.
  run(args, stdin)           CLI entry: return (returncode, stdout, stderr). A negative or
                             0xC000xxxx return code, or a Traceback in stderr, is a crash.
  request(method, path, body)  server entry: return (status, parsed_json_or_None). The runner
                             sets PORT after calling start_server(); body is str, bytes or None.
  start_server()             start the server on port 0 and return (port, stop) where stop is a
                             callable or None; a server object with .server_address and
                             .shutdown() is accepted too. The runner starts one per worker,
                             health-checks it after every probe, and restarts it only if it died.

Optional knobs: EXPECTED_ERRORS, GOOD_INPUT, GOOD_ARGS, GOOD_STDIN, GOOD_PATH (a stateless valid
use of each entry, used by the state probes), and health() -> bool.
"""
# Complexity notation:
#   n = length of the input text/body/stdin, o = bytes of output returned, E = cost of the project's own entry point
import http.client  # O(1)
import json  # O(1)
import os  # O(1)
import subprocess  # O(1)
import sys  # O(1)

EXPECTED_ERRORS = (ValueError,)  # O(1)
GOOD_INPUT = "1"  # O(1)
GOOD_ARGS = []  # O(1)
GOOD_STDIN = ""  # O(1)
GOOD_PATH = "/"  # O(1)

PORT = None  # O(1) set by the runner set by the runner

# The runner passes the per-probe timeout so a CLI that hangs is stopped with its own children.
TIMEOUT = float(os.environ.get("SUPERLITE_PROBE_TIMEOUT", "5"))  # O(1) env lookup
CLI = [sys.executable, "-m", "your_package"]  # O(1) edit: how to start the CLI


def call(text):  # O(E(n))
    from your_package import evaluate  # O(1) edit: the library entry point
    return evaluate(text)  # O(E(n))


def run(args, stdin):  # O(n + o) + process spawn
    p = subprocess.run(CLI + list(args), input=stdin if isinstance(stdin, bytes) else stdin.encode("utf-8", "surrogateescape"),  # O(n + o) + process spawn
                       capture_output=True, timeout=TIMEOUT)  # O(1)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")  # O(o)


def start_server():  # O(1) + thread spawn
    from your_package.server import create_server  # O(1) edit: factory that binds ("127.0.0.1", 0)
    import threading  # O(1)
    server = create_server(("127.0.0.1", 0))  # O(1)
    threading.Thread(target=server.serve_forever, daemon=True).start()  # O(1) thread spawn
    return server.server_address[1], server.shutdown  # O(1)


def request(method, path, body):  # O(n + o) + network round trip
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=TIMEOUT)  # O(1)
    try:  # O(1)
        if isinstance(body, str):  # O(1)
            body = body.encode("utf-8", "surrogateescape")  # O(n)
        conn.request(method, path, body=body)  # O(n) send
        resp = conn.getresponse()  # O(o) wait for server + read headers
        data = resp.read()  # O(o)
        try:  # O(1)
            parsed = json.loads(data) if data else None  # O(o)
        except ValueError:  # O(1)
            parsed = data[:200].decode("utf-8", "replace")  # O(1) capped at 200 bytes
        return resp.status, parsed  # O(1)
    finally:  # O(1)
        conn.close()  # O(1)
