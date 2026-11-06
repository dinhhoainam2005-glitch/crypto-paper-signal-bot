# Checkpoint 002: Finite WebSocket Capture

Completed: 2026-11-06 (Asia/Saigon)

## Passing run

Evidence:

`artifacts/streams/20261105T230935.045611Z/manifest.json`

- Requested duration: 20 seconds.
- Required streams ready: yes.
- Binance: 767 aggregate-trade messages, 189 depth messages, 19 mark-price
  messages.
- Bybit: 382 public-trade messages, 962 order-book messages, 201 ticker
  messages.
- Malformed messages: 0 on both venues.
- Binance depth continuity breaks: 0.
- Bybit update-ID regressions: 0; duplicate updates: 0.
- No liquidation event occurred in this short passing run; this is valid for an
  optional-event stream.

## Important engineering finding

Binance permanently retired its legacy unrouted USD-M WebSocket URL on
2026-04-23. The legacy socket could still appear connected while delivering
only public depth and silently omitting market streams. The collector now uses
separate `/public/ws` and `/market/ws` routes and treats missing expected
messages as failure.

## Decision

Live primary-venue collection feasibility passes. Define and test a canonical
raw-event schema next. H001 outcome analysis remains locked.
