# BTC Market Structure Lab

Clean-room research project for BTC only.

This project does not import, read, benchmark against, or tune from any prior
bot, strategy, dataset, report, threshold, or result in this repository.

## Current phase

Phase 0 is frozen:

- Product charter: `CHARTER.md`
- Data contract: `DATA_INVENTORY.md`
- First falsifiable question: `hypotheses/H001_SWEEP_RESPONSE.md`
- Decision log: `DECISION_LOG.md`
- Current completed checkpoint: `CHECKPOINT_008.md`
- Forward protocol: `FORWARD_RECORDING_PROTOCOL.md`
- Prepared deployment: `DEPLOYMENT.md`

The service is deployment-ready for a fixed sealed-forward study. No historical
performance claim, trade strategy, or real-money execution exists here.

## Run the finite data probe

From this directory:

```powershell
python -m market_structure.probe
python -m unittest discover -s tests -v
```

The probe exits after eight public REST requests and writes immutable raw
responses plus `probe_report.json` under `artifacts/probes/`.

After installing the project dependency, run a finite stream smoke capture:

```powershell
python -m market_structure.collector --duration 60
```

It exits automatically and writes one raw JSONL file per venue plus a manifest.

Normalize a passing capture without creating research features:

```powershell
python -m market_structure.normalize artifacts/streams/<run-id>
```

Then audit deterministic order-book replay:

```powershell
python -m market_structure.book_replay artifacts/streams/<run-id>/normalized.jsonl
```

Build descriptive BTC microstructure frames only after replay passes:

```powershell
python -m market_structure.state_frames artifacts/streams/<run-id>/normalized.jsonl
```

Run the frozen H001 detector without computing outcomes:

```powershell
python -m market_structure.h001_detector artifacts/streams/<run-id>/state_frames.jsonl
```

The authoritative frozen evaluator is
`hypotheses/H001_EVALUATION_SPEC_V2.md`: 30 complete days split 15/7/8, with
one immutable sealed evaluation after collection completes.

Run the validated finite forward pipeline:

```powershell
python -m market_structure.forward_recorder --duration 20 --overlap 3 --segments 1 --detector-every-segments 1
```

This writes a compact, restart-safe H001 ledger and `heartbeat.json`. A data
gap starts a new epoch; it is never hidden or bridged.

Run the supervised HTTP service locally:

```powershell
python -m market_structure.service
```

The service exposes `/health`, `/ready`, and `/status`. Cloud deployment is
prepared with a 2 GB disk and automatic deploys disabled.

## Hard boundary

- Instrument: BTC only.
- Primary venues: Binance and Bybit.
- Optional venue: Hyperliquid, admitted only after an independent data audit.
- Output: decision-support alerts, not automatic orders.
- Old project artifacts are archive material and are not research inputs.
