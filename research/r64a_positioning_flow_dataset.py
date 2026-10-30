from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any

from research.r63a_continuous_multivenue_collector import DEFAULT_SYMBOLS, MINUTE_MS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FLOW_DB = ROOT / "forward_data" / "R63A_multivenue_flow" / "R63A_FLOW.sqlite3"
DEFAULT_POSITIONING_DB = (
    ROOT / "forward_data" / "R63C_positioning_liquidation" / "R63C_POSITIONING.sqlite3"
)
DEFAULT_OUTPUT = ROOT / "r64_output" / "R64A_POSITIONING_FLOW_DATASET"
VENUES = ("binance-futures", "bybit", "hyperliquid")
REFERENCE_VENUE = "binance-futures"
FUNDING_PERIOD_HOURS = {"binance-futures": 8.0, "bybit": 8.0, "hyperliquid": 1.0}
WINDOWS = (5, 15, 60, 240)
WARMUP_MINUTES = 240
ROUND_TRIP_COST_BPS = 10.0
VOLATILITY_MULTIPLIER = 1.25
HORIZONS = {
    "1h": {"minutes": 60, "min_barrier_bps": 35.0, "max_barrier_bps": 200.0},
    "4h": {"minutes": 240, "min_barrier_bps": 70.0, "max_barrier_bps": 400.0},
}
VERSION = "R64A_1"


def median(values: list[float]) -> float:
    return float(statistics.median(values))


def population_stdev(values: list[float]) -> float:
    return float(statistics.pstdev(values)) if len(values) > 1 else 0.0


def load_aligned(
    flow_db: Path, positioning_db: Path, symbols: tuple[str, ...] = DEFAULT_SYMBOLS
) -> dict[str, list[dict[str, Any]]]:
    wanted = set(symbols)
    flow: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    connection = sqlite3.connect(flow_db)
    for row in connection.execute(
        "SELECT venue,symbol,minute_start_ms,minute_utc,trade_count,buy_notional,sell_notional,"
        "midpoint_open,midpoint_high,midpoint_low,midpoint_close,spread_bps_mean,"
        "book_imbalance_mean FROM minute_flow WHERE complete=1 ORDER BY minute_start_ms"
    ):
        venue, symbol, minute = str(row[0]), str(row[1]), int(row[2])
        if venue not in VENUES or symbol not in wanted:
            continue
        flow.setdefault((symbol, minute), {})[venue] = {
            "minute_utc": str(row[3]), "trade_count": int(row[4]),
            "buy_notional": float(row[5]), "sell_notional": float(row[6]),
            "midpoint_open": float(row[7]), "midpoint_high": float(row[8]),
            "midpoint_low": float(row[9]), "midpoint_close": float(row[10]),
            "spread_bps": float(row[11]), "book_imbalance": float(row[12]),
        }
    connection.close()

    positioning: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    connection = sqlite3.connect(positioning_db)
    for row in connection.execute(
        "SELECT venue,symbol,minute_start_ms,mark_open,mark_close,oi_open,oi_close,funding_close,"
        "long_liq_count,short_liq_count,long_liq_notional,short_liq_notional "
        "FROM minute_positioning WHERE complete=1 ORDER BY minute_start_ms"
    ):
        venue, symbol, minute = str(row[0]), str(row[1]), int(row[2])
        if venue not in VENUES or symbol not in wanted:
            continue
        positioning.setdefault((symbol, minute), {})[venue] = {
            "mark_open": float(row[3]), "mark_close": float(row[4]),
            "oi_open": float(row[5]), "oi_close": float(row[6]),
            "funding": float(row[7]), "long_liq_count": int(row[8]),
            "short_liq_count": int(row[9]), "long_liq_notional": float(row[10]),
            "short_liq_notional": float(row[11]),
        }
    connection.close()

    output: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    for key in sorted(set(flow) & set(positioning), key=lambda item: (item[0], item[1])):
        symbol, minute = key
        if set(flow[key]) != set(VENUES) or set(positioning[key]) != set(VENUES):
            continue
        output[symbol].append({
            "symbol": symbol,
            "minute_start_ms": minute,
            "minute_utc": datetime.fromtimestamp(minute / 1000, timezone.utc),
            "flow": flow[key],
            "positioning": positioning[key],
        })
    return output


def is_contiguous(rows: list[dict[str, Any]], start: int, end: int) -> bool:
    if start < 0 or end >= len(rows) or start > end:
        return False
    return all(
        rows[index]["minute_start_ms"] - rows[index - 1]["minute_start_ms"] == MINUTE_MS
        for index in range(start + 1, end + 1)
    )


