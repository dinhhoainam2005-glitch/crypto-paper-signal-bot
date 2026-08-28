# Crypto Paper Signal Bot

Paper-only service for the R22C 4h regime-sleeve trend router.

## Status

This repository is research-to-paper only.

- Live trading: disabled
- Exchange order placement: not implemented
- Telegram alerts: paper notifications only
- Render target: Python background worker

Current paper strategy:

- Strategy: `R22C_REGIME_SLEEVE_4H_PAPER_OBSERVATION`
- Markets: `BTCUSDT 4h`, `ETHUSDT 4h`, `SOLUSDT 4h`, `BNBUSDT 4h`
- Directions: 4h LONG and 4h SHORT regime sleeves
- Gate: breadth-confirmed momentum/breakout/EMA-stack triggers across BTC, ETH, SOL, and BNB
- Risk model: paper signal risk fraction `0.25` per position, max 4 positions per sleeve
- Research status: R22B regime-sleeve pass, approved for paper observation only

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
