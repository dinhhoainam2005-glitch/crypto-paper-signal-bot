# R54 Options-Implied Volatility Research Status

Updated: 2026-09-21 UTC

## Objective

Test a genuinely new options-implied volatility state as a causal interaction
for balanced CORE4 1h LONG/SHORT signals. The R31A 15-check gate was unchanged,
and production R26A and CORE4 V7 were not modified.

## R54A Data Audit

The official public Deribit API supplied complete daily BTC and ETH DVOL OHLC
from 2021-03-24 through 2026-09-20.

| Currency | Rows | Coverage | Missing days | Causal availability |
| --- | ---: | ---: | ---: | --- |
| BTC | 2,007 | 100.0% | 0 | Pass |
| ETH | 2,007 | 100.0% | 0 | Pass |

Every daily candle was made available only at the next UTC day. The public DVOL
series measures expected volatility; it contains neither options skew nor a
directional price forecast. The separate public historical-volatility endpoint
returned only 384 recent hourly observations and was not used as full-cycle
evidence.

## R54B Fixed Interaction

The rule was registered before evaluation. It used the previously selected,
undeployed R41A breakout and R41B mean-reversion trades without reselecting a
price candidate:

- breakout only when DVOL was above its trailing 60-day median and rising over
  five days;
- mean reversion only when DVOL was below its trailing 60-day median and falling
  over five days.

| Sleeve | Split | Trades | LONG | SHORT | PF20 | Win |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Breakout | Development | 139 | 76 | 63 | 0.816 | 43.2% |
| Breakout | Validation | 82 | 50 | 32 | 0.610 | 43.9% |
| Breakout | Locked diagnostic | 79 | 31 | 48 | 0.665 | 43.0% |
| Mean reversion | Development | 1,397 | 755 | 642 | 0.768 | 49.5% |
| Mean reversion | Validation | 393 | 199 | 194 | 0.708 | 49.9% |
| Mean reversion | Locked diagnostic | 824 | 407 | 417 | 0.572 | 46.4% |

Passing sleeves: **0/2**. Both rules lost after 20 bps in every split.

## Decision

Decision: `REJECT_FIXED_DVOL_RULES`.

Do not deploy, retune the reviewed DVOL thresholds, or purchase historical
options-chain data on the strength of this result. R31A remains 8/15. The free
DVOL series solved timeframe and direction counts but did not create positive
expectancy, demonstrating why counts cannot be optimized independently of
quality.

The remaining pre-registered independent route is cross-venue lead-lag. It must
begin with a separate source and a causal synchronized-data audit before any
return rule is tested.
