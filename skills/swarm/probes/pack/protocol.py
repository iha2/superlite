"""protocol: unknown methods, a bad Content-Length, oversize and malformed bodies, unknown CLI commands."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack._common import P

CATEGORY = "protocol"

REJECT = {"status": [400, 404, 405, 501]}
ACCEPT = {"status": [200, 201, 202, 204, 400, 404, 405, 411, 413, 414, 415, 422, 431]}
SIZES = (1_000, 100_000, 3_000_000)

PROBES = []
for m in ("TRACE", "BREW", "PROPFIND", "FOO", "get"):
    PROBES.append(P(f"request_unknown_method_{m}", "request",
                    lambda n, ctx, m=m: (m, ctx.good_path, None), expect=REJECT))
for k, v in {"truncated_object": "{", "truncated_array": "[1,2", "missing_value": '{"a":}',
             "bare_word": "nope", "nul_byte": "\x00", "json_null": "null", "json_array": "[]",
             "json_string": '"text"', "trailing_comma": "[1,]", "empty_body": ""}.items():
    PROBES.append(P(f"body_{k}", "request", lambda n, ctx, v=v: ("POST", ctx.good_path, v),
                    expect=ACCEPT))
PROBES.append(P("body_oversize_text", "request",
                lambda n, ctx: ("POST", ctx.good_path, "x" * n), SIZES, expect=ACCEPT))
PROBES.append(P("body_oversize_json", "request",
                lambda n, ctx: ("POST", ctx.good_path, '{"a":"' + "x" * n + '"}'), SIZES,
                expect=ACCEPT))
PROBES.append(P("path_dot_segments", "request",
                lambda n, ctx: ("GET", "/./../a//b/", None), expect=ACCEPT))


def _raw(head, body=b""):
    return lambda n, ctx: (head.replace("{p}", ctx.good_path) + "\r\n\r\n").encode("latin-1") + body


_H = "Host: x\r\nConnection: close"
for k, (head, body) in {
    "cl_huge": (f"POST {{p}} HTTP/1.1\r\n{_H}\r\nContent-Length: 99999999999999999999", b"abc"),
    "cl_negative": (f"POST {{p}} HTTP/1.1\r\n{_H}\r\nContent-Length: -5", b"abc"),
    "cl_non_numeric": (f"POST {{p}} HTTP/1.1\r\n{_H}\r\nContent-Length: abc", b"abc"),
    "cl_duplicate_conflict": (
        f"POST {{p}} HTTP/1.1\r\n{_H}\r\nContent-Length: 3\r\nContent-Length: 40", b"abc"),
    "cl_longer_than_body": (f"POST {{p}} HTTP/1.1\r\n{_H}\r\nContent-Length: 100", b"abc"),
    "cl_zero_with_body": (f"POST {{p}} HTTP/1.1\r\n{_H}\r\nContent-Length: 0", b"abcdef"),
    "garbage_request_line": ("GARBAGE", b""),
    "no_http_version": ("GET /", b""),
    "unknown_http_version": (f"GET {{p}} HTTP/9.9\r\n{_H}", b""),
    "header_without_colon": (f"GET {{p}} HTTP/1.1\r\n{_H}\r\nbroken header line", b""),
    "nul_in_header": (f"GET {{p}} HTTP/1.1\r\n{_H}\r\nX-A: a\x00b", b""),
    "bare_lf_lines": ("GET {p} HTTP/1.1\nHost: x\nConnection: close\n", b""),
}.items():
    PROBES.append(P(f"raw_{k}", "raw", _raw(head, body)))
PROBES.append(P("raw_huge_header", "raw",
                lambda n, ctx: (f"GET {ctx.good_path} HTTP/1.1\r\n{_H}\r\nX-Big: " + "a" * n
                                + "\r\n\r\n").encode("latin-1"), SIZES))

for k, args in {"unknown_command": ["zz-no-such-command"], "unknown_flag": ["--no-such-flag-zz"],
                "empty_arg": [""], "single_dash": ["-"], "double_dash": ["--"],
                "flag_without_value": ["--output"], "many_dashes": ["-" * 200]}.items():
    PROBES.append(P(f"run_{k}", "run", lambda n, ctx, a=args: (a, "")))
PROBES.append(P("run_no_args_empty_stdin", "run", lambda n, ctx: ([], "")))
PROBES.append(P("run_long_arg", "run", lambda n, ctx: (["a" * n], ""), (100, 1000, 3000)))
PROBES.append(P("run_many_args", "run", lambda n, ctx: (["a"] * n, ""), (10, 200, 1000)))
