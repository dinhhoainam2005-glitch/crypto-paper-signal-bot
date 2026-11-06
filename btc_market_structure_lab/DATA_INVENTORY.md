# Data Inventory and Contract

Frozen: 2026-11-06

Only fields required by the charter and H001 are admitted. Raw records are
append-only; derived records must reference their source and transformation.

## Required primary data

### Binance BTC perpetual

- Public trades or aggregate trades: exchange timestamp, price, quantity, and
  aggressor side where available.
- Order-book best bid/ask and depth updates: exchange timestamp, sequence
  identifiers, price levels, and quantities.
- Mark/index price needed to distinguish venue movement from index movement.
- Open interest and funding observations with exchange timestamps.
- Forced-liquidation events when available from an official public stream.

### Bybit BTC perpetual

The equivalent trade, order-book, mark/index, open-interest, funding, and
liquidation fields, preserving Bybit's native identifiers and timestamps.

## Optional data

Hyperliquid BTC may be added only after a written audit confirms timestamp
semantics, continuity, symbol mapping, and reproducible access. Missing
Hyperliquid data must never stop Binance/Bybit processing.

## Derived data allowed for H001

- Midprice and spread.
- Signed aggressive notional.
- Depth imbalance at explicitly recorded distance bands.
- Price excursion normalized by contemporaneous realized volatility.
- Open-interest change and funding state.
- Cross-venue lead/lag and agreement/disagreement.
- Post-event maximum favorable/adverse excursion for evaluation only.

## Timestamp rules

- Store exchange event time and local receipt time separately in UTC.
- Never substitute receipt time for exchange time.
- Reject or flag sequence gaps; do not silently forward-fill order books.
- Evaluation labels begin strictly after the detector decision timestamp.

## Storage tiers

1. `raw/`: immutable venue-native messages or lossless normalized records.
2. `silver/`: validated, deduplicated, time-aligned BTC records.
3. `features/`: causal features versioned by code and parameters.
4. `studies/`: frozen hypothesis outputs and audit reports.

## Minimum quality report

Each collection or historical import must report coverage, duplicates,
out-of-order records, sequence gaps, timestamp lag, nulls, and source endpoint.
No study starts merely because a file exists.

## Deliberately excluded for now

- Other cryptocurrencies.
- Social-media sentiment, news scoring, on-chain labels, and macro forecasts.
- Any data copied from earlier project directories.
- Paid datasets before free primary-venue feasibility is assessed.
