"""Finite, restart-safe forward recorder for the frozen BTC H001 study."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import shutil
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .book_replay import ReplayError, audit_replay
from .collector import capture
from .h001_detector import IMPULSE_SECONDS, MIN_BASELINE_SECONDS, run_file
from .h001_evaluator import run_data_root as run_h001_evaluation
from .normalize import normalize_capture
from .state_frames import build_file

ONE_SECOND_NS = 1_000_000_000
LEDGER_FILE = "h001_frames.jsonl"
BASELINE_REQUIRED_FRAMES = MIN_BASELINE_SECONDS + IMPULSE_SECONDS


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _append_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


def project_h001_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Keep only causal fields frozen in H001_SPEC_V1."""
    venues: dict[str, Any] = {}
    for venue in ("binance", "bybit"):
        source = frame["venues"][venue]
        venues[venue] = {
            "mid": source["mid"],
            "book_age_ms": source["book_age_ms"],
            "net_aggressive_notional": source["net_aggressive_notional"],
            "imbalance_10bps": source["imbalance_10bps"],
        }
    return {
        "schema_version": 1,
        "instrument": "BTCUSDT_PERPETUAL",
        "frame_end_ns": int(frame["frame_end_ns"]),
        "consolidated_mid": frame["consolidated_mid"],
        "binance_minus_bybit_bps": frame["binance_minus_bybit_bps"],
        "binance_funding_rate": frame["venues"]["binance"]
        .get("market_state", {})
        .get("funding_rate"),
        "venues": venues,
    }


