# superlite build report

projection: fixed forecast saved at lint 2026-11-11T14:47:32 (plan edited since)

                         Projected    Actual  Delta
wall-clock (min)               4.1      52.9  +48.8 (+1179%)
agent cost (USD, +rework)      1.07      3.80  +2.73 (+255%)
task-minutes                  22.3      46.0  +23.7 (+106%)
agents                          13        66  +53 (+408%)
cost split: agents $2.52, rework $1.28, coordinator $24.14, total $27.94
concurrency: peak 6 at once (projected 10); critical path actual T9; projected T3 > T13 (4.1 min)
model mix: haiku 1 tasks $0.02, sonnet 9 tasks $3.78, coordinator opus $24.14
quality: tests 2342 passed, 0 failed; first-time pass n/a; rework dispatches 3; open incidents 218

task  model      pred  actual  delta             cost
T9    sonnet      1.8     4.9  +3.1 (+170%)    $1.396
T3    sonnet      2.1     4.6  +2.5 (+119%)    $0.457
T5    sonnet      1.8     4.1  +2.3 (+123%)    $0.259
T8    sonnet      1.8     3.8  +2.0 (+108%)    $0.254
T13   sonnet      2.0     3.2  +1.1 (+54%)     $0.293
(+5 smaller misses; `superlite-cli report --json` lists all)

