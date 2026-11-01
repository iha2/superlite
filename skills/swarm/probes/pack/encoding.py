"""encoding: lone surrogates, a BOM, invalid UTF-8, non-ASCII digits and whitespace."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack._common import P

CATEGORY = "encoding"

# Safe for every entry kind: encodable as UTF-8, so the adapter itself cannot fail on them.
TEXTS = {
    "bom_prefix": "﻿1",
    "nul_inside": "a\x00b",
    "arabic_digits": "١٢٣",
    "fullwidth_digits": "１２３",
    "unicode_whitespace": "1 + 1　",
    "zero_width": "1​2‍3",
    "rtl_override": "‮1+1",
    "combining_marks": "e" + "́" * 500,
    "astral_chars": "\U0001f600" * 300,
    "control_chars": "".join(chr(i) for i in range(1, 32) if i not in (10, 13)),
    "replacement_char": "�" * 50,
}
RAW_BYTES = {
    "invalid_utf8": b"\xff\xfe\x00\x80\xc0\xaf",
    "overlong_nul": b"\xc0\x80",
    "truncated_sequence": b"1+\xe2\x82",
    "bom_bytes_json": b"\xef\xbb\xbf{}",
}
ACCEPT = {"status": [200, 201, 202, 204, 400, 404, 405, 411, 413, 414, 415, 422]}

PROBES = [P("call_lone_high_surrogate", "call", lambda n, ctx: "\ud800"),
          P("call_lone_low_surrogate", "call", lambda n, ctx: "1\udc00"),
          P("call_latin1_as_text", "call", lambda n, ctx: "\xff\xfe\xc0")]
for k, v in TEXTS.items():
    PROBES.append(P(f"call_{k}", "call", lambda n, ctx, v=v: v))
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, v=v: ([v.replace("\x00", "")], "")))
    PROBES.append(P(f"stdin_{k}", "run", lambda n, ctx, v=v: ([], v)))
    PROBES.append(P(f"body_{k}", "request",
                    lambda n, ctx, v=v: ("POST", ctx.good_path, v), expect=ACCEPT))
for k, v in RAW_BYTES.items():
    PROBES.append(P(f"stdin_bytes_{k}", "run", lambda n, ctx, v=v: ([], v)))
    PROBES.append(P(f"body_bytes_{k}", "request",
                    lambda n, ctx, v=v: ("POST", ctx.good_path, v), expect=ACCEPT))
for k, v in {"percent_invalid_utf8": "/%ff%fe", "percent_overlong": "/%c0%af",
             "percent_nul": "/%00", "percent_bom": "/%ef%bb%bf"}.items():
    PROBES.append(P(f"path_{k}", "request", lambda n, ctx, v=v: ("GET", v, None), expect=ACCEPT))
