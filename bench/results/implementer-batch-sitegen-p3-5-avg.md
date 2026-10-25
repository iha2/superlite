# Sitegen-core: p2 formula avg (p3–p5) vs finish-first avg (ff2–ff4)

Same task `sitegen-core`, arm `superlite`, `BENCH_BUDGET=20`, preapproved. Three fresh reps each.

| metric | p3 | p4 | p5 | **p avg** | ff2 | ff3 | ff4 | **ff avg** | p2 | ff1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| wall_s | 582.2 | 728.1 | 753.0 | **687.8** | 691.6 | 1180.3 | 720.2 | **864.0** | 580.8 | 723.4 |
| cost_usd | 3.3 | 4.7 | 3.9 | **4.0** | 4.6 | 6.3 | 4.4 | **5.1** | 4.0 | 4.8 |
| api_s | 869.5 | 1224.9 | 1142.1 | **1078.8** | 1119.7 | 1562.1 | 1124.6 | **1268.8** | 964.5 | 1186.7 |
| implementer_dispatches | 4 | 6 | 5 | **5** | 6 | 6 | 6 | **6** | 3 | 6 |
| max_concurrent_agents | 4 | 6 | 5 | **5** | 6 | 5 | 6 | **5.7** | 5 | 6 |
| first_implementer_s | 196.9 | 248.1 | 247.7 | **230.9** | 265.7 | 221.0 | 211.2 | **232.6** | 159.1 | 257.9 |
| build_span_s | 227.7 | 247.9 | 305.4 | **260.3** | 230.9 | 318.9 | 162.5 | **237.4** | 244.9 | 135.9 |
| last_build_end_s | 424.6 | 496.0 | 553.1 | **491.2** | 496.6 | 539.9 | 373.7 | **470.1** | 404.0 | 393.8 |
| tail_after_build_s | 148.0 | 223.3 | 184.1 | **185.1** | 185.2 | 630.5 | 335.4 | **383.7** | 167.5 | 320.4 |
| post_final_s | 32.6 | 105.1 | 65.8 | **67.8** | 71.2 | 473.7 | 140.1 | **228.3** | 83.5 | 186.8 |
| rule_blocks | 1 | 2 | 2 | **1.7** | 4 | 5 | 8 | **5.7** | 5 | 11 |
| agent_dispatches | 8 | 14 | 10 | **10.7** | 13 | 15 | 18 | **15.3** | 16 | 20 |
| final_review_s | 23.7 | 24.2 | 20.4 | **22.8** | 22.4 | 19.0 | 28.8 | **23.4** | 22.9 | 23.2 |
| reviews_per_task | 0.7 | 0.7 | 1.0 | **0.8** | 0.6 | 0.8 | 2.0 | **1.1** | 0.8 | 4.0 |
| prime_hash_mismatches | 3 | 4 | 6 | **4.3** | 4 | 5 | 11 | **6.7** | 5 | 12 |

## Quality
- p3: hidden 56/56; 155 passed in 0.17s
- p4: hidden 56/56; 206 passed in 1.00s
- p5: hidden 56/56; 142 passed in 0.20s

## Takeaway
- **p2 formula avg** wall **687.8s** / **$4.0** (range wall 582.2–753.0).
- **finish-first avg** wall **864.0s** / **$5.1** (range wall 691.6–1180.3).
- Implementer dispatches avg **5** (p2) vs **6** (ff); first implementer **230.9s** vs **232.6s**; post-build tail **185.1s** vs **383.7s**.
- Quality: all three p-reps **56/56** hidden.
