# V7 Execution-Timing Audit Protocol

This protocol was frozen after V7 passed its independent second audit and
before the official 1h archives were loaded into the execution audit.

## Purpose

Replace daily-bar path ambiguity and daily-close funding approximation with
checksum-verified Binance USD-M 1h bars and the official funding event times.
The V7 signal, portfolio, risk, TP/SL, and 20 bps one-way cost rules remain
unchanged.

## Conservative event order

At each hourly timestamp the engine processes adverse/favorable open gaps,
then any funding event at that timestamp, then the hourly high/low. If both a
stop and target are reachable within one hourly bar, the stop is processed
first. Daily channel exits, maximum-hold exits, and new entries are processed
at 00:00 UTC before the hourly path. Funding at the entry timestamp is charged
or credited to the new position.

## Frozen gates

- At least 100 OOS trades.
- OOS PF >= 1.30, Sharpe >= 1.00, max drawdown no worse than -15%.
- At least 70% of completed OOS years profitable.
- 2025+ PF >= 1.20, Sharpe >= 1.00, and return positive.
- Five-trade moving-block bootstrap lower mean-R bound > 0.
- Bonferroni-adjusted one-sided HAC p-value < 0.05.
- Every signal contract is complete; every fold reconciles; gross cap holds.
- Exact-timing OOS PF and return may not fall below 70% of the conservative
  daily-audit values.

A pass identifies a research-grade candidate for a separately monitored paper
forward phase. It still does not authorize Telegram alerts, Render changes, or
real-money trading.
