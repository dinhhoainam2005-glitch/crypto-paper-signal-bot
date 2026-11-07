# Forward Recording Protocol

## Purpose

Collect a causal BTC-only forward dataset without downloading month-by-month
history and without letting connection gaps silently enter H001.

## Pipeline

1. Open Binance depth before requesting its REST snapshot.
2. Collect Binance market streams and Bybit linear streams concurrently.
3. Start the next finite session before the current session ends.
4. Normalize all messages by local receipt time.
5. require deterministic, uncrossed two-venue book replay.
6. Build one-second causal state frames.
7. Keep only fields frozen in `H001_SPEC_V1.md` in the forward ledger.
8. Deduplicate overlap by `frame_end_ns`.
9. Continue an epoch only when the next frame is exactly one second later.
10. Start a new epoch after any gap. Never bridge a baseline across a gap.
11. Freeze the first complete UTC day as the start of one 30-day study.
12. Release one sealed result after day 30, then pause collection.

## Storage policy

- The compact H001 ledger is the durable research input.
- Each segment audit preserves stream counts, normalized event counts, replay
  state checksums, sequence statistics, and frame counts.
- Raw and expanded normalized streams are diagnostic material, not the durable
  dataset. The supervised service keeps the latest two completed segments by
  default and deletes only older generated segment directories.
- Full raw retention requires separately sized object storage and is not
  silently enabled.
- An inactive gapped epoch is compressed before the new epoch grows.

## Heartbeat

`heartbeat.json` reports:

- data state: `OK` or `DEGRADED`
- last segment result
- active epoch and consecutive frame count
- 24-hour baseline progress
- H001 baseline and candidate counts when the detector runs
- fixed-study day progress and sealed-evaluation status
- explicit `contains_outcomes=false` and `contains_pnl=false`

## Commands

Finite smoke run:

```powershell
python -m market_structure.forward_recorder --duration 20 --overlap 3 --segments 1 --detector-every-segments 1
```

Supervised service mode uses `--segments 0`. It must run only under a process
manager with persistent storage and external health monitoring.
