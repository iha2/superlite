"""numeric: 1e999, NaN, inf, huge ints, division by zero."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack._common import P

CATEGORY = "numeric"

LITERALS = {
    "exp_overflow": "1e999", "neg_exp_overflow": "-1e999", "exp_underflow": "1e-999",
    "nan": "nan", "nan_upper": "NaN", "inf": "inf", "neg_infinity": "-Infinity",
    "neg_zero": "-0", "div_zero": "1/0", "zero_div_zero": "0/0", "mod_zero": "1%0",
    "float_mul_overflow": "1e308*10", "float_sum": "0.1+0.2", "int_min": "-9223372036854775808",
    "int_overflow": "9223372036854775808", "huge_exponent": "1e999999999",
}
SIZED = {
    "huge_int": lambda n: "9" * n,
    "neg_huge_int": lambda n: "-" + "9" * n,
    "long_fraction": lambda n: "0." + "9" * n,
    "huge_exponent_digits": lambda n: "1e" + "9" * n,
}
STEPS = (20, 400, 5000)
ACCEPT = {"status": [200, 201, 202, 204, 400, 404, 405, 411, 413, 414, 415, 422]}

PROBES = []
for k, v in LITERALS.items():
    PROBES.append(P(f"call_{k}", "call", lambda n, ctx, v=v: v))
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, v=v: ([v], "")))
    PROBES.append(P(f"stdin_{k}", "run", lambda n, ctx, v=v: ([], v)))
    PROBES.append(P(f"body_{k}", "request",
                    lambda n, ctx, v=v: ("POST", ctx.good_path, '{"n": %s}' % v), expect=ACCEPT))
for k, f in SIZED.items():
    PROBES.append(P(f"call_{k}", "call", lambda n, ctx, f=f: f(n), STEPS))
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, f=f: ([f(n)], ""), STEPS))
    PROBES.append(P(f"body_{k}", "request",
                    lambda n, ctx, f=f: ("POST", ctx.good_path, '{"n": %s}' % f(n)),
                    STEPS, expect=ACCEPT))
