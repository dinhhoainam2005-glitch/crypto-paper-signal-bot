# V2 Risk-Defined Trade Protocol

## Status

Frozen before the first V2 run. V2 is a structural extension of the clean-room
portfolio project, not a parameter search over V1.

The 2024+ sample has already been observed in aggregate during V1. It is
therefore labelled evaluation data, not an untouched holdout for V2. A V2 pass
can permit paper observation only; it cannot justify real-money trading.

## Market And Schedule

- Universe: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT spot.
- Timeframe: 1d.
- Decisions: prior daily close; entries and scheduled exits at Monday open.
- Direction: LONG or cash only.
- No leverage, borrowing, shorting, or automatic orders.

## Entry

- Systemic risk gate: BTC is above its 200-day moving average and has positive
  90-day momentum.
- Asset gate: asset is above its 200-day moving average with positive 90-day
  and 180-day momentum.
- New trade only when no position is already open for that symbol.
- Entry is the next Monday open after the completed Sunday candle.

## Risk And Sizing

- Initial stop distance: 2 times 20-day daily ATR.
- Capital risk budget: 0.50% per trade.
- Maximum initial notional: 30% per symbol.
- Maximum initial portfolio gross exposure: 90%.
- Stress execution cost: 20 bps per one-way traded notional.
- Base execution cost: 10 bps per one-way traded notional.

## Exit Plan

- TP1: 1R; close 25% of the initial quantity.
- TP2: 2R; close another 25%.
- TP3: 3R; close the final 50%.
- After TP1, move the remaining stop to entry.
- After TP2, move the remaining stop to TP1.
- Maximum holding time: 28 calendar days.
- Early risk-off exit: systemic or asset gate is false at a Monday review.
- Same-day ambiguity is pessimistic: after known opening gaps are handled,
  stop-loss is assumed to occur before an intraday take-profit.

## Pre-Registered Gates

All gates must pass before paper observation:

1. At least 80 completed trades in full history.
2. At least 20 completed trades entered during 2024+ evaluation.
3. Full-history stress profit factor at least 1.30.
4. Evaluation stress profit factor at least 1.20.
5. Full-history stress Sharpe at least 1.00.
6. Evaluation stress Sharpe at least 1.00.
7. Full-history maximum drawdown no worse than -20%.
8. Evaluation maximum drawdown no worse than -15%.
9. 2022 portfolio return is nonnegative.
10. At least 70% of active calendar years are profitable.
11. Average realized R multiple is positive.
12. No negative-cash or initial gross-exposure violation.
