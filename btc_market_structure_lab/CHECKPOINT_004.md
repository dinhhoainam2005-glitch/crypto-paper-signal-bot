# Checkpoint 004: Deterministic Books and State Frames

Completed: 2026-11-06 (Asia/Saigon)

Evidence directory:

`artifacts/streams/20261105T231651.920540Z`

## Live capture

- Raw messages: 2,641.
- Canonical events: 3,633 with zero rejects.
- Trades: 2,384.
- Book events: 1,032.
- Mark/ticker events: 212.
- Liquidations: 5.
- Canonical ordering: stable causal merge by local receipt timestamp.

## Order-book replay

- Deterministic replay: passed.
- Binance: 1 snapshot, 176 applied deltas, 13 stale deltas discarded, 0
  gaps, 0 crossed books.
- Bybit: 1 snapshot, 841 applied deltas, 0 gaps, 0 regressions, 0 crossed
  books.
- Replaying the same file twice produced identical SHA-256 state checksums.

## State frames

- Complete one-second frames: 17.
- Depth bands: 5, 10, and 25 bps.
- Frames with liquidation activity: 5.
- Labels: none.
- PnL fields: none.
- Automated tests: 21 passed.

## Decision

The causal data foundation is sufficient to implement the already frozen H001
detector. No claim about directional edge or profitability has been made.
