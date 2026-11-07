# H001 Frozen Evaluation Specification v2

Status: FROZEN BEFORE ANY REAL OUTCOME WAS RELEASED

Frozen: 2026-11-07

This version supersedes v1 because a growing 50/25/25 split would move old test
days back into calibration and permit repeated testing. No real H001 outcome
was released under v1.

## Fixed study calendar

- The first complete UTC day in a contiguous epoch freezes the study start.
- The study has exactly 30 complete UTC days: 15 calibration, 7 validation,
  then 8 sealed-test days.
- `h001_study_plan.json` persists this calendar before outcome release.
- Partial days and days outside the fixed calendar are excluded.
- The sealed evaluation runs once after all 30 days exist. Its result is
  immutable; later data cannot move days between splits or change the result.
- A gap opens a new epoch and therefore a new independently frozen calendar.

## Eligible data

- Input is a contiguous H001 epoch of causal one-second BTC frames.
- A complete UTC day contains exactly 86,400 consecutive frame endings.
- Frames lacking Binance funding are ineligible as events or controls.
- The impulse-through-decision window and every second from `D+1` through
  `D+120` must have fresh Binance and Bybit books, age at most 2,000 ms.
- The full outcome window must remain inside the same frozen split.
- Audit counters, frame counts, normalization, replay gaps, and crossed-book
  checks must all reconcile. Missing audit records make quality fail.

## Outcomes

For research direction multiplier `s` (`UP=+1`, `DOWN=-1`):

- signed return at horizon `h` is
  `s * (mid[D+h] / mid[D] - 1) * 10,000` bps for `h=10,30,120`;
- MFE and MAE are respectively the maximum and minimum signed returns over
  `D+1..D+120`; neither is artificially anchored at zero;
- sweep-extreme revisit is measured symmetrically through 120 seconds.

Retained event decisions must be at least 120 seconds apart. Every outcome is
strictly post-decision and contains no PnL, TP, SL, leverage, or sizing.

## Matching

Each retained event is matched without replacement to one earlier control in
the same split and exact stratum:

1. absolute 10-second impulse decile;
2. trailing 3,600-second realized-volatility decile;
3. UTC hour-of-week;
4. Binance funding sign.

Deciles use nearest rank and are estimated only from the fixed 15-day
calibration split. Controls cannot be detector candidates, cannot lie within
120 seconds of any candidate decision, and cannot overlap any retained event
or selected-control outcome. Tie-breakers are impulse distance, volatility
distance, then most recent prior decision; no future return enters selection.

## Sealed pass gate

The test remains inconclusive with fewer than:

- 100 matched events total;
- 20 matched events in each research direction;
- 20 matched events after excluding either trigger venue.

If eligible, H001 passes only when:

1. mean paired 30-second effect is positive and its 95% moving-block-bootstrap
   lower bound is above zero;
2. 10- and 120-second mean effects are positive;
3. the 30-second mean remains positive after excluding either trigger venue;
4. UP and DOWN 30-second means are each non-negative;
5. all data-quality gates pass.

Bootstrap uses circular blocks of `ceil(sqrt(n))`, 10,000 samples, seed
`20261106`, and percentile bounds at 2.5% and 97.5%. A pass demonstrates only
incremental directional information and never authorizes trading.
