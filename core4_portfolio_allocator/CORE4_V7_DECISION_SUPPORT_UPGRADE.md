# CORE4 V7 Decision-Support Upgrade

## Decision

Keep the frozen V7 trade engine unchanged. The attempted 4h and MTF variants did
not preserve V7 quality, so they are not production candidates. The deployed
upgrade makes the paid service useful between low-frequency 1D entries without
silently changing the validated strategy.

## Evidence review

- Exact execution OOS: 156 trades, 61.5% win, PF 1.883, Sharpe 1.006, and
  -3.15% maximum drawdown.
- Signal frequency: 0.528 trades/week across BTC, ETH, SOL, and BNB.
- Same-time entries form correlated clusters. The historical ledger contains
  121 clusters, including 26 multi-symbol clusters.
- Multi-symbol cluster diagnostic: 73.1% winning clusters and PF 3.87. This is
  historical diagnostic evidence, not an independent forward validation.
- The first live multi-symbol cluster lost across BTC, ETH, and BNB. The old
  `A+` label therefore overstated confidence even though the historical
  diagnostic was strong.
- Splitting one 0.35% risk budget across every multi-symbol cluster reduced the
  approximate full-history PF from 1.88 to 1.60 and recent PF from 2.86 to
  1.80. It was not promoted into the frozen V7 engine.

## Runtime changes

- Replace the user-facing `A+` wording with `XAC NHAN CHEO` and explicitly say
  that same-time signals are correlated, not independent bets.
- Show the total model risk of the complete cluster on every signal.
- Add a projected 1D playbook for all four markets using the exact V7 regime
  and breakout rules.
- Classify distance to the allowed-side trigger as `WAIT`, `WATCH` (<=10%),
  `NEAR` (<=3%), or `TESTING` (intraday through the level).
- Send a watch-only alert when a market moves into a closer readiness band or
  when the projected BTC/SMA200 regime changes. Official trades still require
  the 1D close and the existing ten-minute entry window.
- Report forward performance by entry cluster as well as by trade. Fewer than
  20 closed clusters is explicitly marked as an insufficient sample.
- Expose the same information at `/analysis`; `/performance` includes the
  cluster summary.

## Safety boundary

This upgrade does not change entries, exits, position sizing, symbols, the 1D
timeframe, the frozen specification hash, or paper-only controls. A new trading
rule still requires a separate research version and full audit.
