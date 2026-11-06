# BTC Microstructure State Frame v1

Frozen: 2026-11-06

## Purpose

Create a causal, BTC-only representation of observable market structure. The
frame is descriptive data, not a directional label or trade signal.

## Clock

- One-second receipt-time buckets.
- A frame is emitted only after its bucket has closed.
- Exchange timestamps remain available in canonical events, but causal ordering
  follows local receipt time.
- Empty seconds retain the latest valid book while flow resets to zero and data
  age increases.

## Book fields per venue

- Best bid, best ask, mid, and spread in basis points.
- Bid and ask notional within 5, 10, and 25 bps of mid.
- Depth imbalance within each band: `(bid - ask) / (bid + ask)`.
- Book age at frame close.

## Flow fields per venue

- Aggressive buy and sell notional during the second.
- Net aggressive notional: buy minus sell.
- Trade-message age at frame close.

## Liquidation fields

- Long- and short-liquidation notional during the second, by venue.

## Cross-venue fields

- Consolidated mid: arithmetic mean of the two venue mids.
- Binance-minus-Bybit mid dislocation in basis points.

## Invariants

- Both books must have valid snapshots before any frame is emitted.
- Input receipt timestamps must be monotonic.
- A gap or crossed book aborts the build.
- Decimal market values remain exact decimal strings in output.
- No threshold, label, entry, TP, SL, or PnL is computed here.
