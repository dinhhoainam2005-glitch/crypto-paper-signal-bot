# Checkpoint 001: Primary Venue REST Probe

Completed: 2026-11-06 (Asia/Saigon)

## Result

- Scope: BTCUSDT only.
- Binance USD-M Futures: 4/4 endpoints passed.
- Bybit Linear Perpetual: 4/4 endpoints passed.
- Total: 8/8 passed.
- Contract tests: 4/4 passed.

Successful evidence:

`artifacts/probes/20261105T230407.735188Z/probe_report.json`

The earlier `20261105T230353.088196Z` run is retained as an environment audit:
all requests were blocked by the local sandbox before reaching either venue.
It is not a market-data failure.

## Snapshot observations

- Binance REST latency: 119-176 ms across the four requests.
- Bybit REST latency: 70-96 ms across the four requests.
- Both top-of-book snapshots were valid with bid below ask.
- Observed local receipt minus exchange time was about 1.2 seconds for the
  timestamped calls. This includes local clock offset and transport delay; it
  must not be mislabeled as pure network latency.

## Decision

Primary-source feasibility passes. Proceed to a finite WebSocket smoke capture
for raw trades, depth, ticker/mark, and liquidation messages. Do not start H001
outcome analysis until stream continuity and timestamp semantics pass.
