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

The runtime labels every market-data response. With
`CORE4_REQUIRE_FUTURES_FOR_SIGNALS=true`, a Binance spot fallback may keep the
service observable, but it cannot open or replay a paper trade. The heartbeat
shows the verified Futures and fallback counts explicitly.

## Message policy

- One compact startup message per process start.
- One heartbeat per hour.
- Immediate paper Entry, TP1, TP2, TP3, SL/channel/max-hold, and final-close
  messages.
- Stale and chased candidates are recorded but silent by default.
- Every entry includes Entry, SL, TP1, TP2, TP3, risk, validity timing, review
  interval, and 90-day maximum hold.

## Safety

This service cannot place orders. `CANDIDATE_SPEC.json` explicitly keeps live
trading and real money disabled, and its expected hash is compiled into the
runtime. Do not change the strategy during the paper-forward observation; a new
rule requires a new research version and new audits.
