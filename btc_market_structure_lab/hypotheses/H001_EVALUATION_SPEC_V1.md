# H001 Frozen Evaluation Specification v1

Status: SUPERSEDED BEFORE ANY REAL OUTCOME WAS RELEASED

Superseded by `H001_EVALUATION_SPEC_V2.md`, which fixes the study horizon and
sealed-test dates before outcome release. V1 is retained as an audit trail.

Frozen: 2026-11-06

This document resolves operational details left open by `H001_SPEC_V1.md`.
Nothing in this document may be changed after real outcomes are produced.

## Eligible data

- Input is a contiguous H001 epoch of one-second BTC frames.
- A complete UTC day contains exactly 86,400 consecutive frame endings.
- Partial days are excluded from chronological split assignment.
- Detector warm-up and rolling baselines remain causal across complete days in
  the same epoch.
- Frames lacking a Binance funding rate are ineligible as events or controls.
- Data-quality approval additionally requires every contributing segment to
  have zero normalization rejects and deterministic replay with zero book gaps.

## Event outcomes

For an accepted event with decision frame `D` and direction multiplier `s`
(`UP=+1`, `DOWN=-1`):

- signed return at horizon `h` is
  `s * (mid[D+h] / mid[D] - 1) * 10,000` bps for `h=10,30,120`;
- MFE is the maximum signed return over seconds `D+1` through `D+120`;
- MAE is the minimum signed return over the same interval;
- an upward sweep is revisited if any future mid through 120 seconds is at or
  above its sweep extreme;
- a downward sweep is revisited symmetrically at or below its sweep extreme.

No event is labeled unless all 120 post-decision seconds exist in the same
epoch. Outcome windows are purged chronologically: a later event is retained
only when its decision is at least 120 seconds after the previous retained
event decision.

## Matching features

Every event is matched to one earlier control in the same chronological split.
The control is a pseudo-decision frame `C`; its pseudo-candidate is `C-5` and
its pseudo-direction is opposite the sign of the 10-second impulse ending at
`C-5`. This mirrors the event's five-second rejection observation without
requiring a rejection for controls.

Matching strata are exact:

1. absolute 10-second impulse decile;
2. trailing realized-volatility decile, where volatility is the population
   standard deviation of one-second log returns over the 3,600 seconds ending
   at the candidate or pseudo-candidate;
3. UTC hour-of-week of the decision frame, Monday 00:00 UTC = 0;
4. sign of the latest Binance funding rate: `NEGATIVE`, `ZERO`, or `POSITIVE`.

Decile boundaries are estimated from eligible feature observations in the
earliest 50% calibration days only. The same frozen boundaries are applied to
validation and sealed-test days. Quantile boundaries use nearest-rank deciles.

## Control exclusions and deterministic selection

- A control decision must precede its event decision.
- A control cannot be a detector candidate, accepted or rejected, and cannot
  lie within 120 seconds of any detector candidate decision.
- Its complete 120-second future must exist in the same epoch and split.
- It cannot overlap a retained event or another selected control outcome
  window.
- Controls are used without replacement.
- Among exact-stratum controls, select minimum absolute distance in raw
  impulse magnitude, then minimum absolute distance in raw realized
  volatility, then the most recent prior decision. These tie-breakers use no
  future return.
- Events with no eligible exact-stratum control remain recorded as unmatched
  and do not enter effect estimates.

## Chronological split

Sort complete UTC days. With `N` complete days:

- calibration: first `floor(0.50*N)` days;
- validation: next `floor(0.25*N)` days;
- sealed test: all remaining days.

Every split must contain at least one complete day; otherwise the study is
`INCONCLUSIVE_INSUFFICIENT_DAYS`. Results may be computed for calibration and
validation diagnostics, but the sealed-test outcome report is emitted only
once the frozen sample gates are met.

## Paired effects and confidence intervals

For each matched pair and horizon, paired effect is event signed return minus
control signed return. Point estimates are arithmetic means in bps.

The 95% interval uses a moving-block bootstrap over chronologically ordered
paired effects:

- block length: `ceil(sqrt(n))` pairs;
- circular consecutive blocks;
- 10,000 bootstrap samples;
- deterministic seed: `20261106`;
- percentile interval at 2.5% and 97.5%.

## Frozen pass implementation

The sealed test is not eligible for a conclusion with fewer than:

- 100 matched events in total;
- 20 matched events in each research direction;
- 20 matched events remaining after excluding either trigger venue.

If eligible, H001 passes only when:

1. mean paired effect at 30 seconds is positive and its 95% bootstrap lower
   bound is above zero;
2. mean paired effects at 10 and 120 seconds are both positive;
3. the 30-second mean remains positive after excluding Binance triggers and
   after excluding Bybit triggers;
4. direction-specific 30-second means are both non-negative; for v1, any
   negative direction-specific mean is treated as materially negative;
5. all contributing data-quality gates pass.

Any failed condition yields `REJECTED`. Missing sample or data gates yield
`INCONCLUSIVE`, never a pass. A pass indicates directional information only
and still does not define entry, TP, SL, sizing, leverage, or authorization to
trade.
