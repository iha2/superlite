# Sitegen-core finish-first: ff2–ff4 average

Three fresh preapproved runs (`BENCH_BUDGET=20`, arm `superlite`, finish-first packing). Compared to prior ff1 and old-formula p2.

| metric | ff2 | ff3 | ff4 | **avg** | ff1 | p2 |
|---|---:|---:|---:|---:|---:|---:|
| wall_s | 691.6 | 1180.3 | 720.2 | **864.0** | 723.4 | 580.8 |
| cost_usd | 4.6 | 6.3 | 4.4 | **5.1** | 4.8 | 4.0 |
| api_s | 1119.7 | 1562.1 | 1124.6 | **1268.8** | 1186.7 | 964.5 |
| implementer_dispatches | 6 | 6 | 6 | **6** | 6 | 3 |
| max_concurrent_agents | 6 | 5 | 6 | **5.7** | 6 | 5 |
| first_implementer_s | 265.7 | 221.0 | 211.2 | **232.6** | 257.9 | 159.1 |
| build_span_s | 230.9 | 318.9 | 162.5 | **237.4** | 135.9 | 244.9 |
| last_build_end_s | 496.6 | 539.9 | 373.7 | **470.1** | 393.8 | 404.0 |
| tail_after_build_s | 185.2 | 630.5 | 335.4 | **383.7** | 320.4 | 167.5 |
| post_final_s | 71.2 | 473.7 | 140.1 | **228.3** | 186.8 | 83.5 |
| rule_blocks | 4 | 5 | 8 | **5.7** | 11 | 5 |
| agent_dispatches | 13 | 15 | 18 | **15.3** | 20 | 16 |
| final_review_s | 22.4 | 19.0 | 28.8 | **23.4** | 23.2 | 22.9 |
| reviews_per_task | 0.6 | 0.8 | 2.0 | **1.1** | 4.0 | 0.8 |
| prime_hash_mismatches | 4 | 5 | 11 | **6.7** | 12 | 5 |

## Quality
- ff2: hidden 56/56; 133 passed in 0.14s
- ff3: hidden 56/56; 259 passed in 0.95s
- ff4: hidden 56/56; 164 passed in 0.24s

## Takeaway
- Finish-first avg wall **864.0s** / **$5.1** over 3 reps (range wall 691.6–1180.3, cost 4.38–6.34).
- Still packs hard: avg **6** implementer dispatches, peak concurrency **5.7**.
- Avg first implementer **232.6s**, build span **237.4s**, post-build tail **383.7s**.
- vs p2 (single run): wall 580.8s / $3.99 — finish-first still slower/costlier on average despite aiming to shrink F.
- Quality: all three **56/56** hidden.
