# CORE4 V7 Evidence Report

**PAPER_FORWARD_CANDIDATE - RESEARCH GATES PASSED**

This is the first clean-room candidate to pass the primary audit, the
pre-registered independent stress audit, and the exact 1h execution-timing
audit. It is not authorization for real-money trading.

## Frozen Signal

- Universe: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT USD-M futures.
- Timeframe: 1d; both LONG and SHORT.
- Entry: next 00:00 UTC open after a 55-day Donchian breakout.
- Regime: LONG only above BTC SMA200; SHORT only below BTC SMA200.
- Stop: 2 ATR20. TP1/TP2/TP3: 1R/2R/4R, exiting 20%/30%/50%.
- Stop moves to entry after TP1 and to TP1 after TP2.
- Exit: opposite 20-day channel or 90-day maximum hold.
- Risk: 0.35% equity per trade; 20% symbol cap; 60% portfolio gross cap.
- Cost assumption: 20 bps each entry/exit; exact event-time funding.

## Headline Evidence

| Scope | Trades | Trades/week | Win | PF | Mean R | Return | CAGR | Sharpe | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OOS 2021-Aug 2026 | 156 | 0.528 | 61.54% | 1.883 | 0.330 | 19.40% | 3.18% | 1.006 | -3.15% |
| 2025+ | 37 | 0.426 | 72.97% | 2.857 | 0.514 | 6.80% | 4.03% | 1.375 | -1.79% |

Observed OOS length: 5.66 years. Moving-block bootstrap 5% lower mean-R: 0.150. Bonferroni-adjusted HAC p-value: 0.0081.

## Stress Evidence

- 40 bps each way: PF 1.737, Sharpe 0.902, return 17.04%.
- Entry delayed one extra day: PF 1.637, Sharpe 0.735, return 14.36%.
- Independent second audit: 14/14 gates passed.
- Exact 1h execution audit: 19/19 gates passed.

## By Symbol

| symbol | Trades | Win | PF | Mean R | TP1 | TP2 | TP3 | Median hold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BNBUSDT | 41 | 56.10% | 1.754 | 0.341 | 56.10% | 39.02% | 17.07% | 6.3d |
| BTCUSDT | 38 | 50.00% | 1.155 | 0.081 | 55.26% | 31.58% | 10.53% | 12.1d |
| ETHUSDT | 36 | 66.67% | 3.044 | 0.546 | 75.00% | 44.44% | 16.67% | 10.9d |
| SOLUSDT | 41 | 73.17% | 2.283 | 0.360 | 70.73% | 36.59% | 7.32% | 7.7d |

## By Side

| side | Trades | Win | PF | Mean R | TP1 | TP2 | TP3 | Median hold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LONG | 118 | 61.86% | 1.996 | 0.367 | 66.10% | 38.98% | 15.25% | 6.8d |
| SHORT | 38 | 60.53% | 1.546 | 0.213 | 57.89% | 34.21% | 5.26% | 13.5d |

## By Year

| Year | Trades | Win | PF | Return | Sharpe | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2021 | 42 | 59.52% | 2.123 | 6.16% | 1.669 | -2.08% |
| 2022 | 18 | 44.44% | 1.239 | 0.79% | 0.300 | -3.10% |
| 2023 | 28 | 67.86% | 2.024 | 3.20% | 0.906 | -2.29% |
| 2024 | 31 | 54.84% | 1.249 | 1.24% | 0.422 | -2.89% |
| 2025 | 24 | 66.67% | 1.634 | 1.90% | 0.806 | -1.79% |
| 2026 | 13 | 84.62% | 8.814 | 4.81% | 2.005 | -1.01% |

## Decision

V7 is retained unchanged as a research-grade paper-forward candidate.
The low 0.35% trade risk explains the modest CAGR; leverage or risk was
not increased after seeing results. Production, Telegram, Render, and
real-money execution remain unchanged pending a separate paper-forward
authorization and operational build.
