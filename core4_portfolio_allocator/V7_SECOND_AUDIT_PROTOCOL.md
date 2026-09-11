# V7 Independent Second-Audit Protocol

This protocol was frozen after the primary V7 result and before any audit
variant was executed. V7 remains unchanged and no audit result may be used to
select a replacement parameter.

## Frozen strategy

- CORE4 Binance USD-M futures: BTC, ETH, SOL, and BNB.
- Daily 55-day Donchian entry and 20-day opposite-channel exit.
- LONG only above the completed-day BTC SMA200; SHORT only below it.
- Entry at the next daily open, 2 ATR20 stop, TP at 1R/2R/4R with
  20%/30%/50% exits, and a 90-day maximum hold.
- Risk 0.35% per trade, 20% symbol notional cap, and 60% initial gross cap.
- Annual reset OOS from 2021; funding and pessimistic same-bar ordering remain
  enabled.

## Audit variants

1. Exact frozen replay at 20 bps one-way cost.
2. Transaction costs raised to 30 and 40 bps one way.
3. Entry delayed by one additional completed day.
4. Funding stress: all funding credits removed and funding costs multiplied by
   1.5.
5. Parameter-neighborhood diagnostics only: SMA180, SMA220, entry channel 50,
   and entry channel 60. None can replace the frozen parameters.
6. Four leave-one-symbol-out portfolios.

## Statistical and integrity checks

- Five-trade moving-block bootstrap 5% lower bound of mean R must be positive.
- One-sided HAC mean-R p-value, Bonferroni adjusted for seven research
  experiments, must be below 0.05.
- Every ledger row must contain coherent Entry, SL, TP1, TP2, TP3, and a
  maximum holding time no longer than 90 days.
- Annual equity/trade reconciliation and portfolio gross limits must hold in
  every audit run.

## Frozen audit gates

- The primary replay must reproduce V7 and retain all 12 primary gates.
- 30 bps: PF >= 1.40, Sharpe >= 0.85, recent PF >= 1.20, recent return > 0.
- 40 bps: PF >= 1.25, Sharpe >= 0.65, recent PF >= 1.20, recent return > 0.
- One-day delay: PF >= 1.20, Sharpe >= 0.60, recent return > 0.
- Funding stress: PF >= 1.50, Sharpe >= 0.90, recent PF >= 1.20.
- Every parameter neighbor: PF >= 1.30, Sharpe >= 0.75, recent return > 0.
- Every leave-one-out portfolio: PF >= 1.30, Sharpe >= 0.75, recent return > 0.
- At least 70% of completed OOS years must be profitable and no symbol may
  contribute more than 60% of positive portfolio PnL.

A pass advances the strategy to an execution-timing audit. It does not enable
Telegram, Render, paper alerts, or live trading.
