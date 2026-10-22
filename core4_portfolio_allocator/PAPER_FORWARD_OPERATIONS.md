# CORE4 V7 Paper-Forward Operations

## Isolation

- Process: `python -m core4_portfolio_allocator.paper_forward.web`
- State: `CORE4_STATE_PATH`; never share the R26A state path.
- Telegram: only `CORE4_TELEGRAM_BOT_TOKEN` and `CORE4_TELEGRAM_CHAT_ID`.
- Exchange access: public Binance USD-M market-data endpoints only. No API key,
  order endpoint, account endpoint, or withdrawal permission exists.

## Required Render variables

| Variable | Value |
| --- | --- |
| `CORE4_STATE_PATH` | `/var/data/core4_v7_forward_state.json` |
| `CORE4_SCAN_INTERVAL_SECONDS` | `60` |
| `CORE4_HEARTBEAT_INTERVAL_SECONDS` | `3600` |
| `CORE4_MAX_ENTRY_LAG_SECONDS` | `600` |
| `CORE4_MAX_CHASE_BPS` | `40` |
| `CORE4_MAX_REPLAY_MINUTES` | `1500` |
| `CORE4_REQUIRE_FUTURES_FOR_SIGNALS` | `true` |
| `CORE4_BINANCE_DATA_MODE` | `stream_archive` |
| `CORE4_FUTURES_STREAM_CACHE_PATH` | `/var/data/core4_v7_futures_stream.json` |
| `CORE4_FUTURES_ARCHIVE_CACHE_DIR` | `/var/data/core4_v7_futures_archive` |
| `CORE4_TELEGRAM_ENABLED` | `true` after explicit activation |
| `CORE4_TELEGRAM_STARTUP_ENABLED` | `true` |
| `CORE4_TELEGRAM_HEARTBEAT_ENABLED` | `true` |
| `CORE4_TELEGRAM_BOT_TOKEN` | secret |
| `CORE4_TELEGRAM_CHAT_ID` | secret |
| `CORE4_SCAN_TOKEN` | a new random secret |

The persistent disk is necessary for signal deduplication, open-position
recovery, and forward statistics across deploys. A service that sleeps through
00:00-00:10 UTC can miss the frozen entry window, so the deployment template is
Starter rather than Free.

The primary data path combines checksum-verified Binance Vision USD-M archives
with the live Binance USD-M Futures WebSocket. Closed one-minute candles and
funding transitions are retained on the persistent disk for position replay.
The runtime labels every market-data response. With
`CORE4_REQUIRE_FUTURES_FOR_SIGNALS=true`, a Binance spot fallback may keep the
service observable, but it cannot open or replay a paper trade. The heartbeat
shows the verified Futures and fallback counts explicitly.

## Message policy

- One compact startup message per process start.
- One heartbeat per hour.
- Immediate paper Entry, TP1, TP2, TP3, SL/channel/max-hold, and final-close
  messages.
- Watch-only readiness alert when a market first moves within 10%, 3%, or
  intraday through the V7 trigger, or when the projected BTC/SMA200 regime
  changes. These alerts never create a position before the 1D close.
- Heartbeats include the four-market 1D playbook, historical reference, and
  forward results grouped by correlated entry cluster.
- Stale and chased candidates are recorded but silent by default.
- Every entry includes Entry, SL, TP1, TP2, TP3, risk, validity timing, review
  interval, and 90-day maximum hold.

Decision support is available as JSON at `/analysis`. `/performance` includes
the same cluster-level forward summary alongside the original trade ledger.
It also exposes the isolated V17 shadow ledger.

## V17 shadow challenger

The service also observes `CORE4_V17_ALT_FAST_CONFIRMED_DAILY` without changing
V7. V17 trades only BNB/SOL after at least two CORE4 markets produce a same-side
20-day daily breakout. Its state, cash, equity, positions and results are
separate under `shadow_v17`; V17 cannot consume V7 capacity or alter V7
statistics. Telegram messages are explicitly marked `V17 SHADOW`, and the
heartbeat reports its forward sample separately. See
`V17_PAPER_SHADOW_PROTOCOL.md` for the frozen rule and evidence limits.

## Safety

This service cannot place orders. `CANDIDATE_SPEC.json` explicitly keeps live
trading and real money disabled, and its expected hash is compiled into the
runtime. Do not change the strategy during the paper-forward observation; a new
rule requires a new research version and new audits.