def feature_snapshot(rows: list[dict[str, Any]], cutoff_index: int) -> dict[str, Any]:
    if cutoff_index < WARMUP_MINUTES:
        raise ValueError("insufficient feature history")
    result: dict[str, Any] = {
        "feature_cutoff_utc": rows[cutoff_index]["minute_utc"].isoformat(),
        "feature_cutoff_ms": rows[cutoff_index]["minute_start_ms"] + MINUTE_MS,
    }
    for window_size in WINDOWS:
        start = cutoff_index - window_size + 1
        if start < 1 or not is_contiguous(rows, start - 1, cutoff_index):
            raise ValueError("feature window contains a real minute gap")
        window = rows[start:cutoff_index + 1]
        previous = rows[start - 1]
        flows: list[float] = []
        returns: list[float] = []
        oi_changes: list[float] = []
        fundings: list[float] = []
        books: list[float] = []
        spreads: list[float] = []
        liq_pressures: list[float] = []
        for venue in VENUES:
            buy = sum(item["flow"][venue]["buy_notional"] for item in window)
            sell = sum(item["flow"][venue]["sell_notional"] for item in window)
            total_notional = buy + sell
            flow_value = (buy - sell) / total_notional if total_notional else 0.0
            first_mid = previous["flow"][venue]["midpoint_close"]
            last_mid = window[-1]["flow"][venue]["midpoint_close"]
            return_value = last_mid / first_mid - 1.0
            oi_start = window[0]["positioning"][venue]["oi_open"]
            oi_end = window[-1]["positioning"][venue]["oi_close"]
            oi_change = oi_end / oi_start - 1.0 if oi_start else 0.0
            funding = (
                window[-1]["positioning"][venue]["funding"] / FUNDING_PERIOD_HOURS[venue]
            )
            long_liq = sum(item["positioning"][venue]["long_liq_notional"] for item in window)
            short_liq = sum(item["positioning"][venue]["short_liq_notional"] for item in window)
            liq_pressure = (short_liq - long_liq) / total_notional if total_notional else 0.0
            book = statistics.fmean(item["flow"][venue]["book_imbalance"] for item in window)
            spread = statistics.fmean(item["flow"][venue]["spread_bps"] for item in window)
            key = venue.replace("-", "_")
            suffix = f"_{window_size}m"
            result.update({
                f"{key}_flow{suffix}": flow_value,
                f"{key}_return{suffix}": return_value,
                f"{key}_oi_change{suffix}": oi_change,
                f"{key}_funding_hourly{suffix}": funding,
                f"{key}_book{suffix}": book,
                f"{key}_spread_bps{suffix}": spread,
                f"{key}_liq_pressure{suffix}": liq_pressure,
            })
            flows.append(flow_value)
            returns.append(return_value)
            oi_changes.append(oi_change)
            fundings.append(funding)
            books.append(book)
            spreads.append(spread)
            liq_pressures.append(liq_pressure)
        suffix = f"_{window_size}m"
        result.update({
            f"flow_median{suffix}": median(flows),
            f"flow_dispersion{suffix}": population_stdev(flows),
            f"flow_long_votes{suffix}": sum(value > 0 for value in flows),
            f"flow_short_votes{suffix}": sum(value < 0 for value in flows),
            f"return_median{suffix}": median(returns),
            f"return_dispersion{suffix}": population_stdev(returns),
            f"oi_change_median{suffix}": median(oi_changes),
            f"oi_change_dispersion{suffix}": population_stdev(oi_changes),
            f"funding_median{suffix}": median(fundings),
            f"funding_dispersion{suffix}": population_stdev(fundings),
            f"book_median{suffix}": median(books),
            f"spread_median_bps{suffix}": median(spreads),
            f"liq_pressure_median{suffix}": median(liq_pressures),
        })
    return result


def trailing_volatility(rows: list[dict[str, Any]], cutoff_index: int) -> float:
    start = cutoff_index - WARMUP_MINUTES
    if start < 0 or not is_contiguous(rows, start, cutoff_index):
        raise ValueError("insufficient contiguous volatility history")
    prices = [rows[index]["flow"][REFERENCE_VENUE]["midpoint_close"] for index in range(start, cutoff_index + 1)]
    returns = [math.log(current / previous) for previous, current in zip(prices, prices[1:])]
    return population_stdev(returns)