def _load_projected_frames(path: Path) -> list[dict[str, Any]]:
    frames = [
        project_h001_frame(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for previous, current in zip(frames, frames[1:]):
        if int(current["frame_end_ns"]) != int(previous["frame_end_ns"]) + ONE_SECOND_NS:
            raise ValueError("segment state frames are not consecutive one-second buckets")
    return frames


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "active_epoch": 0,
        "active_epoch_file": None,
        "last_frame_end_ns": None,
        "epoch_frames": 0,
        "active_epoch_segments_committed": 0,
        "total_unique_frames": 0,
        "segments_committed": 0,
        "segments_rejected": 0,
        "last_detector_epoch_segments": 0,
        "updated_at_utc": None,
    }


def _load_state(data_root: Path) -> dict[str, Any]:
    path = data_root / "forward_state.json"
    return _read_json(path) if path.exists() else _default_state()


def _ledger_tail(path: Path) -> tuple[int | None, int]:
    if not path.exists():
        return None, 0
    last: int | None = None
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            last = int(json.loads(line)["frame_end_ns"])
            count += 1
    return last, count


def study_is_sealed(data_root: Path) -> bool:
    state = _load_state(data_root)
    relative = state.get("active_epoch_file")
    if not relative:
        return False
    ledger = data_root / str(relative)
    return ledger.with_name("h001_sealed_result.json").exists()


def _new_epoch(data_root: Path, number: int) -> Path:
    epoch_dir = data_root / "epochs" / f"epoch_{number:06d}"
    epoch_dir.mkdir(parents=True, exist_ok=False)
    return epoch_dir / LEDGER_FILE


def _archive_inactive_ledger(ledger: Path, data_root: Path) -> dict[str, Any]:
    epochs_root = (data_root / "epochs").resolve()
    resolved = ledger.resolve()
    if resolved.parent.parent != epochs_root or not resolved.exists():
        return {"status": "SKIPPED", "ledger": str(ledger)}
    output = resolved.with_suffix(resolved.suffix + ".gz")
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with resolved.open("rb") as source, gzip.open(temporary, "wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        temporary.replace(output)
        resolved.unlink()
        return {
            "status": "COMPRESSED",
            "ledger": str(output.relative_to(data_root.resolve())),
            "bytes": output.stat().st_size,
        }
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        return {
            "status": "ERROR",
            "ledger": str(ledger),
            "error": f"{type(exc).__name__}: {exc}",
        }


def commit_frames(
    state_frames_path: Path,
    data_root: Path,
    segment_id: str,
) -> dict[str, Any]:
    """Append unique frames, opening a new epoch whenever continuity is lost."""
    frames = _load_projected_frames(state_frames_path)
    if not frames:
        raise ValueError("segment contains no complete state frames")

    data_root.mkdir(parents=True, exist_ok=True)
    state = _load_state(data_root)
    epoch_number = int(state["active_epoch"])
    ledger = (
        data_root / str(state["active_epoch_file"])
        if state["active_epoch_file"]
        else None
    )
    ledger_last: int | None = None
    ledger_count = 0
    inactive_ledger: Path | None = None
    if ledger is not None:
        ledger_last, ledger_count = _ledger_tail(ledger)
        state["last_frame_end_ns"] = ledger_last
        state["epoch_frames"] = ledger_count

    if ledger is None:
        epoch_number = 1
        ledger = _new_epoch(data_root, epoch_number)
        state["active_epoch"] = epoch_number
        state["active_epoch_file"] = str(ledger.relative_to(data_root))
        state["active_epoch_segments_committed"] = 0
        state["last_detector_epoch_segments"] = 0

    if ledger_last is None:
        new_frames = frames
        continuity = "NEW_EPOCH"
    else:
        new_frames = [
            frame for frame in frames if int(frame["frame_end_ns"]) > ledger_last
        ]
        if not new_frames:
            continuity = "DUPLICATE_ONLY"
        elif int(new_frames[0]["frame_end_ns"]) == ledger_last + ONE_SECOND_NS:
            continuity = "CONTIGUOUS"
        else:
            inactive_ledger = ledger
            epoch_number += 1
            ledger = _new_epoch(data_root, epoch_number)
            state["active_epoch"] = epoch_number
            state["active_epoch_file"] = str(ledger.relative_to(data_root))
            state["last_frame_end_ns"] = None
            state["epoch_frames"] = 0
            state["active_epoch_segments_committed"] = 0
            state["last_detector_epoch_segments"] = 0
            continuity = "GAP_NEW_EPOCH"

    if new_frames:
        with ledger.open("a", encoding="utf-8", newline="\n") as handle:
            for frame in new_frames:
                handle.write(
                    json.dumps(frame, separators=(",", ":"), sort_keys=True) + "\n"
                )
        state["last_frame_end_ns"] = int(new_frames[-1]["frame_end_ns"])
        state["epoch_frames"] = int(state["epoch_frames"]) + len(new_frames)
        state["total_unique_frames"] = int(state["total_unique_frames"]) + len(
            new_frames
        )

    state["segments_committed"] = int(state["segments_committed"]) + 1
    state["active_epoch_segments_committed"] = int(
        state.get("active_epoch_segments_committed", 0)
    ) + 1
    state["updated_at_utc"] = _utc_now()
    _atomic_json(data_root / "forward_state.json", state)
    archived_epoch = (
        _archive_inactive_ledger(inactive_ledger, data_root)
        if inactive_ledger is not None
        else None
    )

    result = {
        "segment_id": segment_id,
        "status": "COMMITTED",
        "continuity": continuity,
        "input_frames": len(frames),
        "appended_frames": len(new_frames),
        "active_epoch": state["active_epoch"],
        "active_epoch_file": state["active_epoch_file"],
        "epoch_frames": state["epoch_frames"],
        "epoch_segments_committed": state["active_epoch_segments_committed"],
        "baseline_progress_pct": min(
            100.0, float(state["epoch_frames"]) / BASELINE_REQUIRED_FRAMES * 100.0
        ),
        "baseline_ready": int(state["epoch_frames"]) >= BASELINE_REQUIRED_FRAMES,
        "committed_at_utc": _utc_now(),
    }
    if archived_epoch is not None:
        result["archived_previous_epoch"] = archived_epoch
    return result


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def prune_completed_segments(data_root: Path, keep: int) -> dict[str, list[Any]]:
    """Remove old generated raw segments only after their manifest exists."""
    if keep < 0:
        raise ValueError("raw segment retention cannot be negative")
    segments_root = data_root / "segments"
    if not segments_root.exists():
        return {"removed": [], "failed": []}
    committed_ids: set[str] = set()
    audit_path = data_root / "segment_audit.jsonl"
    if audit_path.exists():
        with audit_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if record.get("status") == "COMMITTED" and record.get("segment_id"):
                    committed_ids.add(str(record["segment_id"]))
    completed = sorted(
        path
        for path in segments_root.iterdir()
        if path.is_dir()
        and path.name in committed_ids
        and (path / "manifest.json").exists()
    )
    removable = completed[:-keep] if keep else completed
    pending = sorted(
        path
        for path in segments_root.iterdir()
        if path.is_dir() and path.name.endswith(".prune_pending")
    )
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    targets: list[tuple[Path, str]] = [
        (path, path.name.removesuffix(".prune_pending")) for path in pending
    ]
    for path in removable:
        tombstone = path.with_name(path.name + ".prune_pending")
        try:
            path.replace(tombstone)
        except OSError as exc:
            failed.append(
                {"segment": path.name, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        targets.append((tombstone, path.name))

    for path, original_name in targets:
        last_error: OSError | None = None
        for attempt in range(3):
            try:
                shutil.rmtree(path, onerror=_remove_readonly)
                removed.append(original_name)
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
        if last_error is not None:
            failed.append(
                {
                    "segment": original_name,
                    "error": f"{type(last_error).__name__}: {last_error}",
                }
            )
    return {"removed": removed, "failed": failed}


def _write_heartbeat(data_root: Path, payload: dict[str, Any]) -> None:
    state = _load_state(data_root)
    heartbeat_path = data_root / "heartbeat.json"
    existing: dict[str, Any] = {}
    if heartbeat_path.exists():
        try:
            existing = _read_json(heartbeat_path)
        except (OSError, ValueError, json.JSONDecodeError):
            existing = {}
    heartbeat = {
        **existing,
        "schema_version": 1,
        "project": "btc-market-structure-lab",
        "instrument": "BTCUSDT_PERPETUAL",
        "updated_at_utc": _utc_now(),
        "contains_outcomes": False,
        "contains_pnl": False,
        "state": state,
        **payload,
    }
    _atomic_json(heartbeat_path, heartbeat)


def process_segment(manifest_path: Path, data_root: Path) -> dict[str, Any]:
    run_dir = manifest_path.parent
    segment_id = run_dir.name
    try:
        capture_manifest = _read_json(manifest_path)
        if not capture_manifest["core_streams_ready"]:
            raise ValueError("capture did not receive every required core stream")

        normalization_path = normalize_capture(run_dir)
        normalization = _read_json(normalization_path)
        if normalization["rejected_records"] or not normalization["output_events"]:
            raise ValueError("normalization rejected records or produced no events")

        normalized_path = run_dir / normalization["output_file"]
        replay = audit_replay(normalized_path)
        (run_dir / "book_replay_report.json").write_text(
            json.dumps(replay, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if not replay["passed"]:
            raise ReplayError("deterministic order-book replay failed")

        frame_manifest_path = build_file(normalized_path)
        frame_manifest = _read_json(frame_manifest_path)
        if not frame_manifest["frames"]:
            raise ValueError("state-frame builder produced no complete frames")

        commit = commit_frames(run_dir / frame_manifest["output"], data_root, segment_id)
        result = {
            **commit,
            "capture_ready": True,
            "capture_counts": {
                venue: capture_manifest["audits"][venue]["counts"]
                for venue in ("binance", "bybit")
            },
            "normalized_events": normalization["output_events"],
            "normalized_event_counts": normalization["event_counts"],
            "normalization_rejects": normalization["rejected_records"],
            "book_replay_passed": True,
            "book_replay_venues": replay["venues"],
            "state_frames": frame_manifest["frames"],
        }
        _append_json(data_root / "segment_audit.jsonl", result)
        _write_heartbeat(data_root, {"status": "OK", "last_segment": result})
        return result
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, ReplayError) as exc:
        state = _load_state(data_root)
        state["segments_rejected"] = int(state["segments_rejected"]) + 1
        state["updated_at_utc"] = _utc_now()
        _atomic_json(data_root / "forward_state.json", state)
        result = {
            "segment_id": segment_id,
            "status": "REJECTED",
            "error": f"{type(exc).__name__}: {exc}",
            "committed_at_utc": _utc_now(),
        }
        _append_json(data_root / "segment_audit.jsonl", result)
        _write_heartbeat(data_root, {"status": "DEGRADED", "last_segment": result})
        return result


def run_detector(data_root: Path) -> dict[str, Any]:
    state = _load_state(data_root)
    relative = state.get("active_epoch_file")
    if not relative:
        raise ValueError("no active forward epoch")
    ledger = data_root / str(relative)
    manifest_path = run_file(ledger)
    manifest = _read_json(manifest_path)
    evaluation_path = run_h001_evaluation(data_root)
    evaluation = _read_json(evaluation_path)
    state = _load_state(data_root)
    state["last_detector_epoch_segments"] = int(
        state.get("active_epoch_segments_committed", 0)
    )
    state["updated_at_utc"] = _utc_now()
    _atomic_json(data_root / "forward_state.json", state)
    _write_heartbeat(
        data_root,
        {
            "status": "OK",
            "h001": {
                "manifest": str(manifest_path),
                "baseline_ready": manifest["baseline_ready"],
                "baseline_observations": manifest["baseline_observations"],
                "candidates": manifest["candidates"],
                "accepted": manifest["accepted"],
                "rejected": manifest["rejected"],
            },
            "h001_evaluation": {
                "status": evaluation["status"],
                "complete_days": evaluation["days"]["complete_days"],
                "required_days": evaluation["days"].get("required_days"),
                "sealed_outcomes_released": evaluation["sealed_outcomes_released"],
                "matched_test_events": evaluation.get(
                    "sealed_test_sample_gate", {}
                ).get("matched", 0),
                "data_quality_passed": evaluation["data_quality_passed"],
                "report": str(evaluation_path),
            },
        },
    )
    return manifest


async def record_forward(
    duration_seconds: float,
    overlap_seconds: float,
    segments: int,
    data_root: Path,
    detector_every_segments: int,
    raw_retention_segments: int = 2,
) -> list[dict[str, Any]]:
    """Capture overlapping finite sessions; zero segments means run until stopped."""
    output_root = data_root / "segments"
    interval = duration_seconds - overlap_seconds
    results: list[dict[str, Any]] = []
    queue: asyncio.Queue[tuple[int, Path | None, BaseException | None] | None] = (
        asyncio.Queue()
    )
    stop_requested = asyncio.Event()

    async def capture_one(index: int) -> None:
        try:
            manifest = await capture(duration_seconds, output_root)
            await queue.put((index, manifest, None))
        except Exception as exc:
            await queue.put((index, None, exc))

    async def produce() -> None:
        active: set[asyncio.Task[None]] = set()
        try:
            index = 0
            while segments == 0 or index < segments:
                if stop_requested.is_set():
                    break
                task = asyncio.create_task(capture_one(index))
                active.add(task)
                task.add_done_callback(active.discard)
                index += 1
                if segments != 0 and index >= segments:
                    break
                await asyncio.sleep(interval)
            if active:
                await asyncio.gather(*active)
            await queue.put(None)
        finally:
            for task in active:
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)

    producer = asyncio.create_task(produce())
    buffered: dict[int, tuple[Path | None, BaseException | None]] = {}
    expected = 0
    producer_done = False
    try:
        while not producer_done or buffered:
            item = await queue.get()
            if item is None:
                producer_done = True
            else:
                index, manifest_path, error = item
                buffered[index] = (manifest_path, error)

            while expected in buffered:
                manifest_path, error = buffered.pop(expected)
                if error is not None or manifest_path is None:
                    result = {
                        "segment_id": f"capture_{expected:06d}",
                        "status": "REJECTED",
                        "error": (
                            f"{type(error).__name__}: {error}"
                            if error is not None
                            else "capture returned no manifest"
                        ),
                        "committed_at_utc": _utc_now(),
                    }
                    _append_json(data_root / "segment_audit.jsonl", result)
                    _write_heartbeat(
                        data_root, {"status": "DEGRADED", "last_segment": result}
                    )
                else:
                    result = await asyncio.to_thread(
                        process_segment, manifest_path, data_root
                    )
                if segments != 0:
                    results.append(result)
                expected += 1

                if result["status"] == "COMMITTED" and detector_every_segments > 0:
                    state = _load_state(data_root)
                    committed_since_detector = int(
                        state.get("active_epoch_segments_committed", 0)
                    ) - int(state.get("last_detector_epoch_segments", 0))
                    if committed_since_detector >= detector_every_segments:
                        await asyncio.to_thread(run_detector, data_root)
                        if study_is_sealed(data_root):
                            stop_requested.set()
                if result["status"] == "COMMITTED":
                    retention = await asyncio.to_thread(
                        prune_completed_segments, data_root, raw_retention_segments
                    )
                    if retention["removed"] or retention["failed"]:
                        _write_heartbeat(
                            data_root,
                            {
                                "status": "OK",
                                "raw_retention": {
                                    "kept_completed_segments": raw_retention_segments,
                                    "removed_segments": retention["removed"],
                                    "failed_segments": retention["failed"],
                                },
                            },
                        )
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=900.0)
    parser.add_argument("--overlap", type=float, default=15.0)
    parser.add_argument(
        "--segments",
        type=int,
        default=1,
        help="Finite segment count; use 0 only in a supervised 24/7 service.",
    )
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/forward"))
    parser.add_argument("--detector-every-segments", type=int, default=320)
    parser.add_argument("--raw-retention-segments", type=int, default=2)
    args = parser.parse_args()
    if args.duration <= 5 or args.duration > 3600:
        parser.error("duration must be greater than 5 and at most 3600 seconds")
    if args.overlap < 1 or args.overlap >= args.duration:
        parser.error("overlap must be at least 1 second and less than duration")
    if args.segments < 0:
        parser.error("segments cannot be negative")
    if args.detector_every_segments < 0:
        parser.error("detector-every-segments cannot be negative")
    if args.raw_retention_segments < 0:
        parser.error("raw-retention-segments cannot be negative")

    results = asyncio.run(
        record_forward(
            args.duration,
            args.overlap,
            args.segments,
            args.data_root,
            args.detector_every_segments,
            args.raw_retention_segments,
        )
    )
    summary = {
        "segments": len(results),
        "committed": sum(item["status"] == "COMMITTED" for item in results),
        "rejected": sum(item["status"] == "REJECTED" for item in results),
        "heartbeat": str(args.data_root / "heartbeat.json"),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
