# R63A Continuous Multi-Venue Flow Collector

## Objective

Collect contiguous, forward-only one-minute market-flow evidence for BTC, ETH,
SOL and BNB from Binance USD-M Futures, Bybit linear perpetuals and
Hyperliquid. The collector is research infrastructure only.

## Public feeds

- Binance USD-M: individual public trades and best bid/offer. The parser also
  accepts aggregate trades for compatibility; `R63A_BINANCE_TRADE_STREAM` can
  explicitly select `aggTrade` if that feed is verified healthy later.
- Bybit linear: public trades and level-1 order book.
- Hyperliquid: trades and L2 book snapshots.

No API key, account stream, order placement or private user data is used.

The individual `trade` stream is the default because the 2026-10-29 live smoke
test showed that Binance accepted the `aggTrade` subscription but emitted no
events, while `trade` emitted immediately. A silent accepted subscription is
therefore treated as missing data, never as zero flow.

Binance `trade` messages with the exact technical placeholder shape `X=NA`,
`p=0`, `q=0` are excluded and counted under `ignored_events` in the health
status. Other malformed or non-positive trade values remain hard errors.

## Minute contract

Each venue/symbol minute stores:

- aggressive buy/sell notional and trade count;
- quote count and midpoint OHLC;
- mean and closing spread in bps;
- mean and closing top-book imbalance;
- first/last exchange and local timestamps;
- maximum observed capture latency;
- completeness and source stream generation.

Exchange event time assigns an event to a minute. Completed minutes are held
for a grace period before an atomic SQLite transaction. A quote-covered minute
with zero trades is valid zero flow. Events arriving after a cell was flushed
are counted as late and never silently rewritten.

## Storage and safety

- Local SQLite with WAL mode and a unique key on venue/symbol/minute.
- Closed complete minutes are immutable (`INSERT OR IGNORE`). Reconnect replay
  cannot overwrite accepted evidence, and quote-incomplete minutes are counted
  then excluded from the research table.
- Atomic JSON health status.
- Exponential reconnect with bounded backoff.
- Trade-ID de-duplication across reconnects.
- Event-order diagnostics are isolated by venue, symbol and event type; trade
  and quote clocks are never compared with each other.
- Historical snapshots received after reconnect are counted as replay and
  ignored separately from genuinely late events in the active generation.
- No Render, Telegram, CORE4 or live-trading changes.
- No performance statistics are produced by the collector.

The collector only creates evidence. A later protocol must freeze features,
labels and holdout dates before any edge is claimed.
