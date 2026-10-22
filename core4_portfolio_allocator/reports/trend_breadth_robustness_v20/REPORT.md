# CORE4 V20 TREND_BREADTH2 ROBUSTNESS

**PAPER_SHADOW_AUTHORIZED**

| Scenario | Trades/wk | Win | PF | Mean R | Return | Sharpe | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BASE_20BPS | 0.737 | 61.1% | 1.956 | 0.362 | 22.71% | 1.131 | -3.58% |
| COST_30BPS | 0.737 | 60.6% | 1.878 | 0.340 | 21.19% | 1.065 | -3.68% |
| COST_40BPS | 0.737 | 58.4% | 1.803 | 0.319 | 19.69% | 0.999 | -3.77% |
| ENTRY_DELAY_1D | 0.784 | 60.4% | 1.779 | 0.300 | 18.68% | 0.912 | -3.47% |
| FUNDING_ADVERSE | 0.737 | 58.4% | 1.831 | 0.327 | 20.28% | 1.024 | -3.67% |
| COMBINED_STRESS | 0.784 | 54.9% | 1.585 | 0.243 | 14.70% | 0.734 | -3.92% |

Robustness gates: 7/7.
Leave-one-coin-out passes: 4/4.
Bootstrap PF lower 5%: 1.417.
Bootstrap mean R lower 5%: 0.180.
Bootstrap worst 95% max DD: -6.01%.

Production remains unchanged.