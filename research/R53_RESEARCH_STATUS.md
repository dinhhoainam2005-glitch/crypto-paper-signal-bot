# R53 Microstructure Research Status

Updated: 2026-09-21 UTC

## Objective

Find genuinely new 1h and SHORT alpha that can close the seven failed R31A
checks without weakening the unchanged 15/15 gate. Production R26A and CORE4 V7
remain paper-only and unchanged.

## R53A Data Pipeline

R53A downloaded real Tardis Binance Futures samples for BTCUSDT and built causal
1h features from executed trades, OI/funding and exact liquidations.

- Sample date: 2021-09-01.
- Coverage: 24/24 hours.
- Causal feature availability: pass.
- Schema and gzip integrity: pass.
- Promotion evidence: none; this stage validates engineering only.

## R53B Free Multi-Cycle Liquidation Panel

R53B collected every available unauthenticated day-01 monthly liquidation file
for BTC, ETH, SOL and BNB from January 2020/contract launch through September
2026.

| Metric | Result |
| --- | ---: |
| Files ready | 313/313 |
| Coverage | 100.0% |
| Liquidation events | 321,659 |
| Liquidation notional | $1.738B |
| Compressed storage | 5.14 MB |

The panel spans bull, bear, recovery, sideways and shock periods, but it is
sparse: only the first UTC day of each month. It can reject a weak hypothesis;
it cannot prove a production edge.

## R53C Fixed Liquidation Rules

Four rules were fixed before evaluation with a 12-hour hold, 12 bps base cost
and 20 bps stress cost. Development was 2020-2023, validation was 2024 and the
locked diagnostic was 2025-July 2026.

| Rule | Development | Validation | Locked | Decision |
| --- | --- | --- | --- | --- |
| LONG liquidation reversal | 1 trade, PF20 0.000 | 1 trade, PF20 0.000 | 0 trades | Reject |
| LONG squeeze continuation | 27 trades, PF20 4.069 | 1 trade, PF20 0.000 | 9 trades, PF20 3.021 | Reject: insufficient and unstable |
| SHORT deleveraging continuation | 32 trades, PF20 0.215 | 1 trade, PF20 0.000 | 7 trades, PF20 2.435 | Reject: development loss |
| SHORT squeeze reversal | 2 trades, PF20 0.048 | 0 trades | 0 trades | Reject |

No threshold was loosened after viewing results.

## R53D Liquidation + OI + Book Confirmation

R53D applied the pre-registered interaction using verified 2023+ Binance OI and
book-depth data. Continuation required falling OI and same-direction top-1 book
depletion. Reversal required falling OI and opposing top-1 absorption.

- Passing hypotheses: 0/4.
- Only one locked SHORT event survived all confirmations.
- No rule met count and PF20 gates in development, validation and locked data.
- Decision: `REJECT_BOOK_CONFIRMED_RULES`.

## Decision

**DO NOT DEPLOY, DO NOT RETUNE, AND DO NOT BUY FULL HISTORY FOR THIS LIQUIDATION
RULE FAMILY.**

The exact-liquidity path was tested seriously and failed its pre-registered
screen. R31A therefore remains 8/15; editing production metadata to 15/15 would
be false. The two deployed services continue only as paper evidence collectors.

## Remaining Route

A legitimate attempt at the remaining seven checks now needs a genuinely
independent return source, not another price/volume/liquidation threshold. The
next defensible candidates are options volatility/skew state or cross-venue
lead-lag execution. Both require new continuous data and a new untouched test;
neither can be claimed from the current warehouse.

No paid subscription is recommended from R53 evidence. Tardis offers a
no-payment trial with only a randomly selected recent 7-14 day range; it can
validate storage and replay engineering but cannot establish a full-cycle edge.
