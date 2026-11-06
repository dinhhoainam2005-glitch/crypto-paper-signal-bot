# Verified Public API Contracts

Checked against official documentation on 2026-11-06.

## Binance USD-M Futures

- REST base: `https://fapi.binance.com`
- Public high-frequency route: `wss://fstream.binance.com/public`.
- Regular market route: `wss://fstream.binance.com/market`.
- Intended BTC streams: aggregate trades, depth updates, mark price, and
  liquidation orders.
- Stream symbol is lowercase: `btcusdt`.
- Binance permanently retired the legacy unrouted URL on 2026-04-23. The
  collector therefore uses `/public/ws` for depth and `/market/ws` for aggregate
  trades, mark price, and liquidation events, with explicit subscriptions.

Official references:

- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Live-Subscribing-Unsubscribing-to-streams
- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/market-data/rest-api
- https://www.binance.com/en/support/announcement/detail/ebf9b0aa9eca4ff3804eef6fb09ba32a

## Bybit V5 Linear Perpetual

- REST base: `https://api.bybit.com`
- Public WebSocket: `wss://stream.bybit.com/v5/public/linear`
- Intended BTC topics: `publicTrade.BTCUSDT`, `orderbook.50.BTCUSDT`,
  `tickers.BTCUSDT`, and `allLiquidation.BTCUSDT`.
- Order-book `u` is the update ID; `seq` is cross sequence; `cts` is matching
  engine time. These are stored independently from local receipt time.

Official references:

- https://bybit-exchange.github.io/docs/v5/ws/connect
- https://bybit-exchange.github.io/docs/v5/websocket/public/trade
- https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook
- https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation
- https://bybit-exchange.github.io/docs/v5/market/orderbook
- https://bybit-exchange.github.io/docs/v5/market/tickers

## Scope decision

The first probe uses REST snapshots because it is finite, repeatable, and leaves
no background process. WebSocket continuity collection is the next checkpoint
only after this probe passes.
