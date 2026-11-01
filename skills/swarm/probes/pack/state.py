"""state: consistency after an error.

The adapter's GOOD_INPUT / GOOD_ARGS / GOOD_STDIN / GOOD_PATH name a stateless, valid use of the
entry (defaults: "1", [], "", "/"). Each probe runs it, provokes errors, and runs it again; the
good results must match.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack._common import P, Seq

CATEGORY = "state"


def _same_first_last(results, ctx):  # O(r)
    if results[0] != results[-1]:
        return f"valid use gave {results[0]} before the errors and {results[-1]} after"
    return None


def _all_same(results, ctx):  # O(m*r)
    if any(r != results[0] for r in results):
        return f"repeated valid use gave different results: {results[:3]}"
    return None


PROBES = [
    P("call_good_after_errors", "call",
      lambda n, ctx: Seq([ctx.good_input, "(", ")", "\x00", "1e999", "(" * 30, "", "\ud800",
                          ctx.good_input]), check=_same_first_last),
    P("call_repeatable", "call", lambda n, ctx: Seq([ctx.good_input] * 3), check=_all_same),
    P("run_good_after_errors", "run",
      lambda n, ctx: Seq([(ctx.good_args, ctx.good_stdin), (["--no-such-flag-zz"], ""),
                          ([], "\x00\x00"), (["zz-no-such-command"], ""),
                          (ctx.good_args, ctx.good_stdin)]), check=_same_first_last),
    P("request_good_after_errors", "request",
      lambda n, ctx: Seq([("GET", ctx.good_path, None), ("POST", ctx.good_path, "{"),
                          ("BREW", ctx.good_path, None), ("POST", ctx.good_path, b"\xff\xfe"),
                          ("GET", "/%00", None), ("GET", ctx.good_path, None)]),
      check=_same_first_last),
    P("request_repeatable", "request",
      lambda n, ctx: Seq([("GET", ctx.good_path, None)] * 3), check=_all_same),
]
