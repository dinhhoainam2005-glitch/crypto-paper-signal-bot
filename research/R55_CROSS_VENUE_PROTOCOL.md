# R55 Cross-Venue Lead-Lag Protocol

Updated: 2026-09-21 UTC

## Objective

Test whether a price move on an independent futures venue leads the next
tradable Binance Futures move for BTC, ETH, SOL and BNB. R55 does not change the
R31A 15-check gate and cannot modify either deployed paper service.

## Data

- Leader venue: official public Kraken Futures 1h trade candles.
- Follower and execution venue: existing verified Binance Futures 1h candles.
- Kraken contracts: `PI_XBTUSD`, `PI_ETHUSD`, `PF_SOLUSD`, `PF_BNBUSD`.
- A Kraken candle is usable only after its hourly close.
- Signals use the intersection of complete candles from both venues. Missing
  candles are never forward-filled.

## Fixed Rule

For each aligned hour:

1. Calculate Kraken and Binance one-hour close returns.
2. Define divergence as Kraken return minus Binance return.
3. Standardize the current divergence using the mean and standard deviation of
   the previous 168 completed hourly divergences.
4. LONG when divergence z-score is at least `+2.0` and Kraken return is
   positive.
5. SHORT when divergence z-score is at most `-2.0` and Kraken return is
   negative.
6. Enter at the next Binance hourly open and exit six hours later.

The lookback, threshold and holding period are fixed before evaluation. No
symbol-specific threshold is permitted.

## Evaluation

- Development: available history through 2023-12-31.
- Validation: calendar year 2024.
- Locked diagnostic: 2025-01-01 through 2026-08-01.
- Costs: 12 bps base and 20 bps stress, plus realized Binance funding cashflow.
- Minimum trades: 100/30/45 across development/validation/locked diagnostic.
- Minimum PF20: 1.05/1.10/1.10 across those splits.
- Locked diagnostic: at least 15 LONG and 15 SHORT trades.
- Every populated symbol with at least 15 locked trades must have PF20 at least
  1.00.

Passing R55 would permit portfolio-level research only. It would not prove
15/15, authorize deployment or justify real-money trading.

## Stop Conditions

- Stop if Kraken history is not sufficiently continuous or causal.
- Reject the fixed rule if any split fails count, PF20 or direction balance.
- Do not retune on the validation or locked diagnostic intervals.
