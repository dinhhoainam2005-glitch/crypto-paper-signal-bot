# H001 Frozen Operational Specification v1

Status: FROZEN BEFORE DETECTOR OUTPUT OR OUTCOME INSPECTION

Frozen: 2026-11-06

## Mechanism

Test whether an extreme BTC impulse that is rapidly rejected while aggressive
flow exhausts contains directional information in the rejection direction, and
whether cross-venue asymmetry strengthens that information.

This is a liquidity-sweep response study, not a conventional indicator strategy.

## Input

- Causal one-second BTC state frames from Binance and Bybit.
- No other symbol, news, legacy dataset, or prior bot output.
- Frames with either book age above 2,000 ms are ineligible.

## Fixed detector

1. Compute consolidated-mid return over the prior 10 complete seconds.
2. Compare its absolute value with the causal 99.5th percentile of absolute
   10-second returns from the preceding seven days.
3. The baseline requires at least 24 complete hours; before that, no candidate
   may be emitted.
4. An eligible extreme impulse starts a five-second rejection observation.
   The candidate frame must be the highest mid in its 10-second window for an
   upward impulse, or the lowest mid for a downward impulse.
5. Aggregate aggressive flow over the impulse window must be non-zero and have
   the same sign as the price impulse.
6. For an upward impulse, retracement is `(peak - decision_mid) / impulse_size`;
   the downward formula is symmetric.
7. Required retracement: at least 35% by the end of the five-second observation.
8. Aggressive-flow exhaustion passes when the absolute per-second net flow in
   the rejection window is no more than 60% of that in the impulse window, or
   when net flow changes sign.
9. A passing upward sweep implies a `DOWN` research direction; a passing
   downward sweep implies `UP`.
10. Cooldown: 120 seconds after a candidate, whether accepted or rejected, to
   prevent overlapping event families.

Every extreme candidate is recorded, including rejected candidates and its
rejection reason. Suppressed candidates are never silently discarded.

No value above may be tuned inside H001.

## Cross-venue attributes

- Trigger venue: venue with the larger absolute 10-second mid return.
- Lead ratio: trigger absolute return divided by the other venue absolute
  return, with a 0.01-bps numerical floor.
- `ASYMMETRIC` stratum: lead ratio at least 1.25; otherwise `SYNCHRONOUS`.
- Binance-minus-Bybit dislocation, spread, and 10-bps depth imbalance changes
  are recorded as continuous diagnostics and are not gates.

## Decision time

Decision time is the close of the fifth complete second after the extreme
candidate. No data after that time may enter event attributes.

## Frozen outcomes

Starting strictly after decision time:

- Signed consolidated-mid return in the research direction at 10, 30, and 120
  seconds.
- Maximum favorable and adverse excursion through 120 seconds.
- Whether the sweep extreme is revisited within 120 seconds.

The primary endpoint is the 30-second signed return versus matched controls.

## Controls

Each event is matched to prior non-event frames without replacement within the
same chronological split using:

- absolute 10-second impulse decile;
- trailing realized-volatility decile;
- UTC hour-of-week;
- funding-rate sign.

Control selection cannot use future returns. A control receives the same
pseudo-direction rule from the sign of its preceding impulse.

## Chronological evaluation

The calendar details below were superseded before any real outcome was
released. `H001_EVALUATION_SPEC_V2.md` is authoritative and fixes one 30-day
calendar to prevent moving splits and repeated sealed-test inspection.

- Earliest 50% of complete UTC days: baseline and calibration.
- Next 25%: validation.
- Final 25%: sealed test.
- Overlapping 120-second outcome windows are purged.
- No conclusion is allowed with fewer than 100 sealed-test events or fewer than
  20 events in either research direction.

## Pass or rejection

H001 passes only if all conditions hold on the sealed test:

1. Primary 30-second event-minus-control effect is positive and its 95% moving-
   block-bootstrap confidence interval excludes zero.
2. Point estimates at 10 and 120 seconds have the same sign as the primary
   effect.
3. The primary effect remains positive when either trigger venue is excluded.
4. Neither direction has a materially negative primary point estimate.
5. Data-quality gates and deterministic replay remain fully passed.

Otherwise H001 is rejected or remains inconclusive. Passing H001 demonstrates
incremental directional information only; it does not authorize a trade rule.
