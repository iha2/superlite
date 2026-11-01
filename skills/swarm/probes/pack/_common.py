"""Shared building blocks for probe modules.

A probe module defines ``CATEGORY`` (str) and ``PROBES`` (list of dicts built with ``P``).

``P(name, entry, make, sizes=(None,), expect=None, check=None, regress=True)``
  entry   "call" | "run" | "request" | "raw"
  make    ``make(size, ctx) -> input`` where the input is
            call    -> str
            run     -> (args, stdin)
            request -> (method, path, body)
            raw     -> bytes (a raw HTTP request, server only)
          or a ``Seq`` of such inputs, run in order (state probes).
  sizes   stepped sizes, tried in order; the probe stops at the first failure.
  expect  {"status": [...]} the only acceptable statuses (request/raw).
  check   ``check(results, ctx) -> None | str``; a string means "inconsistent".
  regress False when the input depends on files made under ``ctx.tmp``.

``ctx`` carries ``adapter``, ``tmp`` (a private temp dir), ``good_input``,
``good_args``, ``good_stdin`` and ``good_path`` (the adapter's GOOD_* values).
Raise ``Skip`` from ``make`` when the probe cannot apply on this machine.
"""


class Seq(list):
    """An ordered list of inputs for one entry kind."""


class Skip(Exception):
    pass


DEEP = (100, 2000, 10000)


def P(name, entry, make, sizes=(None,), expect=None, check=None, regress=True):  # O(s)
    return {"name": name, "entry": entry, "make": make, "sizes": list(sizes),
            "expect": expect, "check": check, "regress": regress}
