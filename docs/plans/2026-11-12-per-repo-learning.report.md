# superlite build report

projection: fixed forecast saved at lint 2026-11-12T04:21:08 (plan edited since)

                         Projected    Actual  Delta
wall-clock (min)               8.0      28.2  +20.2 (+252%)
agent cost (USD, +rework)      1.42      3.02  +1.60 (+113%)
task-minutes                  20.9      53.5  +32.6 (+156%)
agents                          11         8  -3 (-27%)
cost split: agents $2.77, rework $0.25, coordinator $23.93, total $26.95
concurrency: peak 4 at once (projected 5); critical path actual T4 T7 T8; projected T3 > T5 > T7 > T8 (8.0 min)
model mix: haiku 1 tasks $0.03, sonnet 10 tasks $2.95, unknown 1 tasks $0.04, coordinator opus $23.93
quality: tests 40 passed, 0 failed; first-time pass n/a; rework dispatches 2; open incidents 41

task  model      pred  actual  delta             cost
T9    sonnet      2.6     8.0  +5.4 (+208%)    $0.634
T4    sonnet      2.0     6.8  +4.8 (+234%)    $0.414
T11   sonnet      2.2     6.6  +4.4 (+198%)    $0.202
T1    ?           1.5     0.0  -1.5 (-100%)    $0.036
T7    sonnet      1.9     2.7  +0.8 (+44%)     $0.394
(+7 smaller misses; `superlite-cli report --json` lists all)

agent cost table:
| agent | model | wall_min | tokens | usd |
| --- | --- | --- | --- | --- |
| a452431e1e84fb6cf | sonnet | 4.2433 | 425221 | 0.2108 |
| a5319b61568fe9366 | sonnet | 13.2479 | 3331689 | 0.9364 |
| a6df12fd7c2117760 | haiku | 15.4115 | 1066538 | 0.0293 |
| a9efee59680e62081 | sonnet | 29907936.1401 | 0 | 0.0 |
| a9f642f24f2069167 | sonnet | 17.0965 | 1836350 | 0.6157 |
| ac1709099454b0314 | sonnet | 14.7446 | 4020980 | 1.1917 |
| af4a47a4325afb1df | sonnet | 0.4792 | 38893 | 0.0355 |
| afebc6d08828e6014 | opus | 0.4902 | 34037 | 0.1783 |
| totals |  | 29908001.8533 | 10753708 | 3.1977 |
cost source: est

takeaway: ran 252% slower than projected; cost 113% over; biggest miss T9
