# CORE4 V20 TREND_BREADTH2 Robustness Protocol

## Frozen candidate

V20 tests only the V19 `TREND_BREADTH2` policy. No signal threshold, sleeve
weight, exit rule or universe expansion may be changed after results are seen.

## Exact-path scenarios

1. base: 20 bps execution cost;
2. 30 bps execution cost;
3. 40 bps execution cost;
4. one additional day of entry delay;
5. no funding credits and 1.5x funding debits;
6. combined: 30 bps, one-day delay, no credits and 1.5x debits.

Every scenario uses hourly Binance USD-M Futures execution, stop-first bar
ordering, funding events and the common 2021-01-01 through latest complete
month history.

## Dependency tests

- Remove BTC, ETH, SOL and BNB from the tradable universe one at a time.
- BTC may remain as the causal beta-regime context even when BTC is not traded.
- Report full metrics by symbol and calendar year.

## Uncertainty tests

- Cluster bootstrap trades by entry date, 5,000 samples.
- Moving-block bootstrap daily returns, 2,000 samples with 20-day blocks.
- Report 5% lower bounds for win rate, PF and mean R, plus the 95th percentile
  bootstrapped maximum drawdown.

## Frozen gates

The base candidate must have at least 0.66 trades/week, 58% wins, PF 1.70,
Sharpe 1.00, max drawdown no worse than -5%, and no completed calendar year
below -1% return. Every individual stress scenario must retain positive mean R,
PF at least 1.30 and max drawdown no worse than -7%. The combined stress must
also have positive total return. At least three of four leave-one-coin-out tests
must have PF above 1.20 and positive return.

A pass authorizes a new isolated paper-shadow ledger only. It cannot replace V7
or authorize real-money trading.
