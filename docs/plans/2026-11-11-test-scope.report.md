# superlite build report

projection: fixed forecast saved at lint 2026-11-11T10:05:51 (plan edited since)

                         Projected    Actual  Delta
wall-clock (min)               4.1      47.6  +43.5 (+1064%)
agent cost (USD, +rework)      0.48      1.51  +1.03 (+217%)
task-minutes                  11.7      14.7  +3.1 (+26%)
agents                           7        20  +13 (+186%)
cost split: agents $0.70, rework $0.81, coordinator $9.94, total $11.45
concurrency: peak 5 at once (projected 6); critical path actual T2 T3; projected T1 > T3 (4.1 min)
model mix: haiku 2 tasks $0.04, sonnet 4 tasks $1.42, unknown 1 tasks $0.05, coordinator opus $9.94
quality: tests 46 passed, 0 failed; first-time pass n/a; rework dispatches 5; open incidents 42

task  model      pred  actual  delta             cost
T4    haiku       1.4     3.4  +2.0 (+143%)    $0.017
T2    haiku       1.4     3.2  +1.8 (+125%)    $0.024
T5    ?           1.4     0.0  -1.4 (-100%)    $0.047
T3    sonnet      2.0     3.0  +0.9 (+46%)     $0.864
T1    sonnet      2.1     1.6  -0.4 (-21%)     $0.210
(+2 smaller misses; `superlite-cli report --json` lists all)

agent cost table:
| agent | model | wall_min | tokens | usd |
| --- | --- | --- | --- | --- |
| a29508725cfbe7c13 | sonnet | 29906887.808 | 0 | 0.0 |
| a2ce2c45a8cb6f57c | sonnet | 29906885.2649 | 0 | 0.0 |
| a3f964dc1fe629790 | sonnet | 1.6525 | 215777 | 0.2097 |
| a49ed1ab92e1cc329 | sonnet | 1.8368 | 88644 | 0.1014 |
| a4b4a3e3b913e7b2a | sonnet | 1.8137 | 163261 | 0.1163 |
| a729a161f74a9f8b4 | sonnet | 10.4815 | 893651 | 0.3087 |
| a7953f0e7080e7cc9 | sonnet | 8.1665 | 1050042 | 0.3366 |
| a91e9f597bc23eeaa | sonnet | 7.1663 | 497748 | 0.2364 |
| a9bfd02ab6483c8c9 | sonnet | 29906888.3083 | 0 | 0.0 |
| ab8f37a9904c929d3 | sonnet | 29906833.838 | 0 | 0.0 |
| abfb18e006853755f | sonnet | 29906867.767 | 0 | 0.0 |
| ac6de608bc297c1df | sonnet | 29906828.855 | 207943 | 0.1424 |
| ac796ae20b888f746 | sonnet | 29906897.6476 | 0 | 0.0 |
| acae884f63bde0a60 | haiku | 29906830.2471 | 956062 | 0.0238 |
| ad19483358401fcbb | sonnet | 0.8281 | 45360 | 0.047 |
| ad4a026fafe106248 | sonnet | 1.3026 | 135411 | 0.1051 |
| adbe206cca34d7525 | sonnet | 29906886.3254 | 0 | 0.0 |
| ae1ed35ae2c1d85af | opus | 0.4068 | 35148 | 0.1722 |
| af7fdf1e46fa5815d | haiku | 29906830.4627 | 517119 | 0.0168 |
| afe7d753f38ac7859 | sonnet | 2.9578 | 368951 | 0.2021 |
| totals |  | 299068673.1366 | 5175117 | 2.0185 |
cost source: est

takeaway: ran 1064% slower than projected; cost 217% over; biggest miss T4
