# Crypto Paper Signal Bot

Paper-only web service for the R24A strict-quality signal router.

## Status

This repository is research-to-paper only.

- Live trading: disabled
- Exchange order placement: not implemented
- Telegram alerts: paper notifications only
- Render target: Python web service with an internal paper-scan loop

Current paper strategy:

- Strategy: `R24A_STRICT_QUALITY_R15C_BNB_PAPER_OBSERVATION`
- Signal markets: `BTCUSDT 1h/4h`, `ETHUSDT 1h/4h`, `BNBUSDT 4h`
- Context markets: BTC/ETH/SOL/BNB on 1h and 4h for market breadth checks
- Directions: quality-filtered BNB trend LONG, BTC 1h pullback observation, and R15C BTC/ETH taker-flow quality candidates
- Gate: breadth-confirmed momentum/pullback triggers plus strict R15C volume/flow/realized-vol filters
- Risk model: paper signal risk fraction `0.25` per position, max 4 positions per sleeve
- Research status: R24A quality-first tightening selected for paper observation only
- Freshness guard: suppress paper trade alerts when entry is older than 10 minutes or price has already moved more than 40 bps in the signal direction

## Local Run

```powershell
$env:DISABLE_BACKGROUND_SCAN="1"
$env:PORT="10000"
python -m paper_signal_bot.web
```

Open:

- `http://127.0.0.1:10000/health`
- `http://127.0.0.1:10000/status`
- `http://127.0.0.1:10000/scan`

## Render Web Service

The service is configured through `render.yaml`.

Build command:

```bash
python -m compileall paper_signal_bot
```

Start command:

```bash
python -m paper_signal_bot.web
```

The web service exposes a public URL and still runs the paper scanner in a background thread while the Render instance is awake. The internal scanner is capped at a 1-minute interval even if `SCAN_INTERVAL_SECONDS` is accidentally set higher.

Useful routes:

- `/health`: lightweight uptime endpoint
- `/status`: latest stored bot state
- `/signals/latest`: recent paper signals
- `/scan`: manually trigger a scan and Telegram notification pipeline, optionally protected by `SCAN_TOKEN`

Render Free web services can spin down when idle. This repo includes `.github/workflows/render-keepalive.yml`, which pings `/health` every 5 minutes so the web service can keep running its internal scanner like a worker. You can also ping `/scan?token=<your scan token>` externally if you want the ping itself to trigger each scan.

Required environment variables:

```text
PAPER_ONLY=true
BINANCE_FAPI_BASE_URL=https://fapi.binance.com
SCAN_INTERVAL_SECONDS=60
MAX_INTERNAL_SCAN_INTERVAL_SECONDS=60
MAX_SIGNAL_ENTRY_LAG_SECONDS=600
MAX_SIGNAL_CHASE_BPS=40
MAX_SIGNALS_RETAINED=500
TELEGRAM_ENABLED=true
TELEGRAM_STARTUP_ENABLED=true
TELEGRAM_HEARTBEAT_ENABLED=true
HEARTBEAT_INTERVAL_SECONDS=3600
SCAN_TOKEN=<optional token for /scan>
TELEGRAM_BOT_TOKEN=<your bot token from BotFather>
TELEGRAM_CHAT_ID=<your Telegram chat id>
```

For local worker smoke test:

```powershell
$env:WORKER_RUN_ONCE="1"
python -m paper_signal_bot.worker
```

For local web-service smoke test:

```powershell
$env:DISABLE_BACKGROUND_SCAN="1"
python -m paper_signal_bot.web
```
