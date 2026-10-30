from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from research.r64a_positioning_flow_dataset import DEFAULT_OUTPUT as R64A_OUTPUT


DEFAULT_INPUT = R64A_OUTPUT / "R64A_CAUSAL_ROWS.csv"
DEFAULT_OUTPUT = R64A_OUTPUT.parent / "R64B_FROZEN_MECHANISM_SCREEN"
VERSION = "R64B_1"
HORIZON_MINUTES = {"1h": 60, "4h": 240}


def sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def direction_name(value: int) -> str:
    return "LONG" if value > 0 else "SHORT"


def healthy_trend(row: dict[str, Any]) -> dict[str, Any] | None:
    direction = sign(float(row["return_median_60m"]))
    if not direction:
        return None
    votes = float(row["flow_long_votes_15m"] if direction > 0 else row["flow_short_votes_15m"])
    checks = {
        "return_size": abs(float(row["return_median_60m"])) >= 0.0010,
        "return_alignment": direction * float(row["return_median_15m"]) > 0,
        "flow_alignment": direction * float(row["flow_median_15m"]) > 0,
        "flow_votes": votes >= 2,
        "oi_expansion": float(row["oi_change_median_60m"]) > 0,
        "book_alignment": direction * float(row["book_median_15m"]) > 0,
        "funding_not_extreme": direction * float(row["funding_median_60m"]) < 0.00005,
        "spread_controlled": float(row["spread_median_bps_15m"]) <= 10.0,
    }
    score = sum(checks.values())
    if score < 6 or not checks["flow_alignment"] or not checks["flow_votes"]:
        return None
    return {"family": "HEALTHY_TREND", "direction": direction_name(direction), "score": score}


def deleveraging_continuation(row: dict[str, Any]) -> dict[str, Any] | None:
    direction = sign(float(row["return_median_15m"]))
    if not direction:
        return None
    votes = float(row["flow_long_votes_5m"] if direction > 0 else row["flow_short_votes_5m"])
    liquidation_alignment = sum(
        direction * float(row[f"{venue}_liq_pressure_15m"])
        for venue in ("binance_futures", "bybit", "hyperliquid")
    )
    checks = {
        "return_size": abs(float(row["return_median_15m"])) >= 0.0015,
        "return_alignment": direction * float(row["return_median_5m"]) > 0,
        "flow_alignment": direction * float(row["flow_median_5m"]) > 0,
        "flow_votes": votes >= 2,
        "oi_contraction": float(row["oi_change_median_15m"]) < 0,
        "liquidation_alignment": liquidation_alignment > 0,
        "book_alignment": direction * float(row["book_median_5m"]) > 0,
        "spread_controlled": float(row["spread_median_bps_5m"]) <= 12.0,
    }
    score = sum(checks.values())
    if score < 6 or not checks["oi_contraction"] or not checks["liquidation_alignment"]:
        return None
    return {
        "family": "DELEVERAGING_CONTINUATION",
        "direction": direction_name(direction),
        "score": score,
    }


def crowded_breakout_failure(row: dict[str, Any]) -> dict[str, Any] | None:
    crowded_direction = sign(float(row["return_median_60m"]))
    if not crowded_direction:
        return None
    opposite_votes = float(
        row["flow_short_votes_5m"] if crowded_direction > 0 else row["flow_long_votes_5m"]
    )
    checks = {
        "extension": abs(float(row["return_median_60m"])) >= 0.0035,
        "higher_window_alignment": crowded_direction * float(row["return_median_240m"]) > 0,
        "oi_expansion": float(row["oi_change_median_60m"]) > 0,
        "funding_crowded": crowded_direction * float(row["funding_median_60m"]) > 0.00001,
        "flow_failure": crowded_direction * float(row["flow_median_5m"]) <= 0,
        "book_absorption": crowded_direction * float(row["book_median_5m"]) <= 0,
        "opposite_flow_votes": opposite_votes >= 2,
    }
    score = sum(checks.values())
    if score < 5 or not checks["extension"] or not checks["funding_crowded"]:
        return None
    return {
        "family": "CROWDED_BREAKOUT_FAILURE",
        "direction": direction_name(-crowded_direction),
        "score": score,
    }


