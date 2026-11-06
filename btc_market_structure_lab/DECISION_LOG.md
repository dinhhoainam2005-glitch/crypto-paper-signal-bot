# Decision Log

## 2026-11-06 - Clean-room reset

- Started a new project with no imports from archived work.
- Restricted the universe permanently to BTC unless the user explicitly changes
  the charter.
- Chose market-structure alerts as the first product, not price prediction or
  automatic trading.
- Chose Binance and Bybit as primary venues; Hyperliquid remains optional.
- Froze H001 before collecting or inspecting outcomes.

## Next engineering checkpoint

Run the frozen finite WebSocket capture protocol. Only after it passes may the
project define a normalized raw-event schema; feature and signal code remain
prohibited.

## 2026-11-06 - Primary venue REST feasibility passed

- Verified current official Binance and Bybit public contracts.
- Passed 8/8 live REST endpoint checks and 4/4 offline contract tests.
- Approved a finite, self-terminating WebSocket smoke capture as the next step.

## 2026-11-06 - Finite live stream capture passed

- Required BTC streams arrived from both venues with no malformed messages.
- Corrected Binance routing to the post-April-2026 `/public` and `/market`
  architecture after the liveness audit caught silent missing feeds.
- Approved canonical raw-event normalization; research features remain locked.

## 2026-11-06 - Causal market-state foundation passed

- Added a Binance REST depth snapshot after opening the routed depth stream.
- Passed deterministic two-venue book replay with zero gaps and zero crossed
  books on the live audit capture.
- Added globally receipt-ordered canonical events and descriptive one-second
  BTC microstructure frames.
- Froze the exact H001 operational detector and evaluation protocol before
  generating detector output or inspecting future outcomes.

## 2026-11-06 - H001 detector and forward recorder passed

- Implemented the frozen H001 candidate detector without outcomes or PnL.
- Added compact forward ledgers, overlap deduplication, and explicit continuity
  epochs. Any missing second resets the causal baseline.
- Passed 28 automated tests, a complete one-session live pipeline, and a
  two-session live overlap test with no rejected segments or duplicate frames.
- Approved packaging as a supervised persistent service. This approval covers
  data collection only and is not evidence for H001 or permission to trade.

## 2026-11-06 - Supervised service package passed locally

- A service-level test exposed and then eliminated a Binance depth
  snapshot/delta bootstrap race.
- Passed three consecutive live bootstrap sessions with deterministic replay,
  zero gaps, and one continuous epoch.
- Added liveness, readiness, and status endpoints plus bounded raw retention.
- Prepared a Frankfurt Render Blueprint with a 1 GB persistent disk and
  automatic deployment disabled. No cloud service or recurring charge was
  created.

## 2026-11-06 - Deployment package and notifications passed

- Added optional hourly Vietnamese Telegram operational messages with explicit
  non-trading language and secret-safe error handling.
- Protected captured-but-unprocessed segment backlog from retention cleanup.
- Passed 39 automated tests, staged-diff validation, secret scanning, and an
  isolated wheel build containing every runtime module.
- Deployment remains intentionally pending because it creates a separate paid
  service and disk.
