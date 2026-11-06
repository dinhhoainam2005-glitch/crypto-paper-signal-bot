# Checkpoint 003: Canonical Event Normalization

Completed: 2026-11-06 (Asia/Saigon)

Source capture:

`artifacts/streams/20261105T230935.045611Z`

## Result

- Input raw messages: 2,523.
- Output canonical events: 3,108.
- Trades: 1,737.
- Book events: 1,151.
- Mark-price events: 19.
- Ticker events: 201.
- Rejected raw messages: 0.
- Automated tests after normalization work: 13/13 passed.

The output count exceeds the input count because one Bybit trade message may
contain multiple individual executions; each execution becomes its own event.

## Preserved invariants

- BTCUSDT only.
- Venue event time, matching time, and local receipt time remain separate.
- Price and quantity values remain decimal strings.
- Order-book deltas are not mislabeled as complete books.
- Control messages are omitted rather than treated as market events.

## Next checkpoint

Capture a Binance REST depth snapshot alongside buffered depth deltas, then
implement deterministic Binance and Bybit order-book reconstruction with gap,
crossed-book, and replay-consistency audits. H001 remains locked until this
passes.
