# CORE4 V19 CAUSAL REGIME ALLOCATOR

**REJECT_V19_RETAIN_V7**

| Portfolio | Trades/wk | Win | PF | Mean R | Return | Sharpe | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V7 baseline | 0.530 | 61.0% | 1.848 | 0.321 | 19.19% | 0.984 | -3.29% |
| V19 walk-forward | 0.640 | 60.9% | 1.821 | 0.320 | 19.32% | 0.990 | -3.58% |
| Fixed TREND_BREADTH2 | 0.737 | 61.1% | 1.956 | 0.362 | 22.71% | 1.131 | -3.58% |

Replacement gates: 8/14.
Best fixed-policy shadow nominee: TREND_BREADTH2 (13/14 gates).
Failed fixed-policy checks: drawdown_not_worse.

Walk-forward selections:
- 2021: DISABLED
- 2022: DISABLED
- 2023: TREND_BREADTH2
- 2024: TREND_BREADTH2
- 2025: LOWVOL_TREND_BREADTH2
- 2026: LOWVOL_TREND_BREADTH2

Production remains unchanged.