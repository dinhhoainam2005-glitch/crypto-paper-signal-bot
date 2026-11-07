# Fresh Start Handoff

Updated: 2026-11-07

## User directive

This is a genuinely new project built from zero. It is not another revision of
CORE4, R26A, H001-H004, or any earlier directional bot.

## Clean-room boundary

The following are archived and prohibited as design inputs unless the user
explicitly re-authorizes them later:

- All previous strategy rules, thresholds, benchmarks, and conclusions.
- The inherited BTC/ETH/SOL/BNB universe.
- The inherited 1h/4h/1d timeframes.
- Frequency, win-rate, PF, Sharpe, expectancy, and drawdown targets.
- ATR stop/target formulas and fixed holding periods.
- Existing CORE4, R26A, tri_venue_copilot, H001, H002, H003, and H004 code,
  datasets, reports, and deployment assumptions.

These artifacts may remain on disk as an archive, but the new project must not
read, import, copy, tune against, or use them as a benchmark.

## New brief explicitly supplied after the reset

- The research universe is exactly one symbol: BTC. ETH, SOL, BNB, and all
  other coins are excluded. The universe may not be expanded to manufacture
  frequency, sample size, or favorable performance.
- Start with market-structure detection rather than exact-price prediction.
- Investigate accumulation/distribution, liquidity sweeps, volatility state,
  order flow, OI, funding, and liquidation behavior.
- Use a modular expert system: market state, risk/volatility, then execution
  trigger only if earlier modules justify it.
- Build a decision-support scanner/copilot before considering automation.
- Binance and Bybit BTC markets are the primary venues; Hyperliquid BTC is
  optional and removable when its data is insufficient.
- No automatic trading or real-money authorization is assumed.

## Mandatory next action

Before writing strategy code or running any backtest:

1. Create a brand-new project directory with no imports from archived projects.
2. Write a new charter defining the product output and success criteria from
   first principles.
3. Inventory only the data needed by that charter.
4. Freeze the first falsifiable research question before inspecting outcomes.

Do not resume old monthly downloads, old benchmarks, or old hypothesis tests.

## Active clean-room implementation

Created on 2026-11-06:

`btc_market_structure_lab/`

Completed inside this directory:

- BTC-only charter and data contract frozen.
- H001 cross-venue sweep-response question frozen before outcome inspection.
- Live REST provenance probe passed 8/8 endpoints.
- Finite Binance/Bybit WebSocket capture passed all required feeds.
- Binance post-April-2026 `/public` and `/market` routing is implemented.
- Canonical normalization produced 3,108 events from 2,523 live raw messages
  with zero rejects.
- Automated tests: 13 passed.

Additional work completed on 2026-11-06:

- Binance snapshot plus buffered delta capture implemented.
- Deterministic Binance/Bybit order-book replay passed with zero gaps and zero
  crossed books.
- Canonical events are globally ordered by causal receipt time.
- BTC one-second microstructure state frames implemented and verified.
- H001 operational detector, control design, chronological splits, and
  falsification criteria frozen in `hypotheses/H001_SPEC_V1.md` before output
  or outcome inspection.
- Frozen H001 detector implemented without outcome labels or PnL.
- Restart-safe finite forward recorder implemented with compact ledgers,
  overlap deduplication, explicit continuity epochs, and JSON heartbeat.
- A gap can never be bridged into the 24-hour causal baseline; it opens a new
  epoch automatically.
- Live single-session pipeline passed: 1,415 raw records, 1,511 canonical
  events, zero rejects, deterministic replay, and 18 causal frames.
- Live two-session overlap passed: 2/2 segments committed, zero rejected,
  `CONTIGUOUS`, 22 unique frames in one epoch.
- Supervised web service implemented with `/health`, `/ready`, and `/status`.
- A Binance snapshot/delta race found in service testing was fixed with causal
  bootstrap buffering; a three-session live stress then passed 3/3 with zero
  replay gaps and zero normalization rejects.
- Bounded raw retention and compact durable H001 ledgers implemented.
- Render Blueprint prepared for Frankfurt, paid `0.5c-512mb`, and a 1 GB disk;
  automatic deployment is off, so no new service or charge exists yet.
- Optional Vietnamese Telegram operational heartbeat implemented. It reports
  only data health, continuity, warm-up progress, and audit counts; it states
  explicitly that it is not a trade signal.
- Retention now deletes only explicitly committed segments and cannot remove a
  downloaded-but-unprocessed backlog.
- Isolated deployment wheel built successfully with every runtime module.
- H001 evaluation v2 is frozen to 30 complete days (15/7/8) with one immutable
  sealed result; no moving split or optional stopping remains.
- Long-run detector/evaluator memory was compacted for Render 512 MB; the exact
  seven-day quantile benchmark peaked at 75.49 MiB.
- Final live overlap smoke passed 2/2 with zero rejects, replay gaps, crossed
  books, or normalization rejects.
- Automated tests: 50 passed.

Next exact action: push branch `codex/btc-market-structure-lab`, then deploy the
separate Render web service with 2 GB disk. Estimated recurring base cost is
USD 7.50/month before tax or excess bandwidth. Collection remains data work,
not strategy validation.
