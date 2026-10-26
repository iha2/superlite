"""file: traversal, symlinks, huge files, missing permissions."""
# Complexity notation:
#   n = size in bytes of the file a probe creates, t = length of a probe string (constant), e = entries in a table (at most 12)
import os  # O(1)
import sys  # O(1)

from pack._common import P, Skip  # O(1)

CATEGORY = "file"  # O(1)

TRAVERSAL = {  # O(1)
    "dotdot_unix": "../../../../../../../../etc/passwd",  # O(1)
    "dotdot_windows": "..\\..\\..\\..\\..\\windows\\win.ini",  # O(1)
    "absolute_unix": "/etc/passwd",  # O(1)
    "absolute_windows": "C:\\Windows\\win.ini",  # O(1)
    "doubled_dots": "....//....//....//etc/passwd",  # O(1)
    "encoded_dots": "%2e%2e/%2e%2e/%2e%2e/etc/passwd",  # O(1)
    "nul_in_name": "file\x00.txt",  # O(1)
    "empty_name": "",  # O(1)
    "parent_only": "..",  # O(1)
    "long_name": "a" * 300,  # O(1)
    "device_name": "CON",  # O(1)
    "trailing_dots": "name....",  # O(1)
}  # O(1)
URL_TRAVERSAL = {  # O(1)
    "dotdot": "/../../../../../etc/passwd",  # O(1)
    "encoded_dotdot": "/%2e%2e/%2e%2e/%2e%2e/etc/passwd",  # O(1)
    "encoded_slash": "/..%2f..%2f..%2fetc%2fpasswd",  # O(1)
    "encoded_backslash": "/..%5c..%5c..%5cwindows%5cwin.ini",  # O(1)
    "nul_byte": "/%00",  # O(1)
    "double_slash": "//etc/passwd",  # O(1)
}  # O(1)
ACCEPT = {"status": [200, 301, 302, 400, 403, 404, 405, 414, 422]}  # O(1)

PROBES = []  # O(1)
for k, v in TRAVERSAL.items():  # O(e) iterations
    PROBES.append(P(f"call_traversal_{k}", "call", lambda n, ctx, v=v: v))  # O(1)
    PROBES.append(P(f"arg_traversal_{k}", "run", lambda n, ctx, v=v: ([v.replace("\x00", "")], "")))  # O(1) to build; lambda O(t) per call
for k, v in URL_TRAVERSAL.items():  # O(e) iterations
    PROBES.append(P(f"path_traversal_{k}", "request", lambda n, ctx, v=v: ("GET", v, None),  # O(1)
                    expect=ACCEPT))  # O(1)


def _missing(n, ctx):  # O(1)
    return str(ctx.tmp / "does-not-exist.txt")  # O(1)


def _directory(n, ctx):  # O(1)
    return str(ctx.tmp)  # O(1)


def _binary(n, ctx):  # O(1) per call (16 KiB write)
    p = ctx.tmp / "binary.bin"  # O(1)
    p.write_bytes(bytes(range(256)) * 64)  # O(1) 16 KiB write
    return str(p)  # O(1)


def _empty(n, ctx):  # O(1) per call
    p = ctx.tmp / "empty.txt"  # O(1)
    p.write_bytes(b"")  # O(1)
    return str(p)  # O(1)


def _huge(n, ctx):  # O(n) file write (skipped if the file exists)
    p = ctx.tmp / f"huge_{n}.txt"  # O(1)
    if not p.exists():  # O(1) stat
        chunk = b"a" * (1 << 20)  # O(1) 1 MiB buffer
        with open(p, "wb") as f:  # O(1) open
            for _ in range(n // len(chunk)):  # O(n / 1 MiB) iterations
                f.write(chunk)  # O(1 MiB) per iteration
            f.write(b"a" * (n % len(chunk)))  # O(1 MiB) tail write
    return str(p)  # O(1)


def _symlink(n, ctx):  # O(1)
    jail = ctx.tmp / "jail"  # O(1)
    jail.mkdir(exist_ok=True)  # O(1)
    secret = ctx.tmp / "secret.txt"  # O(1)
    secret.write_text("outside the jail")  # O(1)
    link = jail / "link.txt"  # O(1)
    if not link.is_symlink():  # O(1)
        try:  # O(1)
            os.symlink(secret, link)  # O(1)
        except (OSError, NotImplementedError):  # O(1)
            raise Skip("symlinks unavailable")  # O(1)
    return str(link)  # O(1)


def _symlink_loop(n, ctx):  # O(1)
    a, b = ctx.tmp / "loop_a", ctx.tmp / "loop_b"  # O(1)
    if not a.is_symlink():  # O(1)
        try:  # O(1)
            os.symlink(b, a)  # O(1)
            os.symlink(a, b)  # O(1)
        except (OSError, NotImplementedError):  # O(1)
            raise Skip("symlinks unavailable")  # O(1)
    return str(a)  # O(1)


def _unreadable(n, ctx):  # O(1)
    if sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0):  # O(1)
        raise Skip("permission bits not enforced here")  # O(1)
    p = ctx.tmp / "unreadable.txt"  # O(1)
    p.write_text("secret")  # O(1)
    p.chmod(0)  # O(1)
    return str(p)  # O(1)


for k, f, sizes in (("missing", _missing, (None,)), ("directory", _directory, (None,)),  # O(e) iterations (8)
                    ("binary", _binary, (None,)), ("empty", _empty, (None,)),  # O(1)
                    ("huge", _huge, (1_000_000, 20_000_000, 100_000_000)),  # O(1)
                    ("symlink_outside", _symlink, (None,)), ("symlink_loop", _symlink_loop, (None,)),  # O(1)
                    ("unreadable", _unreadable, (None,))):  # O(1)
    PROBES.append(P(f"call_{k}", "call", f, sizes, regress=False))  # O(1)
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, f=f: ([f(n, ctx)], ""), sizes, regress=False))  # O(1) to build; lambda cost of f(n) per call (O(n) for huge)
