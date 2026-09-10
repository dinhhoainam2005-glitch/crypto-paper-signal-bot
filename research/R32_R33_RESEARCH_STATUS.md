# R32-R33 Research Status

## Production decision

Keep `R26A_QUALITY_CORE_R25A_PULSE_PAPER_OBSERVATION` as the paper incumbent.
Do not enable real-money execution and do not replace the deployed signal core.

R31A remains the best production-parity evidence available:

- Frozen period: 2025-01-01 through 2026-08-01.
- Trades: 498, or 6.042 per week.
- Win rate at 12 bps: 55.2%.
- Profit factor: 1.490 at 12 bps and 1.401 at 20 bps.
- Sharpe: 2.465.
- Risk-fraction-adjusted maximum drawdown: -9.61%.
- Structural gaps: 481 LONG versus 17 SHORT and 481 4h versus 17 1h.
- Historical promotion gate: 8/15 checks passed.

The result is suitable for continued paper observation, not for automatic
real-money trading.

## Challenger results

| Audit | Method | Frozen result at 12 bps | Decision |
| --- | --- | --- | --- |
| R32A | ATR TP/SL execution overlay | 498 trades, 6.042/week, win 56.0%, PF 1.463, Sharpe 2.528, DD -10.89% | Reject; PF and DD worsened |
| R32B | Online trailing-performance expert router | 740 trades, 8.977/week, win 45.4%, PF 0.852, Sharpe -1.192, DD -27.41% | Reject |
| R32C | Static R26A meta-label | 257 trades, 3.118/week, win 49.8%, PF 1.273, Sharpe 1.066, DD -9.73% | Reject |
| R32D | Multi-rule consensus router | 400 trades, 4.853/week, win 45.3%, PF 0.912, Sharpe -0.490, DD -20.11% | Reject |
| R32E | Purged walk-forward meta-label | 276 trades, 3.348/week, win 52.9%, PF 1.425, Sharpe 1.614, DD -9.21% | Reject; positive PF in 5/5 folds but inferior to R26A |
| R33A | Cross-sectional LONG/SHORT pairs | 1h PF 0.789; 4h PF 0.622 | Reject |
| R33B | BTC-to-altcoin lead-lag diffusion | 1h had no stable frozen sample; 4h had 24 trades and PF 0.719 | Reject |

No rejected challenger is authorized for Render deployment.

## What the evidence says

1. Filtering the R26A stream reduces frequency without improving frozen quality.
2. Static and trailing-performance routers overfit the 2023-2024 calibration era.
3. More rule agreement is not equivalent to more independent evidence.
4. The existing rule library remains structurally concentrated in LONG 4h.
5. Market-neutral relative-strength pairs did not survive the frozen period.
6. BTC impulse followed by lagging-alt diffusion did not survive the frozen period.
7. True historical Binance depth starts in 2023, liquidation truth ends in 2024,
   and Hyperliquid point-in-time L2 must not be presented as a historical
   liquidation map. These feeds remain forward confirmation/watch inputs.

## Next valid research boundary

The next challenger must introduce genuinely independent alpha rather than a
new filter over the same 27 rules. It should be developed as a separate paper
sleeve and must satisfy all of the following before production replacement:

- Exact runtime/backtest parity.
- Causal entries at `NEXT_OPEN` with closed-candle features only.
- At least 12 bps base cost and 20 bps stress cost.
- Purged, nested walk-forward selection.
- A final period not used for feature, model, threshold, or route selection.
- Explicit LONG, SHORT, 1h, 4h, regime, symbol, and concentration reports.
- No fabricated historical liquidity, liquidation, or macro vintages.
- No automatic real-money path until both historical and forward gates pass.

Render cost and uptime do not alter these quality gates.
