# R54 Options-Implied Volatility Protocol

Updated: 2026-09-21 UTC

## Objective

Test whether a causal options-implied volatility state adds stable information
to a balanced CORE4 1h LONG/SHORT sleeve. The unchanged R31A 15-check gate
remains the only historical promotion gate. R26A and CORE4 V7 remain paper-only
and unchanged.

## Data Boundary

- Source: official public Deribit API.
- Series: BTC DVOL and ETH DVOL daily OHLC.
- First verified daily candle: 2021-03-24.
- A daily DVOL candle is available to a strategy only at the next UTC day.
- BTC uses BTC DVOL. ETH, SOL and BNB use ETH DVOL as the fixed alt-market
  volatility proxy.
- The public series is an implied-volatility index, not options skew and not a
  directional return signal.

The official historical-volatility endpoint currently exposes only a short
recent window. Full point-in-time skew, risk reversal and options-chain history
are therefore outside this free-data test.

## Fixed R54B Interaction

R54B may use only the already selected, undeployed R41A symmetric 1h breakout
and R41B symmetric 1h mean-reversion trades. No R41 candidate is reselected.

At each signal time, compute from fully closed DVOL days:

- trailing 60-day median of DVOL close;
- five-day percentage change in DVOL close.

The two fixed masks are:

1. Breakout continuation: DVOL close is above its trailing 60-day median and
   five-day DVOL change is positive.
2. Mean reversion: DVOL close is below its trailing 60-day median and five-day
   DVOL change is negative.

No threshold or lookback may change after the frozen result is read.

## Evaluation

- Development: 2021-04-01 through 2023-12-31.
- Validation: 2024-01-01 through 2024-12-31.
- Locked diagnostic: 2025-01-01 through 2026-08-01.
- Costs: retain each source trade's 12 bps and 20 bps net returns.
- Minimum evidence per sleeve: 100/30/45 trades across the three splits.
- Minimum PF20 per split: 1.05/1.10/1.10.
- The locked diagnostic must contain at least 15 LONG and 15 SHORT trades.

Passing this screen would authorize portfolio-level research only. It would not
change R31A metadata, deploy production code or authorize real-money trading.

## Stop Conditions

- Stop if daily coverage is incomplete or timestamps cannot be made causal.
- Reject if either pre-registered sleeve fails count, PF20 or direction balance.
- Do not search additional DVOL thresholds on the reviewed locked interval.
- Do not purchase options history unless this free causal screen passes.
