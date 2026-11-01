"""deep: nesting, long expressions, long chains."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack._common import P, DEEP

CATEGORY = "deep"

TEXTS = {
    "paren_nesting": lambda n: "(" * n + "1" + ")" * n,
    "bracket_nesting": lambda n: "[" * n + "]" * n,
    "brace_nesting": lambda n: "{" * n + "}" * n,
    "json_object_nesting": lambda n: '{"a":' * n + "1" + "}" * n,
    "long_sum": lambda n: "1+" * n + "1",
    "long_unary": lambda n: "-" * n + "1",
    "long_chain": lambda n: "a." * n + "a",
    "long_token": lambda n: "a" * (n * 10),
    "long_list": lambda n: ",".join(["1"] * (n * 5)),
    "long_whitespace": lambda n: " " * (n * 10) + "1",
}
ACCEPT = {"status": [200, 201, 202, 204, 400, 404, 405, 411, 413, 414, 415, 422, 431]}

PROBES = [P(f"call_{k}", "call", lambda n, ctx, f=f: f(n), DEEP) for k, f in TEXTS.items()]
for k in ("paren_nesting", "json_object_nesting", "long_sum", "bracket_nesting"):
    f = TEXTS[k]
    PROBES.append(P(f"stdin_{k}", "run", lambda n, ctx, f=f: ([], f(n)), DEEP))
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, f=f: ([f(min(n, 2000))], ""), DEEP))
    PROBES.append(P(f"body_{k}", "request",
                    lambda n, ctx, f=f: ("POST", ctx.good_path, f(n)), DEEP, expect=ACCEPT))
PROBES.append(P("long_path", "request", lambda n, ctx: ("GET", "/" + "a/" * n, None), DEEP,
                expect=ACCEPT))
PROBES.append(P("long_query", "request",
                lambda n, ctx: ("GET", ctx.good_path + "?a=" + "x" * (n * 5), None), DEEP,
                expect=ACCEPT))
