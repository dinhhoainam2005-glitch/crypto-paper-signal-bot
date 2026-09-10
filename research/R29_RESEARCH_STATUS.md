# R29 Research Status

Updated: 2026-09-10 UTC

## Decision

Production remains `R26A_QUALITY_CORE_R25A_PULSE_PAPER_OBSERVATION` in paper-only
mode. No R29 historical challenger is approved for deployment.

## Historical Evidence

True Binance depth coverage is available from 2023 onward. The integrity audit
accepted 630,987 of 653,696 aligned rows (96.526%) and quarantined 22,709 rows.
BTC COIN-M depth did not pass cross-market price integrity, so BTC remains a
USD-M-only lane. ETH, SOL and BNB can use quality-gated USD-M/COIN-M consensus.

R29O trained independent LONG and SHORT ridge specialists using only earlier
training and validation windows before each test fold.

| TF | Trades/week | PF 12 bps | PF 20 bps | Sharpe | Max DD | LONG | SHORT | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1h | 8.527 | 1.007 | 0.922 | 0.055 | -20.31% | 779 | 148 | Fail |
| 4h | 2.750 | 1.095 | 1.019 | 0.398 | -12.64% | 243 | 56 | Fail |

R29P froze route cells using only the first two R29O OOS folds and applied the
whitelist unchanged to later folds.

| TF | Trades/week | PF 12 bps | PF 20 bps | Sharpe | Max DD | LONG | SHORT | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1h | 0.530 | 2.407 | 2.180 | 1.709 | -1.82% | 30 | 0 | Fail |
| 4h | 0.654 | 0.609 | 0.570 | -1.125 | -9.64% | 37 | 0 | Fail |

The main result is a real frequency-quality frontier: increasing frequency to
the requested level removes most after-cost edge, while the strongest frozen
1h subset is too sparse and one-sided. Further threshold tuning on these same
folds is prohibited because it would increase selection bias.

## Forward Truth

R29Q now runs as the Windows Scheduled Task `R29Q Forward Truth Recorder` and
writes to:

`D:\@Nam\btc_eth_signal_research\raw\R29Q_forward_truth`

Recorded datasets:

- Binance USD-M L2, open interest, mark/funding context and 1m volume.
- Hyperliquid point-in-time L2 for BTC, ETH, SOL and BNB.
- Exact Binance USD-M forced-liquidation prints from `!forceOrder@arr`.

Hyperliquid L2 is explicitly not labelled as a liquidation feed. Global exact
Hyperliquid liquidation metadata requires node-fill data or paid historical
infrastructure and is not fabricated from public trades.

## Locked Next Gates

- 1h exploratory ablation: at least 30 forward days and 90% snapshot coverage.
- 4h exploratory ablation: at least 90 forward days and 90% snapshot coverage.
- Historical pass cannot authorize live money; an untouched forward challenger
  must also pass costs, latency, direction coverage and drawdown gates.
- Until then, R29Q collects evidence and production stays paper-only.

## R30A Runtime Gate

The paper service now has a code-owned `R30A_FORWARD_TRADE_A_PLUS_GATE`. It
settles paper outcomes at each planned exit open, applies 12 bps and 20 bps cost
models, computes forward PF, Sharpe, win rate, drawdown, direction/timeframe
coverage, concentration, delivery reliability, scan coverage and a deterministic
block-bootstrap probability of positive expectancy.

All signals remain `WATCH` because research promotion is explicitly locked.
Historical R26A/R29 metrics cannot unlock the gate. Promotion requires at least
90 untouched forward days, 150 closed trades, every numeric threshold, and an
explicit code review after R29Q/R29R evidence is complete.
