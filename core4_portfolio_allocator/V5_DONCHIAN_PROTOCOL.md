# V5 Daily Breakout Protocol

## Hypothesis

V4 entered any qualifying trend state at weekly reviews. Its loss decomposition
shows repeated late entries and stop-outs. V5 tests a distinct event rule: only
enter when price creates a new multi-week extreme, then let the position expire
before another entry can occur.

## Frozen Rules

- CORE4 USD-M perpetuals with realized funding.
- Daily scan; decision uses completed day `t`, execution uses day `t+1` open.
- LONG trigger: close exceeds the highest high of the preceding 55 days.
- SHORT trigger: close falls below the lowest low of the preceding 55 days.
- The current day is excluded from the channel to prevent lookahead.
- Initial SL: 2 ATR20.
- TP1: 1R, TP2: 2R, TP3: 4R; exit 20%/30%/50%.
- After TP1 stop moves to entry; after TP2 stop moves to TP1.
- Early exit when close crosses the opposite 20-day channel.
- Maximum hold: 90 days.
- Risk: 0.35% equity per trade; 20% maximum symbol notional; 60% gross cap.
- Stress cost: 20 bps per one-way notional.
- Same-day ambiguous bars execute the stop before take-profit.

V5 is evaluated with annual fold resets from 2021 onward and the same funding,
reconciliation, recent-regime, drawdown, and bootstrap requirements as V4.
No parameter grid or alternative channel length is allowed in this experiment.
