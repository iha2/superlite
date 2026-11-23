# superlite build report

projection: fixed forecast saved at lint 2026-11-23T15:36:43 (plan edited since)

                         Projected    Actual  Delta
wall-clock (min)              21.8      38.8  +16.9 (+78%)
agent cost (USD, +rework)      2.39      2.79  +0.41 (+17%)
task-minutes                  32.6      41.1  +8.5 (+26%)
agents                           8        15  +7 (+88%)
cost split: agents $2.20, rework $0.60, coordinator $32.89, total $35.68
concurrency: peak 4 at once (projected 2); critical path actual T0 T1 T3; projected T0 > T1 > T2 > T3 > T7 (21.8 min)
model mix: haiku 1 tasks $0.38, sonnet 5 tasks $2.30, unknown 2 tasks $0.11, coordinator opus $32.89
quality: tests 1 passed, 0 failed; first-time pass n/a; rework dispatches 3; open incidents 59

task  model      pred  actual  delta             cost
T3    sonnet      4.1    19.4  +15.3 (+371%)   $0.932
T7    ?           4.6     0.0  -4.6 (-100%)    $0.037
T5    ?           3.6     0.0  -3.6 (-100%)    $0.075
T6    sonnet      3.2     0.5  -2.7 (-83%)     $0.096
T2    sonnet      4.8     6.9  +2.1 (+43%)     $0.543
(+3 smaller misses; `superlite-cli report --json` lists all)

agent cost table:
| agent | model | wall_min | tokens | usd |
| --- | --- | --- | --- | --- |
| a0b94e3e9c49afc41 | sonnet | 29924446.0765 | 810387 | 0.3426 |
| a305c92d2b5bed09e | sonnet | 29924493.5445 | 0 | 0.0 |
| a30dc9349b20d315b | opus | 0.3618 | 25869 | 0.1339 |
| a38c26d641799ce61 | sonnet | 0.6141 | 150747 | 0.075 |
| a39c13a07ea1bd743 | sonnet | 0.2033 | 41715 | 0.0367 |
| a5448d3c71473ae6e | sonnet | 7.5488 | 1500157 | 0.4846 |
| a6a092e851fd49758 | sonnet | 29924431.0981 | 0 | 0.0 |
| a7777dd6716d62a43 | sonnet | 3.9445 | 195135 | 0.1119 |
| a88adbc83b962faad | sonnet | 29924442.2142 | 0 | 0.0 |
| abe53a0eb3b9d8555 | sonnet | 26.7668 | 3810892 | 0.9901 |
| ae4bcec934ba98d6d | sonnet | 3.824 | 387415 | 0.203 |
| aed13a259e7999614 | sonnet | 6.4813 | 1319901 | 0.6448 |
| af38f11b1fa3ac867 | haiku | 1.5753 | 228464 | 0.0162 |
| af841adf480128698 | sonnet | 29924443.3915 | 0 | 0.0 |
| aff67bb501939f3e2 | sonnet | 29924444.2289 | 0 | 0.0 |
| totals |  | 179546751.8736 | 8470682 | 3.0388 |
cost source: est

trend (last 3 runs): first-time pass n/a -> n/a -> n/a; findings 0 -> 3 -> 7; rework+fixer cost 54% -> 6% -> 21%
lessons: 2 cached, 0 proven
takeaway: ran 78% slower than projected; cost 17% over; biggest miss T3
