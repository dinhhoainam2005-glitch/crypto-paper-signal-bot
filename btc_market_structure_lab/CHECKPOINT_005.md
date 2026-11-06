# Checkpoint 005 - Frozen H001 Detector And Forward Recorder

Date: 2026-11-06

## Status

Passed.

## What was implemented

- The detector in `hypotheses/H001_SPEC_V1.md` was implemented without adding
  tunable CLI parameters.
- The detector writes candidate audit records only. It does not compute future
  returns, PnL, win rate, or trading rules.
- A restart-safe forward recorder now runs the complete quality chain:
  capture, normalization, deterministic book replay, causal state frames,
  compact H001 ledger, heartbeat, and detector audit.
- Overlapping capture sessions remove rotation gaps.
- Duplicate frames are ignored by timestamp.
- Any real time gap opens a new epoch instead of contaminating the 24-hour
  causal baseline.

## Verification

- Automated tests: 28 passed.
- Frozen detector against the earlier 17-frame live capture:
  - baseline observations: 7
  - baseline ready: false
  - candidates: 0
  - expected result because the 24-hour warm-up was not available
- Single-session live forward smoke test:
  - required streams ready: yes
  - raw records: 1,415
  - canonical events: 1,511
  - normalization rejects: 0
  - deterministic replay: passed
  - causal frames: 18
  - committed segments: 1/1
- Two-session overlap live test:
  - committed segments: 2/2
  - rejected segments: 0
  - first segment: 13 frames
  - second segment: 10 input frames, 9 newly appended
  - continuity: `CONTIGUOUS`
  - active epoch: 1 with 22 unique consecutive frames

## Safety boundary

- Instrument remains BTC only.
- Venues remain Binance and Bybit.
- Hyperliquid is still excluded pending a separate audit.
- No outcome inspection, threshold search, PnL, backtest, alert recommendation,
  auto-trading, or real-money execution has been added.

## Next exact action

Package the validated recorder as a supervised web service with persistent
storage and a read-only health/status endpoint. Deployment remains separate
from research approval: collecting the 24-hour baseline is not evidence that
H001 works.
