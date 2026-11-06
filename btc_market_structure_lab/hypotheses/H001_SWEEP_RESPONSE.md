# H001: Cross-Venue Sweep Response

Status: FROZEN BEFORE OUTCOME INSPECTION

Frozen: 2026-11-06

## Research question

When BTC makes an unusually large one-sided price excursion on one venue,
does rapid rejection accompanied by aggressive-flow exhaustion and a weaker
excursion on the other venue contain information about the subsequent BTC
price path relative to matched non-events?

## Why this question

It tests an observable market mechanism: a local liquidity sweep followed by
failure to continue. It does not assume that every large move reverses and it
does not require a manually drawn support or resistance level.

## Event construction

All thresholds are rolling empirical quantiles estimated only from observations
available before the candidate event. No threshold may be tuned using H001
outcomes.

A candidate requires, in order:

1. A short event-time price excursion in the extreme tail of its own recent
   volatility-normalized distribution on a trigger venue.
2. Aggressive trade flow in the excursion direction during the impulse.
3. A measurable rejection from the excursion extreme before the decision time.
4. Aggressive-flow decay or reversal during rejection.
5. The other primary venue shows either a smaller normalized excursion or a
   delayed move. Venue agreement is recorded as a separate stratum, not mixed.

The detector emits `UP-SWEEP`, `DOWN-SWEEP`, or no event. It does not emit a
trade.

## Frozen outcomes

Measure the signed path after decision time over several volatility-clock
horizons fixed before running the study:

- return in the rejection direction;
- maximum favorable and adverse excursion;
- time until the sweep extreme is revisited;
- continuation versus rejection probability.

Results must be shown by direction, trigger venue, volatility regime, and
venue-agreement stratum. These are diagnostics, not separate optimized rules.

## Control design

For each event, select prior non-event observations matched on volatility,
time-of-week, spread, and absolute impulse size. Controls must be selected
without using future returns.

## Falsification

H001 is rejected if unseen chronological evaluation shows no stable incremental
information over matched controls, if calibration collapses across periods, or
if the apparent effect depends on data gaps, one venue, or a few outliers.

## Anti-overfitting rules

- Chronological train/calibration/test boundaries only.
- Purge overlapping event and outcome windows.
- Lock detector parameters before the final test segment.
- Report all candidate events, including suppressed or data-invalid events.
- No conversion to PnL until the event study is complete.
