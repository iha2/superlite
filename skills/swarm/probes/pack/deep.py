"""deep: nesting, long expressions, long chains."""
# Complexity notation:
#   n = probe size step (100, 2000 or 10000), e = entries in a table (10 texts)
from pack._common import P, DEEP  # O(1)

CATEGORY = "deep"  # O(1)

TEXTS = {  # O(1)
    "paren_nesting": lambda n: "(" * n + "1" + ")" * n,  # O(n) per call (lambda); O(1) to build
    "bracket_nesting": lambda n: "[" * n + "]" * n,  # O(n) per call (lambda); O(1) to build
    "brace_nesting": lambda n: "{" * n + "}" * n,  # O(n) per call (lambda); O(1) to build
    "json_object_nesting": lambda n: '{"a":' * n + "1" + "}" * n,  # O(n) per call (lambda); O(1) to build
    "long_sum": lambda n: "1+" * n + "1",  # O(n) per call (lambda); O(1) to build
    "long_unary": lambda n: "-" * n + "1",  # O(n) per call (lambda); O(1) to build
    "long_chain": lambda n: "a." * n + "a",  # O(n) per call (lambda); O(1) to build
    "long_token": lambda n: "a" * (n * 10),  # O(n) per call (lambda); O(1) to build
    "long_list": lambda n: ",".join(["1"] * (n * 5)),  # O(n) per call (lambda); O(1) to build
    "long_whitespace": lambda n: " " * (n * 10) + "1",  # O(n) per call (lambda); O(1) to build
}  # O(1)
ACCEPT = {"status": [200, 201, 202, 204, 400, 404, 405, 411, 413, 414, 415, 422, 431]}  # O(1)

PROBES = [P(f"call_{k}", "call", lambda n, ctx, f=f: f(n), DEEP) for k, f in TEXTS.items()]  # O(e) build; each lambda O(n) per call
for k in ("paren_nesting", "json_object_nesting", "long_sum", "bracket_nesting"):  # O(1) iterations (4)
    f = TEXTS[k]  # O(1)
    PROBES.append(P(f"stdin_{k}", "run", lambda n, ctx, f=f: ([], f(n)), DEEP))  # O(1) to build; lambda O(n) per call
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, f=f: ([f(min(n, 2000))], ""), DEEP))  # O(1) to build; lambda O(n) per call
    PROBES.append(P(f"body_{k}", "request",  # O(1)
                    lambda n, ctx, f=f: ("POST", ctx.good_path, f(n)), DEEP, expect=ACCEPT))  # O(n) per call (lambda)
PROBES.append(P("long_path", "request", lambda n, ctx: ("GET", "/" + "a/" * n, None), DEEP,  # O(n) per call (lambda)
                expect=ACCEPT))  # O(1)
PROBES.append(P("long_query", "request",  # O(1)
                lambda n, ctx: ("GET", ctx.good_path + "?a=" + "x" * (n * 5), None), DEEP,  # O(n) per call (lambda)
                expect=ACCEPT))  # O(1)
