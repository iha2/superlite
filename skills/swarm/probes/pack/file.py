"""file: traversal, symlinks, huge files, missing permissions."""
import os
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack._common import P, Skip

CATEGORY = "file"

TRAVERSAL = {
    "dotdot_unix": "../../../../../../../../etc/passwd",
    "dotdot_windows": "..\\..\\..\\..\\..\\windows\\win.ini",
    "absolute_unix": "/etc/passwd",
    "absolute_windows": "C:\\Windows\\win.ini",
    "doubled_dots": "....//....//....//etc/passwd",
    "encoded_dots": "%2e%2e/%2e%2e/%2e%2e/etc/passwd",
    "nul_in_name": "file\x00.txt",
    "empty_name": "",
    "parent_only": "..",
    "long_name": "a" * 300,
    "device_name": "CON",
    "trailing_dots": "name....",
}
URL_TRAVERSAL = {
    "dotdot": "/../../../../../etc/passwd",
    "encoded_dotdot": "/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
    "encoded_slash": "/..%2f..%2f..%2fetc%2fpasswd",
    "encoded_backslash": "/..%5c..%5c..%5cwindows%5cwin.ini",
    "nul_byte": "/%00",
    "double_slash": "//etc/passwd",
}
ACCEPT = {"status": [200, 301, 302, 400, 403, 404, 405, 414, 422]}

PROBES = []
for k, v in TRAVERSAL.items():
    PROBES.append(P(f"call_traversal_{k}", "call", lambda n, ctx, v=v: v))
    PROBES.append(P(f"arg_traversal_{k}", "run", lambda n, ctx, v=v: ([v.replace("\x00", "")], "")))
for k, v in URL_TRAVERSAL.items():
    PROBES.append(P(f"path_traversal_{k}", "request", lambda n, ctx, v=v: ("GET", v, None),
                    expect=ACCEPT))


def _missing(n, ctx):
    return str(ctx.tmp / "does-not-exist.txt")


def _directory(n, ctx):
    return str(ctx.tmp)


def _binary(n, ctx):
    p = ctx.tmp / "binary.bin"
    p.write_bytes(bytes(range(256)) * 64)
    return str(p)


def _empty(n, ctx):
    p = ctx.tmp / "empty.txt"
    p.write_bytes(b"")
    return str(p)


def _huge(n, ctx):  # O(n) file write (skipped if the file exists)
    p = ctx.tmp / f"huge_{n}.txt"
    if not p.exists():
        chunk = b"a" * (1 << 20)
        with open(p, "wb") as f:
            for _ in range(n // len(chunk)):
                f.write(chunk)
            f.write(b"a" * (n % len(chunk)))
    return str(p)


def _symlink(n, ctx):
    jail = ctx.tmp / "jail"
    jail.mkdir(exist_ok=True)
    secret = ctx.tmp / "secret.txt"
    secret.write_text("outside the jail")
    link = jail / "link.txt"
    if not link.is_symlink():
        try:
            os.symlink(secret, link)
        except (OSError, NotImplementedError):
            raise Skip("symlinks unavailable")
    return str(link)


def _symlink_loop(n, ctx):
    a, b = ctx.tmp / "loop_a", ctx.tmp / "loop_b"
    if not a.is_symlink():
        try:
            os.symlink(b, a)
            os.symlink(a, b)
        except (OSError, NotImplementedError):
            raise Skip("symlinks unavailable")
    return str(a)


def _unreadable(n, ctx):
    if sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        raise Skip("permission bits not enforced here")
    p = ctx.tmp / "unreadable.txt"
    p.write_text("secret")
    p.chmod(0)
    return str(p)


for k, f, sizes in (("missing", _missing, (None,)), ("directory", _directory, (None,)),
                    ("binary", _binary, (None,)), ("empty", _empty, (None,)),
                    ("huge", _huge, (1_000_000, 20_000_000, 100_000_000)),
                    ("symlink_outside", _symlink, (None,)), ("symlink_loop", _symlink_loop, (None,)),
                    ("unreadable", _unreadable, (None,))):
    PROBES.append(P(f"call_{k}", "call", f, sizes, regress=False))
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, f=f: ([f(n, ctx)], ""), sizes, regress=False))
