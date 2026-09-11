# CORE4 Risk-Defined V2

**REJECT_V2**

Fixed LONG/cash rules with explicit ATR stop, TP1/TP2/TP3, and 28-day maximum hold.
Stress results include 20 bps cost per one-way traded notional.

| Period | Trades | Win | PF | Mean R | Return | CAGR | Sharpe | Max DD | Median hold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FULL | 272 | 55.5% | 1.424 | 0.178 | 26.21% | 2.72% | 0.619 | -8.98% | 8.0d |
| DEVELOPMENT | 126 | 61.1% | 1.787 | 0.281 | 18.69% | 4.38% | 1.004 | -5.76% | 8.0d |
| BEAR_2022 | 0 | 0.0% | 0.000 | 0.000 | 0.17% | 0.17% | 1.135 | -0.01% | 0.0d |
| VALIDATION_2022_2023 | 52 | 51.9% | 1.457 | 0.179 | 4.87% | 2.41% | 0.616 | -5.36% | 7.5d |
| EVALUATION_2024_PLUS | 94 | 50.0% | 1.073 | 0.040 | 1.39% | 0.52% | 0.128 | -8.67% | 8.0d |
| RECENT_2025_PLUS | 40 | 52.5% | 0.927 | -0.023 | -0.16% | -0.10% | -0.008 | -4.61% | 7.0d |

Gate: 9/12 checks passed.
Equity reconciliation error: 8.882e-16.
Maximum realized holding time: 28.0 days.
Incomplete trade plans: 0.
No paper alerts, deployment, Telegram message, or live order was enabled.
