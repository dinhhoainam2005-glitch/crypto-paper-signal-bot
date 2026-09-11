# V3 Relative-Strength Walk-Forward Protocol

## Purpose

V3 tests whether cross-sectional selection can preserve the V2 risk control
while removing weak concurrent trades. This is a finite, pre-registered
tournament, not an open-ended parameter search.

## Frozen Candidate Set

1. `ALL`: trade every CORE4 asset that passes the V2 gates.
2. `TOP1`: trade only the eligible asset with the strongest average 90-day and
   180-day momentum.
3. `TOP2`: trade the two strongest eligible assets by the same score.

All candidates use the exact V2 entry, sizing, ATR stop, TP1/TP2/TP3, costs,
risk-off exit, and 28-day maximum hold. There are no other candidate settings.

## Annual Walk-Forward

- Out-of-sample years: 2020 through the available part of 2026.
- At the start of each year, evaluate candidates using only the preceding three
  calendar years.
- Candidate must have at least 20 training trades and PF above 1.0. Otherwise
  `ALL` is used as the fallback.
- Select highest training Sharpe; PF and candidate name are deterministic
  tie-breakers.
- Reset test capital to one at each annual fold and force-close positions at
  the final available daily close of that fold.
- Concatenate only out-of-sample daily returns and trades.

## Evidence Gates

All gates must pass before paper observation:

1. At least 120 out-of-sample trades.
2. OOS profit factor at least 1.30.
3. OOS Sharpe at least 1.00.
4. OOS maximum drawdown no worse than -15%.
5. At least 70% of completed OOS years are profitable.
6. OOS mean realized R is positive.
7. The 5th percentile bootstrap lower bound for mean trade R is positive.
8. Evaluation 2024+ PF at least 1.20.
9. Evaluation 2024+ Sharpe at least 1.00.
10. Recent 2025+ total return is positive.

Passing permits paper observation only. V3 cannot authorize real-money use.
