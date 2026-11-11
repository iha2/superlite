# superlite build report

projection: fixed forecast saved at lint 2026-11-10T21:32:05 (plan edited since)

                         Projected    Actual  Delta
wall-clock (min)               3.7      70.2  +66.6 (+1823%)
agent cost (USD, +rework)      0.61      1.31  +0.70 (+114%)
task-minutes                  12.3      37.8  +25.5 (+207%)
agents                           8        35  +27 (+338%)
feature flow (run-archive): two builds
  prototype   time 3.5 -> 10.6 min, cost $0.34 -> $0.87 (66% of the integration, cap 25%)
  integration time 3.7 -> 70.2 min, cost $0.61 -> $1.31
cost split: agents $0.69, rework $0.61, coordinator $12.97, total $14.28
concurrency: peak 5 at once (projected 7); critical path actual T2; projected T1 > T8 (3.7 min)
model mix: haiku 2 tasks $0.03, sonnet 4 tasks $1.16, unknown 1 tasks $0.11, coordinator opus $12.97, coordinator sonnet $0.00
quality: tests 201 passed, 0 failed; first-time pass n/a; rework dispatches 7; open incidents 34

task  model      pred  actual  delta             cost
T7    haiku       1.4     4.0  +2.6 (+188%)    $0.010
T2    sonnet      1.5     3.5  +2.0 (+133%)    $0.420
T3    sonnet      1.5     3.4  +1.9 (+131%)    $0.486
T4    ?           1.5     0.0  -1.5 (-100%)    $0.110
T8    sonnet      2.1     1.1  -0.9 (-45%)     $0.097
(+2 smaller misses; `superlite-cli report --json` lists all)

agent cost table:
| agent | model | wall_min | tokens | usd |
| --- | --- | --- | --- | --- |
| a06575b4ca0728b35 | sonnet | 1.1538 | 112983 | 0.0968 |
| a1a0e9ea4039feb44 | opus | 2.5914 | 0 | 0.0 |
| a244bb130db75775d | sonnet | 0.721 | 70984 | 0.0626 |
| a262bf8bf05890de6 | sonnet | 29906076.7578 | 0 | 0.0 |
| a288c19f05edb434c | sonnet | 29906079.7013 | 81226 | 0.0611 |
| a2d8b73485fdffd0e | sonnet | 29906074.68 | 0 | 0.0 |
| a40c3cc48be93af1c | sonnet | 1.096 | 85778 | 0.0833 |
| a500905139cfa9c61 | sonnet | 29905997.2331 | 0 | 0.0 |
| a50709b49b71e25c6 | sonnet | 29906076.2268 | 0 | 0.0 |
| a52e4ab53dde12f36 | sonnet | 1.2849 | 140242 | 0.1027 |
| a574e464d5a446481 | sonnet | 29906077.7451 | 0 | 0.0 |
| a5b51e693784d630b | sonnet | 3.5962 | 540732 | 0.2545 |
| a5de0089a69f8f60d | sonnet | 29906077.4703 | 487471 | 0.2566 |
| a66541b092b32ff7c | sonnet | 29906079.7723 | 53063 | 0.0419 |
| a67495d593753739b | sonnet | 0.8657 | 92507 | 0.0791 |
| a6871b86f86018b49 | sonnet | 29906101.744 | 0 | 0.0 |
| a7350444b53e2396f | opus | 1.1287 | 0 | 0.0 |
| a81b488263e83c1be | sonnet | 29906143.5359 | 189914 | 0.1234 |
| a82ed952961bc6fbc | sonnet | 0.6008 | 56513 | 0.0474 |
| a8522ddf4f6f7545d | sonnet | 29906110.7123 | 0 | 0.0 |
| a8840c1aacb9edd62 | sonnet | 29906004.3493 | 0 | 0.0 |
| a8fefc5c142653ce2 | sonnet | 29906143.1589 | 385808 | 0.1942 |
| a9937f805f2595e23 | haiku | 4.0387 | 277051 | 0.0104 |
| a9feedd0b5ff00f89 | sonnet | 29905999.1829 | 0 | 0.0 |
| aa49486949fe70b80 | sonnet | 29906074.705 | 0 | 0.0 |
| aadcc772c17d40013 | sonnet | 29905999.4684 | 0 | 0.0 |
| ab1a08771b63a3cd9 | sonnet | 29906074.8381 | 162763 | 0.1035 |
| abe04991647ebbb45 | haiku | 25.0615 | 1078591 | 0.0244 |
| ad63474914a98f0e4 | sonnet | 29906075.2993 | 0 | 0.0 |
| addb5cc9b50fd55b7 | sonnet | 29906146.8053 | 0 | 0.0 |
| ae1577ca50407c377 | opus | 0.4982 | 36231 | 0.188 |
| ae9b5b5c81a3c76da | sonnet | 29906072.8671 | 0 | 0.0 |
| af35a42cfc7fae89c | sonnet | 29906005.4273 | 0 | 0.0 |
| af4602abf862ea065 | sonnet | 29906079.1293 | 0 | 0.0 |
| afc1d87fd66c06ecf | sonnet | 29906079.5196 | 0 | 0.0 |
| totals |  | 687839692.9663 | 3851857 | 1.7299 |
cost source: est

takeaway: ran 1823% slower than projected; cost 114% over; biggest miss T7
