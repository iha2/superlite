"""state: consistency after an error.

The adapter's GOOD_INPUT / GOOD_ARGS / GOOD_STDIN / GOOD_PATH name a stateless, valid use of the
entry (defaults: "1", [], "", "/"). Each probe runs it, provokes errors, and runs it again; the
good results must match.
"""
# Complexity notation:
#   m = results per probe (at most 9), r = size of one result
from pack._common import P, Seq  # O(1)

CATEGORY = "state"  # O(1)


def _same_first_last(results, ctx):  # O(r)
    if results[0] != results[-1]:  # O(r)
        return f"valid use gave {results[0]} before the errors and {results[-1]} after"  # O(r)
    return None  # O(1)


def _all_same(results, ctx):  # O(m*r)
    if any(r != results[0] for r in results):  # O(m*r)
        return f"repeated valid use gave different results: {results[:3]}"  # O(m*r)
    return None  # O(1)


PROBES = [  # O(1)
    P("call_good_after_errors", "call",  # O(1)
      lambda n, ctx: Seq([ctx.good_input, "(", ")", "\x00", "1e999", "(" * 30, "", "\ud800",  # O(m) per call (lambda)
                          ctx.good_input]), check=_same_first_last),  # O(1)
    P("call_repeatable", "call", lambda n, ctx: Seq([ctx.good_input] * 3), check=_all_same),  # O(m) per call (lambda)
    P("run_good_after_errors", "run",  # O(1)
      lambda n, ctx: Seq([(ctx.good_args, ctx.good_stdin), (["--no-such-flag-zz"], ""),  # O(m) per call (lambda)
                          ([], "\x00\x00"), (["zz-no-such-command"], ""),  # O(m) per call (lambda)
                          (ctx.good_args, ctx.good_stdin)]), check=_same_first_last),  # O(m) per call (lambda)
    P("request_good_after_errors", "request",  # O(1)
      lambda n, ctx: Seq([("GET", ctx.good_path, None), ("POST", ctx.good_path, "{"),  # O(m) per call (lambda)
                          ("BREW", ctx.good_path, None), ("POST", ctx.good_path, b"\xff\xfe"),  # O(m) per call (lambda)
                          ("GET", "/%00", None), ("GET", ctx.good_path, None)]),  # O(m) per call (lambda)
      check=_same_first_last),  # O(1)
    P("request_repeatable", "request",  # O(1)
      lambda n, ctx: Seq([("GET", ctx.good_path, None)] * 3), check=_all_same),  # O(m) per call (lambda)
]  # O(1)
