# CORE4 V23 EARLY TREND CONTINUATION

**REJECT_V23_KEEP_V20_UNCHANGED**

Selected: `DONCHIAN_12_C2` from 10 preregistered candidates.
Selection gate: 10/14.
Replacement gate: 2/12.

| Period | Trades/wk | Trades | Win | PF | Mean R | Sharpe | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DEVELOPMENT_2021_2023 | 2.436 | 381 | 42.5% | 0.978 | -0.009 | -0.067 | -3.19% |
| VALIDATION_2024 | 2.180 | 114 | 53.5% | 1.414 | 0.160 | 1.068 | -1.02% |
| FROZEN_2025_PLUS | 3.061 | 279 | 38.0% | 0.625 | -0.184 | -1.518 | -5.63% |
| FULL | 2.581 | 774 | 42.5% | 0.895 | -0.047 | -0.358 | -6.05% |

## Combined stress

30 bps per transaction plus one 4h entry delay: 2.605/week, Win 37.6%, PF 0.770, Mean -0.106R, Sharpe -0.827, Max DD -8.89%.

V20 production and real-money locks were not changed.

## Decision context

| System | Trades/week | Win | PF | Mean R | Sharpe | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Frozen V20 | 0.737 | 61.1% | 1.956 | 0.362 | 1.131 | -3.58% |
| V23 nominee | 2.581 | 42.5% | 0.895 | -0.047 | -0.358 | -6.05% |

The V23 nominee had gross PF 1.184 before transaction costs. Fees consumed
0.0917 equity units and funding consumed another 0.0088 across 774 trades.
Median holding time was only 1.33 days and 302 trades exited on the 4h trend
invalidation rule. This is frequent short-horizon noise, not a replacement for
the slower V20 edge.
