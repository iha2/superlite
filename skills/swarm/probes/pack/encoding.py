"""encoding: lone surrogates, a BOM, invalid UTF-8, non-ASCII digits and whitespace."""
# Complexity notation:
#   t = length of one probe text (constants, at most about 1500), e = entries in a table (11 texts, 4 byte strings, 4 paths)
from pack._common import P  # O(1)

CATEGORY = "encoding"  # O(1)

# Safe for every entry kind: encodable as UTF-8, so the adapter itself cannot fail on them.
TEXTS = {  # O(1)
    "bom_prefix": "﻿1",  # O(1)
    "nul_inside": "a\x00b",  # O(1)
    "arabic_digits": "١٢٣",  # O(1)
    "fullwidth_digits": "１２３",  # O(1)
    "unicode_whitespace": "1 + 1　",  # O(1)
    "zero_width": "1​2‍3",  # O(1)
    "rtl_override": "‮1+1",  # O(1)
    "combining_marks": "e" + "́" * 500,  # O(1)
    "astral_chars": "\U0001f600" * 300,  # O(1)
    "control_chars": "".join(chr(i) for i in range(1, 32) if i not in (10, 13)),  # O(1)
    "replacement_char": "�" * 50,  # O(1)
}  # O(1)
RAW_BYTES = {  # O(1)
    "invalid_utf8": b"\xff\xfe\x00\x80\xc0\xaf",  # O(1)
    "overlong_nul": b"\xc0\x80",  # O(1)
    "truncated_sequence": b"1+\xe2\x82",  # O(1)
    "bom_bytes_json": b"\xef\xbb\xbf{}",  # O(1)
}  # O(1)
ACCEPT = {"status": [200, 201, 202, 204, 400, 404, 405, 411, 413, 414, 415, 422]}  # O(1)

PROBES = [P("call_lone_high_surrogate", "call", lambda n, ctx: "\ud800"),  # O(1)
          P("call_lone_low_surrogate", "call", lambda n, ctx: "1\udc00"),  # O(1)
          P("call_latin1_as_text", "call", lambda n, ctx: "\xff\xfe\xc0")]  # O(1)
for k, v in TEXTS.items():  # O(e) iterations
    PROBES.append(P(f"call_{k}", "call", lambda n, ctx, v=v: v))  # O(1)
    PROBES.append(P(f"arg_{k}", "run", lambda n, ctx, v=v: ([v.replace("\x00", "")], "")))  # O(1) to build; lambda O(t) per call
    PROBES.append(P(f"stdin_{k}", "run", lambda n, ctx, v=v: ([], v)))  # O(1)
    PROBES.append(P(f"body_{k}", "request",  # O(1)
                    lambda n, ctx, v=v: ("POST", ctx.good_path, v), expect=ACCEPT))  # O(1)
for k, v in RAW_BYTES.items():  # O(e) iterations
    PROBES.append(P(f"stdin_bytes_{k}", "run", lambda n, ctx, v=v: ([], v)))  # O(1)
    PROBES.append(P(f"body_bytes_{k}", "request",  # O(1)
                    lambda n, ctx, v=v: ("POST", ctx.good_path, v), expect=ACCEPT))  # O(1)
for k, v in {"percent_invalid_utf8": "/%ff%fe", "percent_overlong": "/%c0%af",  # O(e) iterations
             "percent_nul": "/%00", "percent_bom": "/%ef%bb%bf"}.items():  # O(1)
    PROBES.append(P(f"path_{k}", "request", lambda n, ctx, v=v: ("GET", v, None), expect=ACCEPT))  # O(1)
