# Crypto Paper Signal Bot

Paper-only web service for the R26A quality-core signal router, with R25A market-pulse, R27A liquidity-intel and R28A macro-event watches.

## Status

This repository is research-to-paper only.

- Live trading: disabled
- Exchange order placement: not implemented
- Telegram alerts: paper notifications only
- Render target: Python web service with an internal paper-scan loop

Current paper strategy:

- Strategy: `R26A_QUALITY_CORE_R25A_PULSE_PAPER_OBSERVATION`
- Signal markets: `BTCUSDT 1h/4h`, `ETHUSDT 1h/4h`, `SOLUSDT 4h`, `BNBUSDT 4h`
- Context markets: BTC/ETH/SOL/BNB on 1h and 4h for market breadth checks
- Directions: quality-filtered BTC/ETH/SOL/BNB LONG sleeves plus R15C ETH 1h SHORT taker-flow quality candidates
- Gate: breadth-confirmed momentum/pullback triggers plus strict R15C volume/flow/realized-vol filters
- Risk model: paper signal risk fraction `0.25` per position, max 4 positions per sleeve
- Research status: R26A quality-core expansion selected for paper observation only
- Freshness guard: suppress paper trade alerts when entry is older than 10 minutes or price has already moved more than 40 bps in the signal direction

Reported R26A quality-gate metrics:

| Sample | Trades/week | PF | Sharpe | Win | Max DD % |
| --- | ---: | ---: | ---: | ---: | ---: |
| Frozen | 5.605 | 1.608 | 2.834 | 55.6% | -9.13 |
| Validation | 6.923 | 1.641 | 3.488 | 54.1% | -8.52 |
| Recent | 4.607 | 11.371 | 8.972 | 77.8% | -1.08 |

R25A watches BTC/ETH/SOL/BNB on 15m, 1h and 4h in both directions. These are
closed-candle impulse alerts, labelled `WATCH ONLY`, with no entry, target,
position or profitability claim. They do not change R26A's backtest metrics.
Each alert expires 10 minutes after candle close. A sudden move can already be
partly or fully over before the candle closes; the detector cannot catch every move.

R27A Liquidity Intel watches BTC/ETH/SOL/BNB with public market data:

- Binance USD-M order book depth as a near-price liquidity heatmap proxy
- Binance USD-M open-interest history as a liquidation-pressure proxy
- Binance 15m volume, range and taker-flow imbalance
- Hyperliquid L2 book as a cross-venue liquidity map check

R27A alerts are also labelled `WATCH ONLY`. They are not backtested trade entries,
do not include TP/SL and do not claim to be a vendor-grade liquidation heatmap. A
true Hyperliquid liquidation-event feed or Coinglass/Hyblock-style liquidation
heatmap can be connected later as a provider if an API key/data subscription is
available.

R28A Macro Event Watch runs independently from trade entries. It tracks high-impact
macro events that can move USD liquidity, rates, DXY and crypto beta:

- Federal Reserve FOMC/rate-decision calendar and press-conference windows
- BLS calendar for CPI, PPI, Employment Situation/NFP and JOLTS
- BEA release schedule for GDP and Personal Income & Outlays/PCE
- Census economic-indicator calendar for retail sales and durable goods
- Cleveland Fed inflation nowcasting for CPI/PCE forecast context
- Optional Trading Economics consensus forecast if `TRADING_ECONOMICS_API_KEY` is configured

R28A sends `WATCH ONLY` macro-risk alerts at `T-7D`, `T-24H`, `T-6H`, `T-1H`,
`T-15M` and `LIVE` windows. These alerts do not create entries, exits, TP/SL or
profitability claims. They are meant to reduce surprise around scheduled macro
risk and to add forecast/consensus context when reliable data is available.

The scanner fetches 12 kline feeds, then optional R27A depth/OI/Hyperliquid feeds.
Unused premium diagnostics no longer delay decisions. `/status` includes scan
duration, scan gap, per-feed freshness, pulse state, liquidity state and macro
calendar state. Telegram displays candle open, close and notification times separately. Trade prices are
checked again using a fresh ticker immediately before sending.

Pending deliveries are retried until expiry and marked `SENT` only after Telegram
acknowledges them. Signal IDs and pulse IDs are deduplicated separately in the local
ledger. If local state is lost, the new process skips candles predating its first
scan instead of replaying old alerts. Telegram does not offer an idempotency key:
an accepted send followed by a lost response can still be duplicated on retry.

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
- `/events/latest`: recent market watches with delivery status
- `/liquidity/latest`: recent liquidity-intel watches
- `/macro/latest`: recent macro-risk watches
- `/macro/calendar`: current macro calendar known by the bot
- `/scan`: manually trigger a scan and Telegram notification pipeline, optionally protected by `SCAN_TOKEN`

Render Free web services can spin down after 15 minutes without inbound traffic.
The `.github/workflows/render-keepalive.yml` schedule requests `/scan` every five
minutes so each keepalive also runs the Telegram notification pipeline, but scheduled
jobs can be delayed; this does not guarantee worker-grade continuous operation or
delivery latency. Local state is ephemeral on Render Free and can be lost on
restart/redeploy. `/health` checks HTTP liveness; use `/status` to inspect actual
scan freshness. See https://render.com/docs/free .

Required environment variables:

```text
PAPER_ONLY=true
BINANCE_FAPI_BASE_URL=https://fapi.binance.com
SCAN_INTERVAL_SECONDS=60
MAX_INTERNAL_SCAN_INTERVAL_SECONDS=60
MAX_SIGNAL_ENTRY_LAG_SECONDS=600
MAX_SIGNAL_CHASE_BPS=40
MAX_SIGNALS_RETAINED=500
MAX_MARKET_EVENTS_RETAINED=500
MAX_MARKET_PULSE_LAG_SECONDS=600
MAX_MARKET_PULSE_EVENTS_PER_SCAN=12
LIQUIDITY_INTEL_ENABLED=true
MAX_LIQUIDITY_EVENT_LAG_SECONDS=600
MAX_LIQUIDITY_EVENTS_PER_SCAN=4
LIQUIDITY_ALERT_SCORE_MIN=55
HYPERLIQUID_API_BASE_URL=https://api.hyperliquid.xyz
MACRO_EVENT_WATCH_ENABLED=true
MACRO_CALENDAR_CACHE_SECONDS=3600
MACRO_LOOKAHEAD_DAYS=45
MACRO_LOOKBACK_MINUTES=90
MAX_MACRO_EVENTS_PER_SCAN=6
MACRO_ALERT_RETRY_MINUTES=90
TRADING_ECONOMICS_API_KEY=<optional consensus forecast provider>
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

## Verification

```powershell
python -m unittest -v tests.test_strategy tests.test_market_pulse tests.test_liquidity_intel tests.test_macro_events
python -m research.r25a_market_audit
```

The audit downloads public Binance USD-M Futures candles into `data/r25a_audit`
and writes `report.json`. It compares September 3-4, 2026 (Vietnam calendar days)
against R24A raw eligibility and the production R25A detector. It models a scan
at close +60s and +120s with four watches per scan. It does not reproduce historical
Telegram delivery, fill prices, profitability or all-market-cycle robustness.
