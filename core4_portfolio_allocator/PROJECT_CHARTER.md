# CORE4 Portfolio Allocator - Clean-Room Charter

## Boundary

This project is independent from the existing signal bot. It does not import
its code, candidates, indicators, thresholds, reports, Telegram formats, or
deployment configuration. The existing bot is frozen and remains unchanged.

Only immutable public market facts may be shared: official exchange OHLC data.

## Objective

Build a slow, risk-first portfolio allocator for BTC, ETH, SOL, BNB, and cash.
It does not predict the next candle and does not target a fixed number of trade
alerts. Once per week it decides how much capital should be held in each asset
and how much should remain in cash.

Success is measured at portfolio level:

- positive return after conservative turnover costs;
- controlled maximum drawdown;
- stable risk-adjusted performance across market cycles;
- no leverage, liquidation, short selling, or automatic order placement;
- robustness in an untouched recent holdout.

## Frozen First Experiment

- Official Binance spot daily bars only.
- Decision at Sunday close; execution at Monday open.
- Asset is eligible when price is above its 200-day moving average and both
  90-day and 180-day momentum are positive.
- Eligible assets receive inverse 60-day volatility weights.
- Maximum 40% allocation to one asset.
- Portfolio volatility target: 15% annualized; gross exposure cannot exceed 1.
- Unallocated capital remains cash.
- Base one-way turnover cost: 10 bps.
- Stress one-way turnover cost: 20 bps.
- No parameter search in the first experiment.

## Pre-Registered Gates

The experiment may proceed to paper observation only when all gates pass:

1. At least two years in the 2024+ holdout.
2. Holdout CAGR at least 8% after stress costs.
3. Holdout Sharpe at least 1.0.
4. Holdout maximum drawdown no worse than -20%.
5. Full-history Sharpe at least 1.0.
6. Full-history maximum drawdown no worse than -25%.
7. Worst full calendar year no worse than -15%.
8. Positive return in the 2022 bear market.

Passing is not permission to trade real money. It only permits a separate
paper-observation stage.

## Mandatory Signal Contract

No actionable signal may be emitted unless it contains all of the following:

- symbol, side, timeframe, and signal timestamp;
- executable entry price or entry zone and an expiry time;
- one stop-loss price;
- TP1, TP2, and TP3 prices;
- reward-to-risk ratio for every take-profit level;
- maximum holding time and the next scheduled review time;
- early-exit and cancellation conditions;
- explicit paper/live state and capital-at-risk limit.

The stop, targets, and holding horizon must be part of the historical and
walk-forward simulation. They cannot be added later only for message display.
Any incomplete or internally inconsistent plan is blocked from Telegram.
