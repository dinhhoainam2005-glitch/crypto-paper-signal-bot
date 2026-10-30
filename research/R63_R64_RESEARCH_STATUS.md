# R63-R64 Research Status

## Running infrastructure

- `R63A_6`: continuous Binance/Bybit/Hyperliquid trade-flow and BBO collector.
- `R63C_1`: continuous Binance/Bybit/Hyperliquid OI, funding and liquidation collector.
- `R64` hourly pipeline: audit both stores, build causal 1h/4h rows, then run the frozen mechanism screen.
- All components are paper research only; auto-trade and real money remain disabled.

## Verified state on 2026-10-30

- R63A: 342 common complete minutes from 01:06 through 06:49 UTC, two known
  historical gaps, and a clean current contiguous tail after restart.
- R63C: task logging enabled and collector restarted successfully. The current
  contiguous tail is clean; the earlier task interruption remains a visible
  historical gap and is never filled with synthetic data.
- R64A: three aligned CORE4 minutes at the last manual pipeline run; zero
  causal labels because the frozen 240-minute warm-up and future horizon had
  not completed.
- R64B: zero candidates and no performance claim. Rules were frozen before
  labels became available.

## Earliest automatic evidence

Assuming uninterrupted collection from the 06:48 UTC restart:

- first eligible completed 1h labels: approximately 12:00 UTC / 19:00 VN;
- first eligible completed 4h labels: approximately 15:00 UTC / 22:00 VN;
- the hourly task rebuilds shortly after those windows close.

These are first rows, not validation. Development remains locked until at
least five independent days and 30 cooldown-adjusted candidate clusters exist.

## Safety verdict

No directional edge has been validated yet. CORE4, Render and Telegram trade
signals were not changed. No leverage or real-money trading is authorized.
