from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from research.r63a_continuous_multivenue_collector import DEFAULT_SYMBOLS
from research.r63c_positioning_liquidation_collector import DEFAULT_OUTPUT_ROOT


EXPECTED_VENUES = ("binance-futures", "bybit", "hyperliquid")
VERSION = "R63D_1"


def audit(output_root: Path, minimum_common_minutes: int = 2) -> dict[str, Any]:
    status_path = output_root / "R63C_STATUS.json"
    database_path = output_root / "R63C_POSITIONING.sqlite3"
    issues: list[str] = []
    warnings: list[str] = []
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    if not status:
        issues.append("missing R63C_STATUS.json")
    coverage: dict[str, Any] = {}
    minute_sets: dict[str, set[int]] = {}
    if not database_path.exists():
        issues.append("missing R63C_POSITIONING.sqlite3")
    else:
        connection = sqlite3.connect(database_path)
        rows = connection.execute(
            "SELECT venue,symbol,COUNT(*),SUM(context_count),SUM(long_liq_count),"
            "SUM(short_liq_count),SUM(long_liq_notional),SUM(short_liq_notional),"
            "MIN(minute_utc),MAX(minute_utc) FROM minute_positioning WHERE complete=1 "
            "GROUP BY venue,symbol ORDER BY venue,symbol"
        ).fetchall()
        for row in rows:
            venue, symbol = str(row[0]), str(row[1])
            coverage[f"{venue}:{symbol}"] = {
                "complete_minutes": int(row[2]),
                "context_count": int(row[3] or 0),
                "long_liq_count": int(row[4] or 0),
                "short_liq_count": int(row[5] or 0),
                "long_liq_notional": float(row[6] or 0.0),
                "short_liq_notional": float(row[7] or 0.0),
                "first_minute_utc": row[8],
                "last_minute_utc": row[9],
            }
        for venue, symbol, minute in connection.execute(
            "SELECT venue,symbol,minute_start_ms FROM minute_positioning WHERE complete=1"
        ):
            minute_sets.setdefault(f"{venue}:{symbol}", set()).add(int(minute))
        connection.close()

    expected = [f"{venue}:{symbol}" for venue in EXPECTED_VENUES for symbol in DEFAULT_SYMBOLS]
    for key in expected:
        cell = coverage.get(key)
        if cell is None:
            issues.append(f"missing complete cell {key}")
            continue
        if cell["complete_minutes"] < minimum_common_minutes:
            issues.append(
                f"insufficient complete minutes {key}: {cell['complete_minutes']} < {minimum_common_minutes}"
            )
        if cell["context_count"] <= 0:
            issues.append(f"no positioning context {key}")

    common = set.intersection(*(minute_sets[key] for key in expected)) if all(
        key in minute_sets for key in expected
    ) else set()
    if len(common) < minimum_common_minutes:
        issues.append(f"common complete minutes {len(common)} < {minimum_common_minutes}")
    if common:
        common_span_minutes = int((max(common) - min(common)) / 60_000) + 1
        common_coverage_rate = len(common) / common_span_minutes
        ordered_common = sorted(common)
        common_contiguous_tail = 1
        for previous, current in zip(reversed(ordered_common[:-1]), reversed(ordered_common[1:])):
            if current - previous != 60_000:
                break
            common_contiguous_tail += 1
    else:
        common_span_minutes = 0
        common_coverage_rate = 0.0
        common_contiguous_tail = 0
    if common_contiguous_tail < minimum_common_minutes:
        issues.append(
            f"contiguous common tail {common_contiguous_tail} < {minimum_common_minutes}"
        )
    if common_span_minutes and common_coverage_rate < 0.995:
        warnings.append(f"historical common minute coverage is {common_coverage_rate:.6f}")

    for venue, state in status.get("streams", {}).items():
        if state in {"DEPENDENCY_MISSING", "INVALID_CONFIGURATION"}:
            issues.append(f"fatal stream state {venue}: {state}")
    late = int(status.get("late_events", 0))
    if late:
        issues.append(f"late events dropped: {late}")
    duplicates = int(status.get("duplicate_liquidations", 0))
    if duplicates:
        warnings.append(f"duplicate liquidations removed: {duplicates}")
    errors = status.get("errors", [])
    if errors:
        warnings.append(f"collector errors recorded: {len(errors)}")

    return {
        "audit_version": VERSION,
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "pass": not issues,
        "minimum_common_minutes": minimum_common_minutes,
        "common_complete_minutes": len(common),
        "common_span_minutes": common_span_minutes,
        "common_missing_minutes": common_span_minutes - len(common),
        "common_coverage_rate": common_coverage_rate,
        "common_contiguous_tail_minutes": common_contiguous_tail,
        "common_first_utc": (
            datetime.fromtimestamp(min(common) / 1000, timezone.utc).isoformat() if common else None
        ),
        "common_last_utc": (
            datetime.fromtimestamp(max(common) / 1000, timezone.utc).isoformat() if common else None
        ),
        "expected_cells": len(expected),
        "covered_cells": sum(key in coverage for key in expected),
        "coverage": coverage,
        "issues": issues,
        "warnings": warnings,
        "collector_version": status.get("version"),
    }


def save_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit R63C positioning collector coverage.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--minimum-common-minutes", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = audit(args.output_root, args.minimum_common_minutes)
    save_report(report, args.output_root / "R63D_AUDIT.json")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
