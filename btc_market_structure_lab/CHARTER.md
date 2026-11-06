# Project Charter

Frozen: 2026-11-06

## Mission

Build a BTC-only decision-support system that detects observable market
structure and reports what the market is doing now. It will not claim to know
an exact future price and will not place orders.

## Product output

The first usable product is a concise alert containing:

1. The detected event or state: liquidity sweep, absorption, expansion,
   compression, accumulation-like balance, or distribution-like balance.
2. Directional implication: bullish, bearish, or indeterminate.
3. Evidence from each venue, with disagreement shown rather than hidden.
4. Event price, invalidation condition, observation horizon, and data age.
5. Calibrated historical probability and sample count when sufficient.
6. A clear `WATCH ONLY` status until a separate go-live protocol exists.

The system may explicitly output `NO EDGE` or `INSUFFICIENT DATA`.

## Scope

- One underlying only: BTC.
- Binance BTC perpetual and Bybit BTC perpetual are primary observations.
- Hyperliquid BTC is optional and cannot block the primary product.
- Spot BTC may be used only as a reference price or venue-dislocation check.
- No other coin may be used as a feature, confirmation, benchmark, or way to
  increase event frequency.

## Research principles

- Event time before arbitrary chart timeframes.
- Causal features only: every feature must have existed when an alert fires.
- Venue disagreement is information, not missing confirmation.
- Thresholds are estimated inside training windows only.
- Every hypothesis is written and frozen before outcome inspection.
- Costs, latency, missing data, and false alerts are first-class outcomes.
- Failure is recorded; failed rules are not silently retuned on the same data.

## Success criteria for the research product

Success is not a preselected win rate or trade frequency. Phase 1 succeeds only
if all of the following are true:

1. Data provenance, timestamps, gaps, and venue identity are auditable.
2. The same frozen detector can be replayed without changing its outputs.
3. Its probability estimates are calibrated on unseen chronological data.
4. It adds information versus matched non-event observations, with uncertainty
   intervals reported rather than a single headline number.
5. Results are not carried by one short period, one venue, or a handful of
   extreme observations.
6. Alerts arrive soon enough to remain actionable after measured latency and
   conservative transaction-cost assumptions.

Only after those conditions pass may a separate study translate alerts into
entry, stop, target, sizing, or holding rules.

## Explicit non-goals

- Predicting an exact BTC price.
- Maximizing the number of alerts.
- Achieving a promised profit, win rate, or monthly return.
- Reusing old strategy logic or old acceptance thresholds.
- Automatic trading or real-money deployment.