agent cost table:
| agent | model | wall_min | tokens | usd |
| --- | --- | --- | --- | --- |
| a03cec541579c073b | sonnet | 29907148.8109 | 0 | 0.0 |
| a042cbb7ac1dcd322 | sonnet | 29907148.1407 | 0 | 0.0 |
| a09a95292a06ec49f | sonnet | 29906943.2917 | 0 | 0.0 |
| a194315c712ff3210 | sonnet | 29907101.0263 | 0 | 0.0 |
| a1cdea4c31c19c1b1 | sonnet | 29907101.5498 | 0 | 0.0 |
| a1d5084d8d142e933 | sonnet | 29907141.2313 | 0 | 0.0 |
| a2502e8dcdbd0c1a9 | sonnet | 29907040.5456 | 0 | 0.0 |
| a278feafdc5724f4f | sonnet | 29906766.8885 | 0 | 0.0 |
| a2ac5b701e73544c4 | sonnet | 29907010.7509 | 0 | 0.0 |
| a2f53a3898135a055 | sonnet | 29907152.9977 | 0 | 0.0 |
| a3084f0f7e3402d65 | opus | 15.5074 | 1974371 | 1.0755 |
| a32299bbcae2488c1 | sonnet | 29907163.4496 | 0 | 0.0 |
| a32558a8094775241 | sonnet | 29906957.4301 | 0 | 0.0 |
| a32cda0798b245992 | sonnet | 29906908.8486 | 0 | 0.0 |
| a3447d6c6337053cf | opus | 0.5606 | 36810 | 0.1989 |
| a36dd10f7afd2e3e1 | sonnet | 29906911.3043 | 0 | 0.0 |
| a3a292abd3f820a59 | sonnet | 29907123.2993 | 0 | 0.0 |
| a3c6caac85acb7599 | sonnet | 29907126.5173 | 1381837 | 0.6133 |
| a3cfe90d46c370ea7 | sonnet | 29907121.7445 | 0 | 0.0 |
| a3f64fff8cd5bcf31 | sonnet | 29907008.7638 | 0 | 0.0 |
| a3fd2a9408fa0307f | sonnet | 29906942.3251 | 0 | 0.0 |
| a424901820a491c55 | sonnet | 29907110.2668 | 0 | 0.0 |
| a43225a2c2d4d3454 | sonnet | 29906940.6553 | 0 | 0.0 |
| a436824a342655fc7 | sonnet | 29906982.7603 | 0 | 0.0 |
| a48d8eb077eed65bb | sonnet | 29907053.5996 | 0 | 0.0 |
| a4b7f8aacb9627227 | sonnet | 29907102.3207 | 0 | 0.0 |
| a4fefbd86bbe848b5 | sonnet | 29906919.3732 | 0 | 0.0 |
| a5097ecf21eb9997f | sonnet | 29907124.1252 | 0 | 0.0 |
| a53efbf94ad9e8bd9 | sonnet | 29907071.0071 | 0 | 0.0 |
| a558acd6e6a7729e2 | sonnet | 17.0813 | 862821 | 0.4056 |
| a57d284af2c5bf3df | sonnet | 29906892.0688 | 0 | 0.0 |
| a600e37b0f4705225 | sonnet | 29907065.7731 | 0 | 0.0 |
| a60b1a94c3aa7a1a3 | sonnet | 29906942.5331 | 0 | 0.0 |
| a6620d80994526c80 | sonnet | 29907159.7018 | 0 | 0.0 |
| a6b6afa271cec77b2 | sonnet | 29907131.5759 | 0 | 0.0 |
| a6df8134391258cfa | sonnet | 0.542 | 75273 | 0.054 |
| a6f5cb7cba2d83bd3 | sonnet | 29907056.1123 | 0 | 0.0 |
| a716a4ba2fce14f13 | sonnet | 29907015.3784 | 0 | 0.0 |
| a73caf8555332c653 | haiku | 16.2108 | 544892 | 0.0181 |
| a77f30a5618a1189e | sonnet | 29906909.965 | 0 | 0.0 |
| a7a641ae2017d5209 | sonnet | 29907113.828 | 602166 | 0.2537 |
| a7c1dbcabe83789f2 | sonnet | 29906920.292 | 0 | 0.0 |
| a7fc9061a50a9308c | sonnet | 29907147.6434 | 0 | 0.0 |
| a92f6c2baf641fb7f | sonnet | 29906908.8614 | 0 | 0.0 |
| a961e467832294a1b | sonnet | 29906911.1575 | 0 | 0.0 |
| a9c697f87b91dfc1f | sonnet | 29906895.0911 | 0 | 0.0 |
| aa9ea0abc702e73e4 | sonnet | 29907149.919 | 0 | 0.0 |
| aaaf41a376ca9dcf2 | sonnet | 29906749.8496 | 0 | 0.0 |
| ab544547373bd3b4f | sonnet | 29907128.2927 | 0 | 0.0 |
| ab9ac4279c29c7973 | sonnet | 29906890.7874 | 0 | 0.0 |
| aba582fb0fc2b16e3 | sonnet | 1.2936 | 227108 | 0.1515 |
| abb3be0899f057074 | sonnet | 29907163.4682 | 0 | 0.0 |
| abf82a21a52bbe03c | sonnet | 29906759.7264 | 0 | 0.0 |
| ac260948df1f61046 | sonnet | 29907129.3167 | 0 | 0.0 |
| ac284161232dfd4a8 | sonnet | 16.1447 | 857972 | 0.4819 |
| ac7b7cea3ac7e0688 | sonnet | 29907129.6071 | 1476036 | 0.7504 |
| ac7e46b6ddd7688cf | sonnet | 29907074.9623 | 0 | 0.0 |
| ad0d1094070592cef | sonnet | 29906893.8231 | 0 | 0.0 |
| ad25777186d7c65a5 | sonnet | 29906945.4043 | 0 | 0.0 |
| ade9e9e7361ec2cae | sonnet | 29906946.2982 | 0 | 0.0 |
| ae64085b80eedfc9c | sonnet | 29907156.0145 | 0 | 0.0 |
| ae9afeda4a047adbe | sonnet | 1.2074 | 241200 | 0.1492 |
| aeaacfcc08c493170 | sonnet | 29906903.1837 | 0 | 0.0 |
| aee056b0c2b4d79b2 | sonnet | 29907140.2433 | 0 | 0.0 |
| af731e4d5ae3bde91 | sonnet | 29906886.2888 | 0 | 0.0 |
| afa07d87ff22a32f0 | sonnet | 29907095.5219 | 0 | 0.0 |
| totals |  | 1734607404.261 | 8280486 | 4.1521 |
cost source: est

takeaway: ran 1179% slower than projected; cost 255% over; biggest miss T9
