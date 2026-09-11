# CORE4 Symmetric Futures V4

**REJECT_V4**

Annual reset OOS simulation, explicit LONG/SHORT SL and TP1/TP2/TP3,
28-day maximum hold, realized funding, and 20 bps one-way stress cost.

| Scope | Trades | Win | PF | Mean R | Return | CAGR | Sharpe | Max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OOS full | 394 | 45.9% | 0.904 | -0.042 | -6.17% | -1.12% | -0.225 | -12.56% |
| 2025+ | 116 | 40.5% | 0.657 | -0.170 | -6.82% | -4.16% | -0.924 | -7.34% |

Bootstrap 5% lower mean-R bound: -0.130.
Gate: 5/12 passed.
Funding is aggregated by UTC day and valued at the daily close; this
approximation would require event-time marks in any second audit.
No Telegram, Render, paper alert, or live order was enabled.
