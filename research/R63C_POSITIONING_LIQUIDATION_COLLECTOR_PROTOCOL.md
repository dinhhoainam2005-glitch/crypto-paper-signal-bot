# R63C Positioning And Liquidation Collector

## Objective

Collect forward-only positioning evidence for BTC, ETH, SOL and BNB without an
API key. R63C complements R63A order flow; it does not create trade signals.

## Public sources

- Binance USD-M public REST: mark price, funding and open interest every 30s.
- Binance USD-M public WebSocket: symbol liquidation-order events.
- Bybit linear public WebSocket: derivative ticker and all-liquidation events.
- Hyperliquid public WebSocket: active-asset context with mark, funding and OI.

No account endpoint, wallet, API key or order permission is used.

## Minute contract

For each venue/symbol minute, store mark-price open/close, open-interest
open/close, closing funding rate, long/short liquidation count and notional,
event latency and completeness. A minute is complete only when mark, OI and
funding are all observed. No liquidation event is recorded as zero, but later
research must not infer exchange-wide feed health from zero alone.

Closed complete rows are immutable. Incomplete rows are counted and excluded.
Reconnect, duplicate liquidation and late-event diagnostics remain visible in
the atomic health file.

## Research boundary

R63C only builds evidence for a pre-registered R64 study. It does not alter
CORE4, Render, Telegram or live-trading state. Real money remains locked.
