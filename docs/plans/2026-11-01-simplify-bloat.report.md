# superlite build report

projection: fixed forecast saved at lint 2026-11-01T09:25:06 (plan edited since)

                         Projected    Actual  Delta
wall-clock (min)              24.3     237.1  +212.7 (+874%)
agent cost (USD, +rework)      6.81     29.09  +22.27 (+327%)
task-minutes                  62.1     489.0  +427.0 (+688%)
agents                          19        53  +34 (+179%)
cost split: agents $21.77, rework $7.32, coordinator $49.91, total $78.99
concurrency: peak 8 at once (projected 7); critical path actual T1 T2 T3 T5 T8 T9 T14 T16; projected T1 > T2 > T3 > T5 > T8 > T9 > T14 > T16 (24.3 min)
model mix: sonnet 19 tasks $21.77, unknown 17 tasks $7.32, coordinator opus $49.91, coordinator sonnet $0.00
quality: tests 1332 passed, 1 failed; first-time pass n/a; rework dispatches 17; open incidents 802

task  model      pred  actual  delta             cost
T16   sonnet      3.8    74.0  +70.2 (+1853%)  $3.036
T17   sonnet      3.0    71.4  +68.4 (+2256%)  $0.823
T8    sonnet      4.2    55.8  +51.7 (+1235%)  $2.812
T6    sonnet      4.2    40.3  +36.1 (+860%)   $2.676
T2    sonnet      2.1    36.6  +34.5 (+1627%)  $1.184
(+31 smaller misses; `sl report --json` lists all)

agent cost table:
| agent | model | wall_min | tokens | usd |
| --- | --- | --- | --- | --- |
| a00bc43a689bca2bd | sonnet | 29892476.1879 | 0 | 0.0 |
| a00ecdb62bed36196 | sonnet | 15.9173 | 5041372 | 1.1725 |
| a02b1efafa918e185 | sonnet | 4.2215 | 553310 | 0.3207 |
| a08a2dd815a2fb2af | sonnet | 1.8547 | 211140 | 0.1084 |
| a0a9f787faf9a6281 | sonnet | 10.9258 | 675727 | 0.3257 |
| a0af0f5f9ced95217 | sonnet | 1.7079 | 45513 | 0.0355 |
| a11b84cf8c61d33b3 | sonnet | 8.0925 | 1142077 | 0.3765 |
| a1278b868f0ddc0a8 | sonnet | 5.312 | 1005324 | 0.5063 |
| a131f8aaf6797165f | sonnet | 29892134.2361 | 0 | 0.0 |
| a1500fe9693868587 | sonnet | 6.4252 | 1568864 | 0.6143 |
| a1ecf50fbaa418e80 | sonnet | 1.355 | 117200 | 0.1231 |
| a1fcd237447e8b4fb | sonnet | 0.3675 | 48921 | 0.0364 |
| a215595d6b431f178 | sonnet | 7.3893 | 1409677 | 0.5547 |
| a2608968f249273c9 | sonnet | 6.1842 | 216561 | 0.1317 |
| a2672a5eb9aaba2c6 | sonnet | 3.3035 | 386813 | 0.1677 |
| a272dee5df2789974 | sonnet | 41.6191 | 11263520 | 2.7931 |
| a37e9213ff8abee3c | sonnet | 0.0 | 0 | 0.0 |
| a3a2eb05fb79a322c | sonnet | 11.9418 | 2928169 | 0.7894 |
| a3dd6a000e0a90d62 | sonnet | 0.7583 | 86940 | 0.08 |
| a4588f6ff47fe4da0 | sonnet | 44.9577 | 5630050 | 2.4862 |
| a4a769b285486db42 | sonnet | 10.884 | 1841623 | 0.5683 |
| a53edce1eda1ed3cb | sonnet | 9.5691 | 4608514 | 1.1394 |
| a5439c8f3ea362dac | sonnet | 0.5014 | 47186 | 0.035 |
| a5f725e6b94d6521c | sonnet | 11.4308 | 227088 | 0.184 |
| a60d7b2c40f9b5cb2 | sonnet | 17.9414 | 2042188 | 0.6363 |
| a668108c4c3835f1e | sonnet | 74.0306 | 9013995 | 3.0362 |
| a6743c14e1e2bf541 | sonnet | 12.9122 | 2962400 | 1.0351 |
| a70eaaede5c4b6f45 | sonnet | 5.417 | 827888 | 0.2609 |
| a711af12596d4b42a | sonnet | 0.7611 | 151327 | 0.0737 |
| a7175d97c83557f55 | sonnet | 4.2108 | 410100 | 0.2197 |
| a79919b5f0d416935 | sonnet | 15.9064 | 728514 | 0.3636 |
| a79ff182d2f57b2be | sonnet | 48.6832 | 2396278 | 1.2524 |
| a7dfef77f01202265 | sonnet | 5.3951 | 772817 | 0.27 |
| a8635fc0a49f7907a | sonnet | 5.5629 | 1971947 | 0.5794 |
| a8bc012790b48b71e | sonnet | 7.278 | 2094655 | 0.6036 |
| a8bd5ac393692603e | sonnet | 2.186 | 103536 | 0.0791 |
| a90b4022b5632488f | sonnet | 1.102 | 144802 | 0.111 |
| a94a8e7859968125c | sonnet | 3.3958 | 348398 | 0.1996 |
| a9972bfbf4bdba4ce | sonnet | 1.374 | 82109 | 0.0729 |
| a99e55f347aa86cb1 | sonnet | 24.5165 | 913258 | 0.5511 |
| aa2460b3f2907c943 | sonnet | 10.3203 | 2081003 | 0.5735 |
| aa68f6dc5a82ea1ac | sonnet | 32.2409 | 7987706 | 2.2993 |
| ab7731fbe1ceef8af | sonnet | 0.9128 | 96799 | 0.0804 |
| ab8e5600e8e984793 | sonnet | 71.5183 | 1959830 | 0.8227 |
| ac9671a1ea404ec20 | sonnet | 3.2188 | 372404 | 0.172 |
| acc8fb6580d9a15de | sonnet | 1.3925 | 152347 | 0.0962 |
| ace373fe1bdd9a123 | sonnet | 7.6175 | 1530372 | 0.6892 |
| acf6d84ed1621253c | sonnet | 25.7366 | 3315509 | 1.2608 |
| adbdec8b29dedea9f | sonnet | 0.1052 | 348457 | 0.1574 |
| ae4b2c5836513aefa | opus | 0.4303 | 33507 | 0.1725 |
| ae80ac17bcdd334a7 | sonnet | 9.5542 | 280814 | 0.1684 |
| af332bc6e20025403 | sonnet | 13.644 | 1452054 | 0.7394 |
| af47044764744df07 | sonnet | 1.5801 | 321848 | 0.1697 |
| totals |  | 59785224.0871 | 83952451 | 29.295 |
cost source: est

takeaway: ran 874% slower than projected; cost 327% over; biggest miss T16
