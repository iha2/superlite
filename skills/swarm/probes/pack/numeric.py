"""numeric: 1e999, NaN, inf, huge ints, division by zero."""
# Complexity notation:
#   n = probe size step (20, 400 or 5000 digits), e = entries in a table (16 literals, 4 sized)
from pack._common import P  # O(1)

CATEGORY = "numeric"  # O(1)

LITERALS = {  # O(1)
    "exp_overflow": "1e999", "neg_exp_overflow": "-1e999", "exp_underflow": "1e-999",  # O(1)
    "nan": "nan", "nan_upper": "NaN", "inf": "inf", "neg_infinity": "-Infinity",  # O(1)
    "neg_zero": "-0", "div_zero": "1/0", "zero_div_zero": "0/0", "mod_zero": "1%0",  # O(1)
    "float_mul_overflow": "1e308*10", "float_sum": "0.1+0.2", "int_min": "-9223372036854775808",  # O(1)
    "int_overflow": "9223372036854775808", "huge_exponent": "1e999999999",  # O(1)
}  # O(1)
SIZED = {  # O(1)
    "huge_int": lambda n: "9" * n,  # O(1) to build; lambda O(n) per call
    "neg_huge_int": lambda n: "-" + "9" * n,  # O(1) to build; lambda O(n) per call
    "long_fraction": lambda n: "0." + "9" * n,  # O(1) to build; lambda O(n) per call
    "huge_exponent_digits": lambda n: "1e" + "9" * n,  # O(1) to build; lambda O(n) per call
}  # O(1)
STEPS = (20, 400, 5000)  # O(1)
ACCEPT = {"status": [200, 201, 202, 204, 400, 404, 405, 411, 413, 414, 415, 422]}  # O(1)

PROBES = []  # O(1)
for k, v in LITERALS.items():  # O(e) iterations
    PROBES.append(P(f"call_{k}", "call", lambda n, ctx, v=v: v))  # O(1)
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, v=v: ([v], "")))  # O(1)
    PROBES.append(P(f"stdin_{k}", "run", lambda n, ctx, v=v: ([], v)))  # O(1)
    PROBES.append(P(f"body_{k}", "request",  # O(1)
                    lambda n, ctx, v=v: ("POST", ctx.good_path, '{"n": %s}' % v), expect=ACCEPT))  # O(1)
for k, f in SIZED.items():  # O(e) iterations
    PROBES.append(P(f"call_{k}", "call", lambda n, ctx, f=f: f(n), STEPS))  # O(1) to build; lambda O(n) per call
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, f=f: ([f(n)], ""), STEPS))  # O(1) to build; lambda O(n) per call
    PROBES.append(P(f"body_{k}", "request",  # O(1)
                    lambda n, ctx, f=f: ("POST", ctx.good_path, '{"n": %s}' % f(n)),  # O(n) per call (lambda)
                    STEPS, expect=ACCEPT))  # O(1)
