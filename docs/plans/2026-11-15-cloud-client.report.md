# superlite build report

projection: fixed forecast saved at lint 2026-11-15T19:33:31 (plan edited since)

                         Projected    Actual  Delta
wall-clock (min)              30.8      34.0  +3.2 (+10%)
agent cost (USD, +rework)      1.36      1.99  +0.62 (+46%)
task-minutes                 131.1     118.6  -12.5 (-10%)
agents                          15        23  +8 (+53%)
cost split: agents $1.91, rework $0.08, coordinator $77.78, total $79.77
concurrency: peak 8 at once (projected 10); critical path actual T14; projected T3 > T15 (30.8 min)
model mix: haiku 3 tasks $0.18, sonnet 10 tasks $1.81, coordinator opus $77.78, coordinator sonnet $0.00
quality: tests 154 passed, 0 failed; first-time pass n/a; rework dispatches 1; open incidents 103

task  model      pred  actual  delta             cost
T14   haiku       5.7    34.0  +28.3 (+496%)   $0.114
T6    haiku       5.8    31.3  +25.6 (+444%)   $0.045
T3    sonnet     18.3     3.4  -15.0 (-82%)    $0.187
T4    sonnet     12.4     0.6  -11.8 (-95%)    $0.089
T15   sonnet     12.4     1.7  -10.7 (-86%)    $0.234
(+8 smaller misses; `superlite-cli report --json` lists all)

agent cost table:
| agent | model | wall_min | tokens | usd |
| --- | --- | --- | --- | --- |
| a10a5718cb92d7974 | sonnet | 29913194.9129 | 0 | 0.0 |
| a1f37587621611d9f | sonnet | 1.9071 | 326083 | 0.1458 |
| a24bb42ff89f1e2d0 | sonnet | 14.6609 | 828773 | 0.3459 |
| a2a699a01c1f4856c | haiku | 15.7448 | 573405 | 0.0201 |
| a2c9d33d3278aeb90 | sonnet | 29913153.3673 | 0 | 0.0 |
| a340541c0d3701b11 | sonnet | 2.3753 | 495835 | 0.1945 |
| a4c89e34d593c2ef0 | sonnet | 29913188.5403 | 0 | 0.0 |
| a5a80cffb281f06ef | haiku | 33.9863 | 1785942 | 0.0358 |
| a67d98178e70070ac | haiku | 31.3829 | 2312902 | 0.0452 |
| a67db32848ccfd27a | sonnet | 1.4703 | 311163 | 0.1704 |
| a705c8733ea3bb52b | sonnet | 29913165.2348 | 0 | 0.0 |
| a71d7ba01f18af6f9 | sonnet | 29913203.7403 | 0 | 0.0 |
| a78739d7d8612e01f | sonnet | 0.8142 | 138142 | 0.0934 |
| a7884ba446069183e | sonnet | 11.5327 | 588935 | 0.3911 |
| a822f6f1fc7ab1879 | sonnet | 1.1083 | 215884 | 0.1311 |
| a9487297a5cacfbe1 | sonnet | 29913170.0518 | 0 | 0.0 |
| ab2674458f2c2bdb7 | sonnet | 29913138.9201 | 0 | 0.0 |
| ab6734ee4387c1c51 | sonnet | 22.6826 | 714141 | 0.4383 |
| ac0c491e666b5d8b9 | sonnet | 0.9042 | 131292 | 0.0786 |
| ac392bd564272dc41 | sonnet | 29913160.6999 | 0 | 0.0 |
| ac42726ada075a9bb | sonnet | 29913156.3762 | 0 | 0.0 |
| acb675e688ec3ace5 | sonnet | 20.3009 | 1752583 | 0.6301 |
| adde1f9d346612de7 | opus | 0.5819 | 33991 | 0.193 |
| totals |  | 269218691.296 | 10209071 | 2.9133 |
cost source: est

trend (last 3 runs): first-time pass n/a -> n/a -> n/a; findings 0 -> 0 -> 10; rework+fixer cost 2% -> 8% -> 4%
lessons: 4 cached, 0 proven
takeaway: ran 10% slower than projected; cost 46% over; biggest miss T14
