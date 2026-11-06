# Canonical Raw-Event Schema v1

Frozen: 2026-11-06

This schema normalizes transport differences without creating market features.

## Common envelope

- `schema_version`: integer, currently `1`.
- `venue`: `binance` or `bybit`.
- `instrument`: always `BTCUSDT_PERPETUAL`.
- `event_type`: `trade`, `book`, `mark`, `ticker`, or `liquidation`.
- `exchange_event_time_ms`: venue event-generation timestamp when provided.
- `matching_time_ms`: matching-engine or execution timestamp when provided.
- `receipt_time_ns`: local raw-message receipt timestamp.
- `source_sequence`: venue-native sequence or update identifier.
- `source`: source stream/topic.
- `data`: event-specific fields.

## Numeric policy

Prices, quantities, funding, and open interest remain decimal strings. They are
not converted to binary floating point in the canonical raw layer.

## Direction policy

- `aggressor_side` is `BUY` or `SELL` for trades.
- Liquidations contain `raw_side` and `liquidated_position_side` separately.
- No bullish/bearish interpretation exists in this layer.

## Book policy

Book events retain venue-native bid and ask updates, update kind, and sequence
fields. A separate stateful component must apply snapshots and deltas. The
normalizer never pretends a delta is a complete order book.

## Controls

Subscription acknowledgements and pings are not market events and are omitted.
Any event for a symbol other than BTCUSDT is rejected.

## Global ordering

Canonical output is a stable k-way merge of the venue files by local
`receipt_time_ns`. Per-connection order is preserved when timestamps tie. Lead
and lag research must use this causal receipt order; it may not sort retrospectively
by a later exchange timestamp.
