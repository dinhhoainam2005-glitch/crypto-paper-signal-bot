# V6 Causal Sleeve Council Protocol

V6 preserves every V5 signal, SL, TP, holding, funding, cost, and sizing rule.
It adds one annual risk-allocation decision using past data only.

For each test year from 2022 onward:

- Run V5 on the preceding three calendar years.
- Evaluate the eight fixed sleeves: four symbols times LONG/SHORT.
- A sleeve is eligible next year only with at least 8 closed training trades,
  training PF at least 1.20, and positive mean realized R.
- No fallback sleeve is enabled when none qualifies.
- Freeze the eligible set for the complete next year.
- Reset fold capital and force-close at the fold's final available close.

No alternative threshold, lookback, ranking, or number of sleeves is tested.
The evidence gates remain: OOS and 2025+ PF, Sharpe, return, drawdown, bootstrap
lower confidence, funding inclusion, reconciliation, and gross-cap compliance.
