# Crypto Paper Signal Bot

Paper-only service for the R14H/R14I validated taker-flow breakout research candidate.

## Status

This repository is research-to-paper only.

- Live trading: disabled
- Exchange order placement: not implemented
- Telegram/live alerts: not approved
- Render target: Python web service

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

For local worker smoke test:

```powershell
$env:WORKER_RUN_ONCE="1"
python -m paper_signal_bot.worker
```
