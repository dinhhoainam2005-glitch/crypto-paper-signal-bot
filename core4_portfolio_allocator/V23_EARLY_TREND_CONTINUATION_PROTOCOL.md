# CORE4 V23 Early Trend Continuation Protocol

## Objective

Test whether a causal 4h continuation sleeve can improve the slow entry and low
frequency of the frozen V20 daily system without weakening the quality floor.
V20 remains unchanged. V23 is research-only until every historical and forward
gate is passed.

## Frozen research boundary

- Universe: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT USD-M futures only.
- History: common exact Futures history from 2021-01-01 through the last fully
  archived day.
- Context: the V20 `TREND_BREADTH2` daily regime. BTC must be on the correct
  side of its causal SMA200, the SMA200 20-day slope must agree, and at least
  two CORE4 markets must be on the same side of their SMA200.
- Trigger timeframe: 4h. No 15m or 1h signal generation.
- Execution: next 4h open; 1h bars resolve gaps, stops and targets.
- Costs: 20 bps per transaction in the base audit. Funding is charged from the
  archived Binance USD-M funding stream.
- Risk: 0.10% equity per trade, 15% maximum notional per symbol and 40% maximum
  initial portfolio gross.
- Plan: 2 x 4h ATR20 stop, TP1/TP2/TP3 at 1R/2R/3R with 25%/25%/50% exits,
  break-even after TP1, TP1 stop after TP2, and a 10-day maximum hold.
- Early exit: next 4h open after the daily regime invalidates or the completed
  4h close crosses EMA20 against the position.
- Ambiguous 1h bars: stop is evaluated before targets.

## Finite tournament

Ten configurations are registered before results are inspected:

1. Donchian close breakout over 6, 12, or 18 completed 4h bars.
2. EMA reclaim of EMA20 or EMA40 with a candle-range confirmation and aligned
   six-bar EMA slope.
3. Each trigger is tested with either one raw signal or at least two same-side
   CORE4 signals closing in the same 4h cycle.

No parameter is added after observing the results.

## Selection and sealed evaluation

- Development: 2021-01-01 through 2023-12-31.
- Validation: calendar 2024.
- Frozen evaluation: 2025-01-01 onward.
- Candidate selection uses development and validation only. Frozen results are
  opened once for the selected candidate and are not used to retune it.

The development/validation gate requires positive expectancy, controlled
drawdown, meaningful frequency and positive risk-adjusted performance in both
periods. The final replacement gate requires at least 1.0 trade/week, win rate
at least 52%, PF at least 1.50, mean at least +0.15R, Sharpe at least 1.0,
maximum drawdown no worse than -6%, positive frozen evidence, and survival of
30 bps plus a one-bar entry-delay stress.

## Decision rule

Passing history authorizes only a separate paper-forward shadow. It does not
modify V20, authorize auto-trading, or authorize real money.
