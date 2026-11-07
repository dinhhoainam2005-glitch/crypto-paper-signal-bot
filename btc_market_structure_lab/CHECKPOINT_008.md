# Checkpoint 008 - Sealed Forward Study Ready

Date: 2026-11-07

## Status

Ready to push and deploy as a BTC research-data service. No edge result exists.

## Frozen study

- BTC only; Binance and Bybit primary venues.
- Fixed 30 complete UTC days: 15 calibration, 7 validation, 8 sealed test.
- One immutable sealed evaluation after day 30; no moving split or repeated
  optional testing.
- Minimum sealed-test gates remain 100 matched events, 20 per direction, and
  20 after excluding either trigger venue.
- Passing proves directional information only, never permission to trade.

## Reliability changes

- Binance snapshot bootstrap retries until a valid depth bridge exists.
- Detector uses bounded sorted blocks instead of unbounded heap tombstones.
- Evaluator uses typed arrays and compact control pools for Render 512 MB.
- Outcome windows must be fresh and stay inside one frozen split.
- Audit counters and frame totals reconcile before data quality can pass.
- Daily detector cadence is restart-safe through persisted commit counters.
- Old gapped epochs are compressed; collection stops after sealed completion.
- Persistent disk increased to 2 GB for the fixed 30-day ledger.

## Verification

- Automated tests: 50 passed.
- Final live overlap smoke: 2/2 committed, 0 rejected, 25 unique frames.
- Replay: zero Binance/Bybit gaps and zero crossed books.
- Normalization rejects: zero.
- Evaluator: `INCONCLUSIVE_INSUFFICIENT_DAYS`; outcomes remained sealed.
- Seven-day rolling-quantile memory benchmark: 75.49 MiB peak.
- Wheel: `btc_market_structure_lab-0.1.0-py3-none-any.whl`
- Wheel SHA-256:
  `90e5a3a621970155909f75c5192df6ab01c03a5a9740f179c89d97544f17d7d3`

## Cost boundary

Render `0.5c-512mb` is USD 7/month and 2 GB disk is USD 0.50/month at
official prices checked 2026-11-07. Estimated base is USD 7.50/month before
tax or excess bandwidth.
