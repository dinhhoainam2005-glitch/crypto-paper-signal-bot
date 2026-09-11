# V4 Symmetric Futures Protocol

## Research Question

Can a symmetric daily trend engine earn positive expectancy in both rising and
falling regimes after perpetual funding and conservative execution costs?

V4 does not alter V2/V3 after seeing their results. It opens a new futures-only
hypothesis with newly downloaded official USD-M futures and funding archives.

## Frozen Design

- CORE4 USD-M perpetuals, 1d decision timeframe.
- LONG when close is above SMA200 and 90d/180d momentum are positive.
- SHORT when close is below SMA200 and 90d/180d momentum are negative.
- No position otherwise.
- Entry at the next Monday open after a completed Sunday candle.
- Initial stop: 2 times ATR20.
- TP1/TP2/TP3: 1R/2R/3R with 25%/25%/50% exits.
- Stop moves to entry after TP1 and TP1 after TP2.
- Maximum hold: 28 days; weekly signal-invalidation exit.
- Risk: 0.35% equity per trade; maximum 20% initial notional per symbol.
- Maximum initial gross exposure: 60%; no notional leverage above account equity.
- Stress execution cost: 20 bps per one-way notional.
- Every realized funding settlement is included with the correct LONG/SHORT sign.

The lower risk and gross caps are fixed before the first run because futures
positions add funding, liquidation, and exchange-counterparty risks absent from
the spot-only experiment.

## Evaluation

V4 must use annual expanding walk-forward reporting from 2021 onward. The first
V4 run may not be deployed regardless of its score. It can only become a
candidate for a second independent audit and paper observation.
