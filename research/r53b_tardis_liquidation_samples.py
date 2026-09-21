"""Download and audit free monthly CORE4 Tardis liquidation samples.

Tardis permits unauthenticated downloads for the first day of each month. This
collector deliberately requests only those days and only CORE4 symbols. The
result is a sparse feasibility panel, not continuous history and not promotion
evidence.
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from r53a_tardis_microstructure_probe import (
    CORE_SYMBOLS,
    ROOT,
    aggregate_liquidations,
    dataset_url,
)


DEFAULT_DATA_DIR = ROOT / "data" / "r53_tardis" / "liquidations"
DEFAULT_OUTPUT_DIR = ROOT / "r53b_output" / "reports" / "R53B_LIQUIDATION_SAMPLES"
SYMBOL_START_MONTH = {
    # December 2019 probes do not expose a valid normalized liquidation file.
    "BTCUSDT": "2020-01",
    "ETHUSDT": "2020-01",
    "SOLUSDT": "2020-10",
    "BNBUSDT": "2020-03",
}
USER_AGENT = "Mozilla/5.0 (compatible; R53 research audit)"


@dataclass(frozen=True)
class SampleTask:
    symbol: str
    date: str
    url: str
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-month", default="2019-12")
    parser.add_argument("--to-month", default="2026-09")
    parser.add_argument("--symbols", nargs="+", choices=CORE_SYMBOLS, default=list(CORE_SYMBOLS))
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    return parser.parse_args()


def month_starts(from_month: str, to_month: str) -> list[pd.Timestamp]:
    start = pd.Period(from_month, freq="M")
    end = pd.Period(to_month, freq="M")
    if end < start:
        raise ValueError("to-month must not be earlier than from-month")
    return [period.to_timestamp() for period in pd.period_range(start, end, freq="M")]


def build_tasks(
    data_dir: Path,
    from_month: str,
    to_month: str,
    symbols: list[str] | tuple[str, ...],
) -> list[SampleTask]:
    requested = month_starts(from_month, to_month)
    tasks: list[SampleTask] = []
    for symbol in symbols:
        launch = pd.Period(SYMBOL_START_MONTH[symbol], freq="M").to_timestamp()
        for stamp in requested:
            if stamp < launch:
                continue
            date = stamp.strftime("%Y-%m-%d")
            tasks.append(
                SampleTask(
                    symbol=symbol,
                    date=date,
                    url=dataset_url("liquidations", date, symbol),
                    path=data_dir / symbol / f"{date}.csv.gz",
                )
            )
    return tasks


def validate_gzip(path: Path) -> tuple[bool, str | None]:
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            header = handle.readline().strip()
        required = {"exchange", "symbol", "timestamp", "local_timestamp", "side", "price", "amount"}
        columns = set(header.split(",")) if header else set()
        missing = sorted(required - columns)
        if missing:
            return False, f"missing columns: {missing}"
        return True, None
    except (OSError, UnicodeError) as exc:
        return False, f"invalid gzip/csv: {exc}"


def download(task: SampleTask, retries: int = 3) -> dict[str, Any]:
    task.path.parent.mkdir(parents=True, exist_ok=True)
    if task.path.exists():
        valid, error = validate_gzip(task.path)
        if valid:
            return {"status": "cached", "bytes": task.path.stat().st_size, "error": None}
        task.path.unlink()
        if error:
            last_error = error
        else:
            last_error = "invalid cached file"
    else:
        last_error = None

    temporary = task.path.with_suffix(task.path.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(task.url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=90) as response:
                with temporary.open("wb") as handle:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        handle.write(block)
            valid, error = validate_gzip(temporary)
            if not valid:
                raise ValueError(error)
            temporary.replace(task.path)
            return {"status": "downloaded", "bytes": task.path.stat().st_size, "error": None}
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code == 404:
                temporary.unlink(missing_ok=True)
                return {"status": "unavailable", "bytes": 0, "error": last_error}
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        temporary.unlink(missing_ok=True)
        if attempt < retries:
            time.sleep(float(attempt))
    return {"status": "error", "bytes": 0, "error": last_error}


def audit_file(task: SampleTask) -> dict[str, Any]:
    if not task.path.exists():
        return {
            **asdict(task),
            "path": str(task.path.resolve()),
            "status": "missing",
            "bytes": 0,
            "hours": 0,
            "events": 0,
            "notional": 0.0,
        }
    valid, error = validate_gzip(task.path)
    if not valid:
        return {
            **asdict(task),
            "path": str(task.path.resolve()),
            "status": "invalid",
            "error": error,
            "bytes": task.path.stat().st_size,
            "hours": 0,
            "events": 0,
            "notional": 0.0,
        }
    frame = aggregate_liquidations(task.path)
    return {
        **asdict(task),
        "path": str(task.path.resolve()),
        "status": "ready",
        "error": None,
        "bytes": task.path.stat().st_size,
        "hours": int(len(frame)),
        "events": int(frame["liquidation_count"].sum()) if not frame.empty else 0,
        "notional": float(frame["liquidation_notional"].sum()) if not frame.empty else 0.0,
    }


def write_outputs(output_dir: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = pd.DataFrame(rows)
    manifest.to_csv(output_dir / "R53B_SAMPLE_MANIFEST.csv", index=False)
    ready = manifest.loc[manifest["status"].eq("ready")] if not manifest.empty else manifest
    verdict = {
        "audit_id": "R53B_LIQUIDATION_SAMPLES",
        "purpose": "SPARSE_SAMPLE_FEASIBILITY_NOT_ALPHA_EVIDENCE",
        "requested_files": int(len(manifest)),
        "ready_files": int(len(ready)),
        "coverage_pct": float(len(ready) / len(manifest) * 100.0) if len(manifest) else 0.0,
        "compressed_bytes": int(ready["bytes"].sum()) if not ready.empty else 0,
        "liquidation_events": int(ready["events"].sum()) if not ready.empty else 0,
        "liquidation_notional": float(ready["notional"].sum()) if not ready.empty else 0.0,
        "symbols_ready": sorted(ready["symbol"].unique().tolist()) if not ready.empty else [],
        "promotion_eligible": False,
        "status": "PASS" if len(ready) == len(manifest) and len(manifest) > 0 else "INCOMPLETE",
    }
    (output_dir / "R53B_VERDICT.json").write_text(
        json.dumps(verdict, indent=2, sort_keys=True), encoding="utf-8"
    )
    lines = [
        "# R53B Free Liquidation Sample Audit",
        "",
        f"- Status: **{verdict['status']}**.",
        f"- Ready files: `{verdict['ready_files']}/{verdict['requested_files']}`.",
        f"- CORE4 symbols represented: `{len(verdict['symbols_ready'])}/4`.",
        f"- Liquidation events: `{verdict['liquidation_events']}`.",
        f"- Compressed size: `{verdict['compressed_bytes']}` bytes.",
        "- Promotion eligible: **False**. Monthly day-01 samples are sparse.",
        "",
        "A complete result authorizes a bounded sparse-panel event study. It does not replace continuous full-history data and cannot change the R31A 8/15 verdict.",
        "",
    ]
    (output_dir / "R53B_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return verdict


def main() -> None:
    args = parse_args()
    tasks = build_tasks(args.data_dir, args.from_month, args.to_month, args.symbols)
    if args.limit is not None:
        tasks = tasks[: max(args.limit, 0)]
    if args.download:
        for index, task in enumerate(tasks, start=1):
            result = download(task)
            print(
                f"[{index}/{len(tasks)}] {task.symbol} {task.date}: "
                f"{result['status']} ({result['bytes']} bytes)",
                flush=True,
            )
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
    rows = [audit_file(task) for task in tasks]
    verdict = write_outputs(args.output_dir, rows)
    print(json.dumps(verdict, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
