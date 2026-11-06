# Checkpoint 006 - Supervised Service Package

Date: 2026-11-06

## Status

Passed locally; cloud deployment intentionally not started.

## Reliability work

- Corrected a real Binance snapshot/delta bootstrap race found by service-level
  testing.
- The depth connection now becomes active before the REST snapshot request.
- Depth deltas continue buffering while the snapshot downloads.
- Stale bootstrap deltas are audited and dropped.
- The bridge delta is released only after the snapshot becomes causally usable.
- Buffered receipt-time adjustment is explicit in the raw envelope.
- Shutdown cancels active capture tasks instead of leaving them behind.

## Verification

- Automated tests: 36 passed.
- Three-session live bootstrap stress:
  - committed: 3/3
  - rejected: 0
  - later sessions: `CONTIGUOUS`
  - Binance replay gaps: 0 in every segment
  - normalization rejects: 0
- Local supervised HTTP service:
  - `/health`: `alive`
  - `/ready`: `ready`
  - data state: `OK`
  - last segment: `COMMITTED / CONTIGUOUS`
- Retention test leaves one requested raw segment while preserving the compact
  ledger and full segment audit.

## Research status

H001 quality remains unknown. The service package proves data correctness and
operational continuity only. A complete causal baseline and sealed outcomes
are still required before any conclusion.
