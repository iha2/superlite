# Sitegen-core: finish-first (ff1) vs prior (p2)

Same task `sitegen-core`, arm `superlite`, `BENCH_BUDGET=20`, preapproved.

| metric | p2 (old max(B+A,F/T)) | ff1 (finish-first) |
|---|---:|---:|
| wall_s | 580.8 | 723.4 |
| cost_usd | 3.99 | 4.75 |
| api_s | 964.5 | 1186.7 |
| hidden | 56/56 | 56/56 |
| implementer_dispatches | 3 | 6 |
| max_concurrent_agents | 5 | 6 |
| first_implementer_s | 159.1 | 257.9 |
| build_span_s | 244.9 | 135.9 |
| last_build_end_s | 404.0 | 393.8 |
| tail_after_build_s | 167.5 | 320.4 |
| rule_blocks | 5 | 11 |

## Takeaway
- **Build wave shorter** once agents start (ff1 build span ~136s vs p2 ~245s) with **6 concurrent implementers** (vs 3 implementer dispatches / peak 5).
- **Overall wall slower** (723s vs 581s): first implementer started later (~258s vs ~159s) and post-build tail grew (~320s vs ~168s).
- **Quality unchanged:** hidden **56/56**; own tests 125 passed.
- Cost **$4.75** vs **$3.99**.