def select_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    for detector in (deleveraging_continuation, crowded_breakout_failure, healthy_trend):
        candidate = detector(row)
        if candidate is not None:
            return candidate
    return None


def score_candidate(row: dict[str, Any], direction: str) -> float:
    barrier = float(row["barrier_bps"])
    cost = float(row["round_trip_cost_bps"])
    label = str(row["label"])
    if int(float(row.get("ambiguous_touch", 0))):
        pnl_bps = -barrier - cost
    elif label == direction:
        pnl_bps = barrier - cost
    elif label in {"LONG", "SHORT"}:
        pnl_bps = -barrier - cost
    else:
        terminal = float(row["terminal_return_bps"])
        pnl_bps = (terminal if direction == "LONG" else -terminal) - cost
    return pnl_bps / barrier if barrier > 0 else -1.0


def apply_cooldown(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    next_allowed: dict[tuple[str, str], datetime] = {}
    for candidate in sorted(candidates, key=lambda item: item["entry_time"]):
        key = (candidate["symbol"], candidate["timeframe"])
        entry = candidate["entry_time"]
        if entry < next_allowed.get(key, datetime.min.replace(tzinfo=entry.tzinfo)):
            continue
        selected.append(candidate)
        next_allowed[key] = entry + timedelta(minutes=HORIZON_MINUTES[candidate["timeframe"]])
    return selected


def metrics(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return {
        "count": len(values),
        "win_rate": len(wins) / len(values) if values else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (None if gross_profit else 0.0),
        "mean_r": sum(values) / len(values) if values else 0.0,
        "max_drawdown_r": max_drawdown,
    }


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run(input_path: Path = DEFAULT_INPUT, output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    rows = load_rows(input_path)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        candidate = select_candidate(row)
        if candidate is None:
            continue
        entry_time = datetime.fromisoformat(row["entry_utc"])
        candidates.append({
            "symbol": row["symbol"], "timeframe": row["timeframe"],
            "entry_time": entry_time, "entry_utc": row["entry_utc"],
            "feature_cutoff_utc": row["feature_cutoff_utc"],
            "family": candidate["family"], "direction": candidate["direction"],
            "score": candidate["score"],
            "label": row["label"],
            "r_net": score_candidate(row, candidate["direction"]),
        })
    independent = apply_cooldown(candidates)
    days = sorted({item["entry_time"].date().isoformat() for item in independent})
    overall = metrics([float(item["r_net"]) for item in independent])
    output_dir.mkdir(parents=True, exist_ok=True)
    if independent:
        fieldnames = [key for key in independent[0] if key != "entry_time"]
        with (output_dir / "R64B_CANDIDATES.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows({key: value for key, value in item.items() if key != "entry_time"} for item in independent)
    ready = len(days) >= 5 and len(independent) >= 30
    verdict = {
        "stage": "R64B_FROZEN_MECHANISM_SCREEN",
        "version": VERSION,
        "status": "DEVELOPMENT_SCREEN_READY" if ready else "ACCUMULATING_FROZEN_CANDIDATES",
        "causal_rows": len(rows),
        "raw_candidates": len(candidates),
        "independent_candidates": len(independent),
        "independent_days": len(days),
        "by_family": dict(Counter(item["family"] for item in independent)),
        "by_direction": dict(Counter(item["direction"] for item in independent)),
        "observational_metrics_not_validated": overall,
        "development_minimum_days": 5,
        "development_minimum_clusters": 30,
        "edge_validated": False,
        "real_money_authorized": False,
        "production_changed": False,
    }
    (output_dir / "verdict.json").write_text(
        json.dumps(verdict, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen R64 mechanism candidates.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.output_dir), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
