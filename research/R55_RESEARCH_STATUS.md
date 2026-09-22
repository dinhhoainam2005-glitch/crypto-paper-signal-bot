# R55 Cross-Venue Research Status

Updated: 2026-09-21 UTC

## Objective

Test whether completed Kraken Futures 1h moves lead tradable Binance Futures
moves for BTC, ETH, SOL and BNB. The rule and gate were registered before the
return evaluation. R26A and CORE4 V7 were not changed.

## R55A Data Audit

The official public Kraken Futures chart API supplied continuous 1h trade
candles for all four markets.

| Symbol | Kraken contract | Start | Rows | Coverage | Missing hours |
| --- | --- | --- | ---: | ---: | ---: |
| BTCUSDT | PI_XBTUSD | 2020-02-26 12:00 UTC | 57,564 | 100.0% | 0 |
| ETHUSDT | PI_ETHUSD | 2020-02-26 12:00 UTC | 57,564 | 100.0% | 0 |
| SOLUSDT | PF_SOLUSD | 2022-03-23 11:00 UTC | 39,421 | 100.0% | 0 |
| BNBUSDT | PF_BNBUSD | 2023-11-22 13:00 UTC | 24,803 | 100.0% | 0 |

All timestamps passed the rule that a candle becomes available only at its
hourly close. No missing candle was forward-filled.

## R55B Fixed Lead-Lag Rule

The fixed signal used the Kraken return minus Binance return, standardized by
the previous 168 completed hourly divergences. It entered at the next Binance
open when the absolute z-score reached 2.0 in the direction of the Kraken move,
held six hours, included realized funding and applied 12/20 bps costs.

| Split | Trades | LONG | SHORT | PF12 | PF20 | Win |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Development | 2,229 | 1,117 | 1,112 | 0.878 | 0.789 | 43.7% |
| Validation | 1,015 | 527 | 488 | 0.794 | 0.703 | 43.9% |
| Locked diagnostic | 1,501 | 732 | 769 | 0.794 | 0.681 | 46.5% |

Locked symbol PF20 was 0.774 for BNB, 0.714 for BTC, 0.593 for ETH and 0.713
for SOL. All four failed independently.

## Decision

Decision: `REJECT_FIXED_LEAD_LAG_RULE`.

This result is not a frequency failure. It has thousands of balanced 1h LONG
and SHORT observations, but expectancy is negative after costs in development,
validation and locked history. The reviewed threshold, holding period and
direction must not be inverted or retuned on the same locked interval.

R31A remains 8/15. R53 liquidation microstructure, R54 DVOL routing and R55
cross-venue lead-lag have now all failed their fixed screens. No production
change is justified, and neither service is ready for real-money trading.
