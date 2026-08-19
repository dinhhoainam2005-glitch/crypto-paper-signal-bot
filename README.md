# Crypto Paper Signal Bot

Paper-only service for the R15C taker-flow composite volatility-filter research candidate.

## Status

This repository is research-to-paper only.

- Live trading: disabled
- Exchange order placement: not implemented
- Telegram alerts: paper notifications only
- Render target: Python background worker

Current paper strategy:

- Strategy: `R15C_TAKER_FLOW_COMPOSITE_VOL_FILTER_PAPER_OBSERVATION`
- Markets: `BTCUSDT 4h`, `ETHUSDT 1h`, `ETHUSDT 4h`
- Directions: BTC 4h LONG, ETH 4h LONG, ETH 1h SHORT
- Gate: taker-flow breakout with `quote_volume_prior_z_20 >= 1.659151276879225` and `realized_vol_24 >= 0.005616411766518594`
- Research status: near-pass stress candidate, approved for paper observation only

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

## Render Background Worker

The service is configured through `render.yaml`.

Build command:

```bash
python -m compileall paper_signal_bot
```

Start command:

```bash
python -m paper_signal_bot.worker
```

Background workers do not expose a public URL. Check Render Logs for `worker_started`, `paper_scan`, and `paper_signal` events.

Required environment variables:

```text
PAPER_ONLY=true
BINANCE_FAPI_BASE_URL=https://fapi.binance.com
SCAN_INTERVAL_SECONDS=300
MAX_SIGNALS_RETAINED=500
TELEGRAM_ENABLED=true
TELEGRAM_STARTUP_ENABLED=true
TELEGRAM_HEARTBEAT_ENABLED=true
HEARTBEAT_INTERVAL_SECONDS=3600
TELEGRAM_BOT_TOKEN=<your bot token from BotFather>
TELEGRAM_CHAT_ID=<your Telegram chat id>
```

For local worker smoke test:

```powershell
$env:WORKER_RUN_ONCE="1"
python -m paper_signal_bot.worker
```
