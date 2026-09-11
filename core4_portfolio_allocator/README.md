# CORE4 V7

Independent clean-room research and paper-forward system for the frozen
`CORE4_V7_BETA_REGIME_DONCHIAN` candidate. The existing R26A bot is not imported,
modified, stopped, or deployed by this project.

## Candidate

- BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT USD-M perpetual futures.
- Daily 55-day Donchian breakout, LONG and SHORT, gated by BTC SMA200.
- Entry at the next 00:00 UTC open.
- SL at 2 ATR20; TP1/TP2/TP3 at 1R/2R/4R.
- Exit fractions 20%/30%/50%; maximum hold 90 days.
- 0.35% equity risk per trade; 20% symbol cap; 60% portfolio gross cap.

The frozen specification is `CANDIDATE_SPEC.json`. The paper service checks its
SHA-256 at startup and refuses to run if it changes.

## Paper-Forward Service

```powershell
$env:CORE4_STATE_PATH = "core4_portfolio_allocator/data/core4_v7_forward_state.json"
python -m core4_portfolio_allocator.paper_forward.web
```

Routes:

- `/health`: process health and immutable strategy hash.
- `/status`: complete forward state.
- `/signals`: latest signals, including suppressed stale/chased candidates.
- `/positions`: current paper positions.
- `/performance`: paper equity and closed trades.
- `/spec`: frozen candidate specification.
- `/scan`: protected manual scan when `CORE4_SCAN_TOKEN` is set.

Telegram is disabled by default. The service uses only the independent
`CORE4_TELEGRAM_*` variables and never reads the old bot's Telegram variables.

## Tests

```powershell
python -m unittest discover -s core4_portfolio_allocator/tests
```

Research evidence is in
`reports/beta_regime_v7_execution_audit/REPORT.md`. Passing historical gates
qualifies V7 for paper-forward observation only and is not a guarantee of live
profit.
