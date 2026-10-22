# CORE4 V17 Alt Fast Confirmed Paper Shadow

## Decision

Replacement decision: `REJECT_AS_V7_REPLACEMENT`.

Observation decision: `PROSPECTIVE_PAPER_SHADOW_ONLY`.

V17 does not replace or modify frozen CORE4 V7. It runs in a separate ledger
inside the same always-on paper service so prospective results can be measured
without another Render instance.

## Frozen Shadow Rule

- Signal timeframe: 1D.
- Confirmation universe: BTC, ETH, SOL and BNB.
- Tradable symbols: BNB and SOL only.
- Direction: LONG or SHORT, aligned with BTC close versus BTC SMA200.
- Trigger: 20-day Donchian close breakout.
- Confirmation: at least two CORE4 symbols break in the same direction on the
  same completed daily candle.
- Entry: next 00:00 UTC open, subject to the existing 10-minute and 40-bps
  stale/chase gates.
- Stop and targets: 2 ATR20; TP1/TP2/TP3 at 1R/2R/4R with 20%/30%/50% exits.
- Early exit: opposite 10-day Donchian close.
- Maximum hold: 90 days.
- Paper risk: 0.105% per position; 6% symbol notional cap; 18% gross cap.

## Historical Diagnostic

Exact hourly path, event-time funding and 20 bps each-way stress cost:

| Metric | V7 | V17 70/30 diagnostic |
| --- | ---: | ---: |
| Trades/week | 0.521 | 0.815 |
| Win rate | 62.3% | 63.5% |
| Profit factor | 1.941 | 1.968 |
| Sharpe | 1.035 | 1.103 |
| Maximum drawdown | -3.29% | -2.62% |

The candidate did not dominate V7 in every subperiod. The BNB/SOL restriction
was chosen after inspecting V16 symbol results, so these numbers are
post-selection evidence, not an untouched validation. V17 therefore remains
explicitly labelled `SHADOW`; it cannot authorize real-money trading.

## Promotion Gate

No promotion is automatic. Review only after at least 30 closed shadow trades
and six months of forward observation. Minimum gates are PF >= 1.30, positive
mean R, maximum drawdown no worse than -10%, and no operational/data-source
breach. Passing those gates permits a new independent audit, not live trading.
