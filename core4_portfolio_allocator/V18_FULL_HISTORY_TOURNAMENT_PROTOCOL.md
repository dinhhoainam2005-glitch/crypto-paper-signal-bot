# CORE4 V18 Full-History Robustness Tournament

## Purpose

Search a finite, predeclared library of daily trend configurations across the
entire common CORE4 USD-M Futures history. The tournament is retrospective
research. It cannot create untouched evidence and cannot authorize live money.

## Data Boundary

- Verified Binance USD-M daily bars, exact H1 execution bars and event-time
  funding through the last complete archived month.
- CORE4 means BTC, ETH, SOL and BNB. SOL Futures starts in September 2020 and
  the BTC SMA200 requires a warm-up, so the common executable history begins
  on 2021-01-01.
- Stress cost is 20 bps on every entry and exit. Same-bar ambiguity remains
  stop-first.

## Frozen Library

All sleeves retain BTC SMA200 direction, next-open entry, 2 ATR20 stop,
1R/2R/4R targets, 20%/30%/50% exits, 90-day maximum hold, 0.35% risk per
trade, 20% symbol cap and 60% gross cap.

The finite channel library is:

- 20/10, 40/15, 55/20, 70/25 and 90/30 entry/exit days;
- either one signal or at least two same-direction CORE4 signals at the same
  decision time;
- all four symbols, plus one explicitly diagnostic BNB/SOL subset for the
  confirmed 20/10 and 40/15 sleeves.

Each challenger is also blended with frozen V7 at challenger weights 10%,
20%, 30% and 40%. Pure challenger sleeves are retained as diagnostics.

## Evaluation

Report the full period and every calendar year from 2021 onward. The primary
objectives are trades/week, win rate, profit factor, mean R, total return,
Sharpe and maximum drawdown. A Pareto frontier is reported rather than hiding
trade-offs behind one arbitrary score.

A replacement is eligible only if it:

1. increases frequency by at least 25%;
2. does not reduce win rate, PF, mean R, return or Sharpe;
3. does not worsen maximum drawdown;
4. has full-history PF at least 1.30 and Sharpe at least 1.00;
5. has positive mean R and maximum drawdown no worse than -10%;
6. is profitable in at least 70% of completed calendar years;
7. has no calendar year with PF below 1.00 or return below -1%.

If no candidate passes every gate, V7 remains the reference and there is no
replacement. Any passing candidate may enter a fresh paper-shadow audit only.