def directional_label(rows: list[dict[str, Any]], cutoff_index: int, timeframe: str) -> dict[str, Any]:
    spec = HORIZONS[timeframe]
    horizon = int(spec["minutes"])
    entry_index = cutoff_index + 1
    end_index = entry_index + horizon - 1
    if not is_contiguous(rows, cutoff_index, end_index):
        raise ValueError("future path is missing or contains a gap")
    entry = rows[entry_index]["flow"][REFERENCE_VENUE]["midpoint_open"]
    sigma = trailing_volatility(rows, cutoff_index)
    raw_barrier = VOLATILITY_MULTIPLIER * sigma * math.sqrt(horizon) * 10_000.0
    barrier_bps = min(max(raw_barrier, spec["min_barrier_bps"]), spec["max_barrier_bps"])
    upper = entry * (1.0 + barrier_bps / 10_000.0)
    lower = entry * (1.0 - barrier_bps / 10_000.0)
    label = "NO_TRADE"
    first_touch_minutes: int | None = None
    ambiguous_touch = False
    for offset, index in enumerate(range(entry_index, end_index + 1), start=1):
        candle = rows[index]["flow"][REFERENCE_VENUE]
        hit_upper = candle["midpoint_high"] >= upper
        hit_lower = candle["midpoint_low"] <= lower
        if hit_upper and hit_lower:
            ambiguous_touch = True
            first_touch_minutes = offset
            break
        if hit_upper:
            label, first_touch_minutes = "LONG", offset
            break
        if hit_lower:
            label, first_touch_minutes = "SHORT", offset
            break
    terminal = rows[end_index]["flow"][REFERENCE_VENUE]["midpoint_close"] / entry - 1.0
    return {
        "timeframe": timeframe,
        "entry_utc": rows[entry_index]["minute_utc"].isoformat(),
        "label_end_utc": rows[end_index]["minute_utc"].isoformat(),
        "entry_midpoint": entry,
        "barrier_bps": barrier_bps,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "label": label,
        "first_touch_minutes": first_touch_minutes,
        "ambiguous_touch": int(ambiguous_touch),
        "terminal_return_bps": terminal * 10_000.0,
    }


def is_anchor(stamp: datetime, timeframe: str) -> bool:
    if timeframe == "1h":
        return stamp.minute in {14, 29, 44, 59}
    return stamp.minute == 59


def build_samples(rows_by_symbol: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for symbol, rows in rows_by_symbol.items():
        for cutoff_index in range(WARMUP_MINUTES, len(rows)):
            stamp = rows[cutoff_index]["minute_utc"]
            for timeframe in HORIZONS:
                if not is_anchor(stamp, timeframe):
                    continue
                try:
                    features = feature_snapshot(rows, cutoff_index)
                    label = directional_label(rows, cutoff_index, timeframe)
                except ValueError:
                    continue
                samples.append({"symbol": symbol, **features, **label})
    return samples


def run(flow_db: Path, positioning_db: Path, output_dir: Path) -> dict[str, Any]:
    rows_by_symbol = load_aligned(flow_db, positioning_db)
    samples = build_samples(rows_by_symbol)
    output_dir.mkdir(parents=True, exist_ok=True)
    if samples:
        with (output_dir / "R64A_CAUSAL_ROWS.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(samples[0]))
            writer.writeheader()
            writer.writerows(samples)
    rows_per_symbol = {symbol: len(rows) for symbol, rows in rows_by_symbol.items()}
    verdict = {
        "stage": "R64A_POSITIONING_FLOW_DATASET",
        "version": VERSION,
        "status": "CAUSAL_ROWS_READY_FOR_DAY_BLOCKED_RESEARCH" if samples else "ACCUMULATING_CONTIGUOUS_DATA",
        "aligned_minutes_by_symbol": rows_per_symbol,
        "sample_rows": len(samples),
        "samples_by_symbol": dict(Counter(item["symbol"] for item in samples)),
        "samples_by_timeframe": dict(Counter(item["timeframe"] for item in samples)),
        "labels": dict(Counter(item["label"] for item in samples)),
        "ambiguous_touch_rows": sum(item["ambiguous_touch"] for item in samples),
        "performance_metrics": None,
        "edge_validated": False,
        "real_money_authorized": False,
        "production_changed": False,
    }
    (output_dir / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the causal R64 positioning-flow dataset.")
    parser.add_argument("--flow-db", type=Path, default=DEFAULT_FLOW_DB)
    parser.add_argument("--positioning-db", type=Path, default=DEFAULT_POSITIONING_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.flow_db, args.positioning_db, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
