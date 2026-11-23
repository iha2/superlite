# superlite build report

projection: no saved forecast, recomputed from the plan as it is now

                         Projected    Actual  Delta
wall-clock (min)               7.9       n/a  n/a
agent cost (USD, +rework)      0.21      0.00  -0.21 (-100%)
task-minutes                  18.5       0.0  -18.5 (-100%)
agents                           4        12  +8 (+200%)
feature flow (close-session): two builds
  prototype   time n/a -> n/a min, cost $n/a -> $n/a
  integration time 7.9 -> n/a min, cost $0.21 -> $0.00
cost split: agents $0.00, rework $0.00, coordinator $4.10, total $4.10
concurrency: peak 0 at once (projected 3); critical path actual n/a; projected T3 (7.9 min)
model mix: n/a, coordinator opus $4.10
quality: tests 1503 passed, 0 failed; first-time pass n/a; rework dispatches 0; open incidents 0

task  model      pred  actual  delta             cost

agent cost table:
| agent | model | wall_min | tokens | usd |
| --- | --- | --- | --- | --- |
| a47d105175546a0b8 | sonnet | 29924426.1951 | 0 | 0.0 |
| a5ca471788387e1ab | sonnet | 29924436.6429 | 0 | 0.0 |
| a7da646a445e43900 | sonnet | 29924406.8608 | 0 | 0.0 |
| a8fb1eaf072fb4483 | sonnet | 29924448.2927 | 0 | 0.0 |
| a97da0f2bb0e6032b | sonnet | 29924428.3671 | 0 | 0.0 |
| a9bda012e92d72b2e | sonnet | 29924436.6126 | 0 | 0.0 |
| ac0413211a162304c | sonnet | 29924403.166 | 0 | 0.0 |
| ad6bba75577789c25 | opus | 1.2597 | 474728 | 0.4447 |
| aedd0afcc6523c925 | opus | 0.3687 | 19121 | 0.0905 |
| aef1d991c0b576c25 | sonnet | 29924412.6193 | 0 | 0.0 |
| af0638610ddc58f2c | sonnet | 29924404.5544 | 426970 | 0.2137 |
| afa4697ff7891d638 | sonnet | 29924442.5913 | 0 | 0.0 |
| totals |  | 299244247.5306 | 920819 | 0.7489 |
cost source: est

takeaway: cost 100% under
