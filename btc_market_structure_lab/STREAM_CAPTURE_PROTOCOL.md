# Finite Stream Capture Protocol

Frozen: 2026-11-06

## Purpose

Test whether live BTC public streams can be collected with explicit provenance,
timestamps, and basic continuity checks. This is not a signal or backtest.

## Streams

Binance USD-M Futures:

- `btcusdt@aggTrade`
- `btcusdt@depth@100ms`
- `btcusdt@markPrice@1s`
- `btcusdt@forceOrder` (optional-event stream; zero events is valid)

Depth is routed through Binance `/public`; aggregate trades, mark price, and
liquidations are routed through `/market`. A connected socket without expected
messages is treated as unhealthy.

Bybit Linear Perpetual:

- `publicTrade.BTCUSDT`
- `orderbook.50.BTCUSDT`
- `tickers.BTCUSDT`
- `allLiquidation.BTCUSDT` (optional-event stream; zero events is valid)

## Pass conditions

- Both connections complete cleanly for the requested finite duration.
- Each required non-liquidation stream produces at least one message.
- Every raw line records local receipt time and the untouched venue payload.
- No malformed JSON.
- Binance depth `pu` continuity breaks and Bybit update-ID regressions are
  counted and reported, never silently repaired.
- The process exits by itself and leaves no collector running.
