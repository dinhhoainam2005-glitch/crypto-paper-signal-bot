# R64 Positioning-Flow Directional Study

## Mandate

Test whether contiguous multi-venue flow plus positioning can forecast
`LONG / SHORT / NO_TRADE` for BTC, ETH, SOL and BNB at 1h and 4h. R64 is a
research protocol, not a signal service.

## Frozen inputs

- R63A complete one-minute trade-flow and best-book rows.
- R63C complete one-minute mark, open-interest and funding rows.
- Funding is normalized to an hourly-equivalent rate before cross-venue
  comparison (Binance/Bybit 8h; Hyperliquid 1h for the frozen CORE4 study).
- Liquidation observations are additive event evidence; a zero count alone is
  never interpreted as proof that no exchange-wide liquidation occurred.
- Features at cutoff use only rows whose exchange minute has already closed.
- Entry is the next Binance Futures minute open proxy; round-trip costs and
  slippage are deducted before scoring.

## Mechanism families

1. `HEALTHY_TREND`: aligned return and taker flow across venues, OI expanding,
   funding not extreme, spread controlled. Forecast continuation.
2. `DELEVERAGING_CONTINUATION`: directional return and flow with OI contraction,
   matching-side liquidation pressure and thin opposing liquidity. Forecast
   short-horizon continuation only when at least two venues confirm.
3. `CROWDED_BREAKOUT_FAILURE`: extreme funding/OI crowding, price extension,
   weakening cross-venue flow and book absorption. Forecast reversal.

No additional mechanism may be introduced after validation is opened. Failed
families stay failed; thresholds cannot be rescued with holdout information.

## Evaluation

- Horizons: 1h and 4h, evaluated separately.
- Purged, day-blocked walk-forward splits with horizon-length embargo.
- A signal cluster is one coin/direction/time episode; correlated CORE4 coins
  are not counted as independent evidence.
- Report per coin and per independent cluster: trades/week, WR, PF, mean R,
  Sharpe, max drawdown, long/short balance, fees and slippage sensitivity.
- Development gate: at least 30 independent clusters, both directions, PF
  >= 1.20, mean R > 0 and stress PF >= 1.05.
- Validation gate: at least 15 clusters, PF >= 1.10, mean R > 0 and no single
  coin contributing more than 50% of profit.
- Forward gate remains separate and cannot be replaced by backtest results.

Until every gate passes, Telegram trade signals, Render deployment, leverage
and real money remain locked.
