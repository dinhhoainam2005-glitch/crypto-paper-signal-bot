# Deterministic Order-Book Replay Protocol

Frozen: 2026-11-06

## Purpose

Reconstruct BTC perpetual order books from venue-native snapshots and deltas.
This checkpoint validates data mechanics only; it creates no signal or feature.

## Binance

1. Subscribe to the routed public depth stream before requesting REST depth.
2. Buffer WebSocket messages while requesting the REST snapshot.
3. Discard deltas fully covered by `lastUpdateId`.
4. The first applied delta must bridge the next update ID.
5. Every later delta must have `pu` equal to the prior applied `u`.
6. Any gap invalidates that replay; no forward fill or silent restart.

## Bybit

1. Initialize only from a `snapshot` message.
2. Apply `delta` messages in increasing update-ID order.
3. Quantity zero deletes a level; non-zero quantity replaces it.
4. A new snapshot is an explicit reset.
5. Regressions or gaps invalidate that replay.

## Common invariants

- Best bid must remain strictly below best ask.
- Prices and quantities use exact decimal arithmetic.
- Replaying the same canonical file twice must produce the same counts, final
  best prices, level counts, and SHA-256 state checksum.
- A failing replay blocks all feature and hypothesis work.
