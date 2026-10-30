from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from research.r63a_continuous_multivenue_collector import DEFAULT_OUTPUT_ROOT, DEFAULT_SYMBOLS


EXPECTED_VENUES = ("binance-futures", "bybit", "hyperliquid")
VERSION = "R63B_1"


def audit(output_root: Path, minimum_common_minutes: int = 2) -> dict[str, Any]:
    status_path = output_root / "R63A_STATUS.json"
    database_path = output_root / "R63A_FLOW.sqlite3"
    issues: list[str] = []
    warnings: list[str] = []
    if not status_path.exists():
        issues.append("missing R63A_STATUS.json")
        status: dict[str, Any] = {}
    else:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    coverage: dict[str, Any] = {}
    complete_minutes: dict[str, set[int]] = {}
    if not database_path.exists():
        issues.append("missing R63A_FLOW.sqlite3")
    else:
        connection = sqlite3.connect(database_path)
        rows = connection.execute(
            "SELECT venue,symbol,COUNT(*),SUM(trade_count),SUM(quote_count),"
            "MIN(minute_utc),MAX(minute_utc) FROM minute_flow WHERE complete=1 "
            "GROUP BY venue,symbol ORDER BY venue,symbol"
        ).fetchall()
        for venue, symbol, minutes, trades, quotes, first_minute, last_minute in rows:
            key = f"{venue}:{symbol}"
            coverage[key] = {
                "complete_minutes": int(minutes),
                "trade_count": int(trades or 0),
                "quote_count": int(quotes or 0),
                "first_minute_utc": first_minute,
                "last_minute_utc": last_minute,
            }
        for venue, symbol, minute_start_ms in connection.execute(
            "SELECT venue,symbol,minute_start_ms FROM minute_flow WHERE complete=1"
        ):
            complete_minutes.setdefault(f"{venue}:{symbol}", set()).add(int(minute_start_ms))
        connection.close()

    expected_cells = [f"{venue}:{symbol}" for venue in EXPECTED_VENUES for symbol in DEFAULT_SYMBOLS]
    for key in expected_cells:
        cell = coverage.get(key)
        if cell is None:
            issues.append(f"missing complete cell {key}")
            continue
        if cell["complete_minutes"] < minimum_common_minutes:
            issues.append(
                f"insufficient complete minutes {key}: {cell['complete_minutes']} < {minimum_common_minutes}"
            )
        if cell["trade_count"] <= 0:
            issues.append(f"no trades in complete minutes {key}")
        if cell["quote_count"] <= 0:
            issues.append(f"no quotes in complete minutes {key}")

    if expected_cells and all(key in complete_minutes for key in expected_cells):
        common = set.intersection(*(complete_minutes[key] for key in expected_cells))
    else:
        common = set()
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

    fatal_states = {"DEPENDENCY_MISSING", "INVALID_CONFIGURATION"}
    for venue, state in status.get("streams", {}).items():
        if state in fatal_states:
            issues.append(f"fatal stream state {venue}: {state}")
    late_events = int(status.get("late_events", 0))
    if late_events:
        issues.append(f"late events dropped: {late_events}")
    duplicates = int(status.get("duplicate_trades", 0))
    if duplicates:
        warnings.append(f"duplicate trades removed: {duplicates}")
    errors = status.get("errors", [])
    if errors:
        warnings.append(f"reconnect/parser errors recorded: {len(errors)}")
    out_of_order = int(status.get("out_of_order_events", 0))
    if out_of_order:
        warnings.append(f"out-of-order events observed: {out_of_order}")
    replay_events = int(status.get("replay_events_ignored", 0))
    if replay_events:
        warnings.append(f"reconnect replay events ignored: {replay_events}")

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
        "expected_cells": len(expected_cells),
        "covered_cells": sum(key in coverage for key in expected_cells),
        "coverage": coverage,
        "issues": issues,
        "warnings": warnings,
        "collector_version": status.get("version"),
        "ignored_events": status.get("ignored_events", {}),
        "out_of_order_by_stream": status.get("out_of_order_by_stream", {}),
    }


def save_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit R63A multi-venue collector coverage.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--minimum-common-minutes", type=int, default=2)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = audit(args.output_root, args.minimum_common_minutes)
    report_path = args.report or args.output_root / "R63B_AUDIT.json"
    save_report(report, report_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
