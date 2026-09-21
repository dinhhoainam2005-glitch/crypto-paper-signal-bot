# R53 Unchanged 15-of-15 Research Protocol

Updated: 2026-09-21 UTC

## Objective

Build a new CORE4 portfolio that passes every existing R31A historical quality
check without changing the checks after seeing results. A 15/15 result is a
promotion gate, not a promised outcome. R26A and CORE4 V7 remain paper-only and
unchanged while R53 is researched separately.

## Why R53 Is New

The earlier search reused mostly candle, volume, taker-volume, public Binance
metrics and derived breadth features. That search produced a useful 4h LONG
trend sleeve but repeatedly failed to produce independent 1h and SHORT alpha.
R53 introduces event-level data that were not present in those backtests:

- executed trade flow and causal CVD;
- open-interest changes, funding and mark/index basis;
- forced-liquidation direction and intensity;
- top-of-book/L2 absorption, depletion and depth imbalance;
- cross-market lead-lag among BTC, ETH, SOL and BNB.

Only BTCUSDT, ETHUSDT, SOLUSDT and BNBUSDT may create positions.

## Frozen Quality Gate

The following checks are copied from R31A and may not be weakened:

1. Runtime/backtest parity.
2. At least 150 closed trades.
3. Win rate after 12 bps at least 60%.
4. Profit factor after 12 bps at least 1.50.
5. Profit factor after 20 bps at least 1.20.
6. Sharpe after 12 bps at least 1.20.
7. Risk-adjusted maximum drawdown no worse than -10%.
8. Bootstrap probability of positive expectancy at least 80%.
9. At least 30 LONG trades.
10. At least 30 SHORT trades.
11. At least 30 trades in each populated 1h and 4h timeframe.
12. Maximum symbol profit contribution at most 50%.
13. Every populated symbol/timeframe/side cell has PF20 at least 1.10.
14. Every regime with at least 20 trades has PF20 at least 1.00.
15. At least 70% of populated half-years have positive average net return.

Current R31A evidence remains 8/15. Historical values in production metadata
must not be edited unless a new causal replay and independent report justify it.

## Pre-Registered Sleeves

R53 may test only these economic hypotheses in the first round:

1. `FLOW_BREAKOUT`: price breakout confirmed by taker CVD, rising OI and
   non-extreme funding; symmetric LONG and SHORT rules.
2. `LIQUIDATION_REVERSAL`: forced-liquidation burst into prior support or
   resistance, followed by measurable absorption and reversal confirmation.
3. `DELEVERAGING_CONTINUATION`: price and OI fall together after long
   liquidations, with weak bid replenishment; SHORT only.
4. `SQUEEZE_CONTINUATION`: price rises while OI falls after short liquidations,
   with ask depletion; LONG only.
5. `CROSS_MARKET_LEAD_LAG`: BTC/ETH flow leads SOL/BNB only when the follower's
   book and trade flow confirm in the same direction.

No sleeve may be added after inspecting the locked test unless it starts a new
version with a new untouched test period.

## Evaluation Design

- Development: 2020-01-01 through 2023-12-31.
- Validation: 2024-01-01 through 2024-12-31.
- Locked historical test: 2025-01-01 through the last acquired pre-freeze day.
- True forward: begins only after code, thresholds and hashes are frozen.
- Execution: completed 1h/4h candle, next tradable price, no same-bar fills.
- Costs: 12 bps base and 20 bps stress, plus observed spread/slippage where
  event data permit.
- Purging: overlapping labels and open positions are purged across folds.
- Selection: development only. Validation is one confirmation pass. Locked
  history is opened once; it cannot be used for another retune.

The 2025-2026 market path has been viewed in earlier candle research, so the
locked historical test is not a substitute for true forward evidence. It is a
stronger diagnostic for the newly acquired microstructure features only.

## Staged Decision

### R53A - Data probe

Use Tardis first-day-of-month samples to validate schemas, causality, timestamp
handling and storage size. R53A cannot pass any profitability gate.

### R53B - Full-history warehouse

Acquire continuous event data only after R53A passes. Audit gaps, incidents,
clock ordering and symbol availability before feature generation.

### R53C - Sleeve research

Run the five frozen hypotheses with bounded parameter grids and walk-forward
selection. Reject any sleeve that fails PF20, direction, timeframe or regime
stability on validation.

### R53D - Portfolio replay

Combine only independently accepted sleeves, reproduce runtime signals exactly,
then evaluate the unchanged R31A 15 checks once on the locked historical test.

### R53E - Paper forward

Even a historical 15/15 result only unlocks accelerated paper review. It does
not authorize automatic or large real-money trading.

## Stop Conditions

- Stop if the data source cannot provide continuous, auditable event history.
- Stop a sleeve if PF20 is below one in any populated major regime.
- Stop if quality appears only after symbol, period or threshold post-selection.
- Do not buy a commercial dataset until free samples pass schema, causality and
  storage/compute feasibility checks.
