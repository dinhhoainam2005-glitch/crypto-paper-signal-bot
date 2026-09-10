"""Frozen audit of ATR-based exits for the exact R26A production signals.

The entry set is the R31A production-parity replay. Exit parameters are selected
with development (2020-2023) plus validation (2024) data only. The 2025 through
July 2026 window remains sealed until one global overlay has been locked.

Barrier simulation is deliberately conservative: when both TP and SL trade in
the same candle, the stop is assumed to have filled first. Every trade enters at
NEXT_OPEN and uses only ATR information available at the preceding candle close.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS = ROOT / ".codex_deps"
for path in (LOCAL_DEPS, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import r31a_r26a_production_parity_backtest as r31a  # noqa: E402


INPUT_TRADES = (
    ROOT
    / "r31a_output"
    / "reports"
    / "R31A_R26A_production_parity"
    / "R31A_ACCEPTED_TRADES.csv"
)
OUTPUT_DIR = ROOT / "r32a_output" / "reports" / "R32A_ATR_execution_overlay"

CALIBRATION_START = pd.Timestamp("2020-09-01", tz="UTC")
DEVELOPMENT_END = pd.Timestamp("2024-01-01", tz="UTC")
FROZEN_START = pd.Timestamp("2025-01-01", tz="UTC")
FROZEN_END = pd.Timestamp("2026-08-01", tz="UTC")
BASE_COST_BPS = 12.0
STRESS_COST_BPS = 20.0
RISK_FRACTION = 0.25

TP_GRID = (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
SL_GRID = (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
MAX_HOLD_FRACTIONS = (0.5, 0.75, 1.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=r31a.WAREHOUSE)
    parser.add_argument("--input-trades", type=Path, default=INPUT_TRADES)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def load_trades(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        parse_dates=["signal_time", "entry_time", "exit_time"],
    )
    for column in ("signal_time", "entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame = frame.loc[frame["accepted"].astype(str).str.lower().eq("true")].copy()
    return frame.sort_values(["entry_time", "group_order"]).reset_index(drop=True)


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["px_close"].shift(1)
    return pd.concat(
        [
            frame["px_high"] - frame["px_low"],
            (frame["px_high"] - previous_close).abs(),
            (frame["px_low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def load_bars(root: Path, trades: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    panels: dict[tuple[str, str], pd.DataFrame] = {}
    for asset, timeframe in sorted(set(zip(trades["asset"], trades["timeframe"]))):
        frame = r31a.load_cell(root, str(asset), str(timeframe)).copy()
        frame["atr14"] = true_range(frame).rolling(14, min_periods=14).mean()
        panels[(str(asset), str(timeframe))] = frame
    return panels


def enrich_paths(
    trades: pd.DataFrame,
    panels: dict[tuple[str, str], pd.DataFrame],
) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    for index, trade in trades.iterrows():
        panel = panels[(str(trade["asset"]), str(trade["timeframe"]))]
        signal_bar = trade["signal_time"]
        if signal_bar not in panel.index or trade["entry_time"] not in panel.index:
            continue
        atr = float(panel.at[signal_bar, "atr14"])
        entry = float(trade["entry_price"])
        if not math.isfinite(atr) or atr <= 0.0 or entry <= 0.0:
            continue
        path = panel.loc[
            (panel.index >= trade["entry_time"]) & (panel.index < trade["exit_time"]),
            ["px_high", "px_low", "px_close"],
        ].copy()
        if path.empty:
            continue
        paths.append(
            {
                "trade_index": int(index),
                "atr": atr,
                "entry": entry,
                "path_times": path.index.to_numpy(),
                "high": path["px_high"].to_numpy(dtype=float),
                "low": path["px_low"].to_numpy(dtype=float),
            }
        )
    return paths


def overlay_returns(
    trades: pd.DataFrame,
    paths: list[dict[str, Any]],
    panels: dict[tuple[str, str], pd.DataFrame],
    tp_atr: float,
    sl_atr: float,
    max_hold_fraction: float,
) -> pd.DataFrame:
    output = trades.copy()
    output["overlay_gross_return"] = np.nan
    output["overlay_exit_time"] = pd.Series(
        pd.NaT, index=output.index, dtype="datetime64[ns, UTC]"
    )
    output["overlay_exit_reason"] = "MISSING_PATH"
    output["tp_atr"] = tp_atr
    output["sl_atr"] = sl_atr
    output["max_hold_fraction"] = max_hold_fraction

    for item in paths:
        index = item["trade_index"]
        trade = trades.iloc[index]
        entry = item["entry"]
        side = str(trade["side"])
        sign = 1.0 if side == "LONG" else -1.0
        tp_price = entry + sign * tp_atr * item["atr"]
        sl_price = entry - sign * sl_atr * item["atr"]
        original_hold = int(trade["hold_bars"])
        max_bars = max(1, min(original_hold, int(math.ceil(original_hold * max_hold_fraction))))
        highs = item["high"][:max_bars]
        lows = item["low"][:max_bars]
        times = item["path_times"][:max_bars]

        exit_price: float | None = None
        exit_time: pd.Timestamp | None = None
        reason = "TIME"
        for bar_index, (high, low) in enumerate(zip(highs, lows)):
            if side == "LONG":
                hit_stop = low <= sl_price
                hit_target = high >= tp_price
            else:
                hit_stop = high >= sl_price
                hit_target = low <= tp_price
            # OHLC bars do not reveal intrabar ordering, so collisions debit SL.
            if hit_stop:
                exit_price = sl_price
                exit_time = pd.Timestamp(times[bar_index])
                reason = "SL" if not hit_target else "SL_COLLISION"
                break
            if hit_target:
                exit_price = tp_price
                exit_time = pd.Timestamp(times[bar_index])
                reason = "TP"
                break

        if exit_price is None:
            timeframe = str(trade["timeframe"])
            scheduled = trade["entry_time"] + r31a.TF_DELTA[timeframe] * max_bars
            panel = panels[(str(trade["asset"]), timeframe)]
            if scheduled not in panel.index:
                continue
            exit_price = float(panel.at[scheduled, "px_open"])
            exit_time = scheduled

        output.at[index, "overlay_gross_return"] = sign * (exit_price / entry - 1.0)
        output.at[index, "overlay_exit_time"] = exit_time
        output.at[index, "overlay_exit_reason"] = reason

    return output.dropna(subset=["overlay_gross_return"]).copy()


def metrics(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, cost_bps: float) -> dict[str, Any]:
    subset = frame.loc[frame["entry_time"].ge(start) & frame["entry_time"].lt(end)].copy()
    returns = RISK_FRACTION * (
        subset["overlay_gross_return"].to_numpy(dtype=float) - cost_bps / 10000.0
    )
    days = max((end - start).total_seconds() / 86400.0, 1.0)
    wins = float(returns[returns > 0.0].sum())
    losses = float(returns[returns < 0.0].sum())
    profit_factor = wins / abs(losses) if losses < 0.0 else (math.inf if wins > 0.0 else 0.0)
    trades_per_week = float(len(subset) / (days / 7.0))
    std = float(np.std(returns)) if len(returns) else 0.0
    sharpe = (
        float(np.mean(returns)) / std * math.sqrt(max(trades_per_week * 52.0, 1.0))
        if std > 0.0
        else 0.0
    )
    return {
        "trades": int(len(subset)),
        "trades_per_week": trades_per_week,
        "win_rate": float(np.mean(returns > 0.0)) if len(returns) else 0.0,
        "profit_factor": float(profit_factor),
        "sharpe": float(sharpe),
        "max_drawdown_pct": float(r31a.max_drawdown_pct(returns.tolist())),
        "avg_net_bps": float(np.mean(returns) * 10000.0) if len(returns) else 0.0,
        "exit_tp": int(subset["overlay_exit_reason"].eq("TP").sum()),
        "exit_sl": int(subset["overlay_exit_reason"].isin(["SL", "SL_COLLISION"]).sum()),
        "exit_time": int(subset["overlay_exit_reason"].eq("TIME").sum()),
        "trades_per_week_exact": float(len(subset) / (days / 7.0)),
        "cost_bps": cost_bps,
    }


def score_candidate(
    development: dict[str, Any],
    validation: dict[str, Any],
    validation_stress: dict[str, Any],
) -> tuple[float, bool]:
    stable_pf = min(
        float(development["profit_factor"]),
        float(validation["profit_factor"]),
        float(validation_stress["profit_factor"]),
    )
    stable_win = min(float(development["win_rate"]), float(validation["win_rate"]))
    passed = bool(
        development["trades"] >= 500
        and validation["trades"] >= 250
        and stable_pf >= 1.20
        and validation_stress["profit_factor"] >= 1.10
        and development["max_drawdown_pct"] >= -30.0
        and validation["max_drawdown_pct"] >= -15.0
    )
    score = (
        min(stable_pf, 3.0) * 45.0
        + stable_win * 30.0
        + min(float(validation["sharpe"]), 5.0) * 6.0
        + float(validation_stress["avg_net_bps"]) / 3.0
        + float(validation["max_drawdown_pct"]) / 5.0
    )
    return float(score), passed


def baseline_frame(trades: pd.DataFrame) -> pd.DataFrame:
    frame = trades.copy()
    frame["overlay_gross_return"] = frame["gross_return"]
    frame["overlay_exit_time"] = frame["exit_time"]
    frame["overlay_exit_reason"] = "ORIGINAL_TIME"
    return frame


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    display = frame.loc[:, columns].copy()
    for column in display.columns:
        if pd.api.types.is_float_dtype(display[column]):
            display[column] = display[column].map(
                lambda value: f"{value:.4f}" if pd.notna(value) else ""
            )
    header = "| " + " | ".join(display.columns) + " |"
    separator = "| " + " | ".join(["---"] * len(display.columns)) + " |"
    rows = [
        "| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |"
        for row in display.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows])


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trades = load_trades(args.input_trades)
    panels = load_bars(args.warehouse, trades)
    paths = enrich_paths(trades, panels)
    if len(paths) != len(trades):
        raise RuntimeError(f"Only {len(paths)}/{len(trades)} trades have valid paths")

    calibration_rows: list[dict[str, Any]] = []
    locked: tuple[float, float, float] | None = None
    locked_score = -math.inf
    locked_pass = False
    for tp_atr in TP_GRID:
        for sl_atr in SL_GRID:
            for hold_fraction in MAX_HOLD_FRACTIONS:
                overlaid = overlay_returns(
                    trades, paths, panels, tp_atr, sl_atr, hold_fraction
                )
                development = metrics(
                    overlaid, CALIBRATION_START, DEVELOPMENT_END, BASE_COST_BPS
                )
                validation = metrics(
                    overlaid, DEVELOPMENT_END, FROZEN_START, BASE_COST_BPS
                )
                validation_stress = metrics(
                    overlaid, DEVELOPMENT_END, FROZEN_START, STRESS_COST_BPS
                )
                score, passed = score_candidate(development, validation, validation_stress)
                row = {
                    "tp_atr": tp_atr,
                    "sl_atr": sl_atr,
                    "max_hold_fraction": hold_fraction,
                    "selection_score": score,
                    "selection_pass": passed,
                }
                for prefix, block in (
                    ("development", development),
                    ("validation", validation),
                    ("validation_stress", validation_stress),
                ):
                    for key, value in block.items():
                        row[f"{prefix}_{key}"] = value
                calibration_rows.append(row)
                rank = (passed, score)
                current = (locked_pass, locked_score)
                if rank > current:
                    locked = (tp_atr, sl_atr, hold_fraction)
                    locked_score = score
                    locked_pass = passed

    if locked is None:
        raise RuntimeError("No execution overlay candidate was evaluated")
    tp_atr, sl_atr, hold_fraction = locked
    selected = overlay_returns(
        trades, paths, panels, tp_atr, sl_atr, hold_fraction
    )
    baseline = baseline_frame(trades)

    periods = {
        "development": (CALIBRATION_START, DEVELOPMENT_END),
        "validation_2024": (DEVELOPMENT_END, FROZEN_START),
        "frozen_2025_2026m07": (FROZEN_START, FROZEN_END),
        "recent_2026m01_m07": (pd.Timestamp("2026-01-01", tz="UTC"), FROZEN_END),
    }
    comparison_rows: list[dict[str, Any]] = []
    for period, (start, end) in periods.items():
        for label, frame in (("R31A_FIXED_HOLD", baseline), ("R32A_ATR_OVERLAY", selected)):
            for cost in (BASE_COST_BPS, STRESS_COST_BPS):
                comparison_rows.append(
                    {"period": period, "model": label, **metrics(frame, start, end, cost)}
                )
    comparison = pd.DataFrame(comparison_rows)

    frozen = selected.loc[
        selected["entry_time"].ge(FROZEN_START) & selected["entry_time"].lt(FROZEN_END)
    ].copy()
    cell_rows: list[dict[str, Any]] = []
    for keys, group in frozen.groupby(["asset", "timeframe", "side"], sort=True):
        block = metrics(group, FROZEN_START, FROZEN_END, BASE_COST_BPS)
        cell_rows.append(
            {"asset": keys[0], "timeframe": keys[1], "side": keys[2], **block}
        )
    cells = pd.DataFrame(cell_rows)

    frozen_base = comparison.loc[
        comparison["period"].eq("frozen_2025_2026m07")
        & comparison["model"].eq("R31A_FIXED_HOLD")
        & comparison["cost_bps"].eq(BASE_COST_BPS)
    ].iloc[0]
    frozen_overlay = comparison.loc[
        comparison["period"].eq("frozen_2025_2026m07")
        & comparison["model"].eq("R32A_ATR_OVERLAY")
        & comparison["cost_bps"].eq(BASE_COST_BPS)
    ].iloc[0]
    frozen_stress = comparison.loc[
        comparison["period"].eq("frozen_2025_2026m07")
        & comparison["model"].eq("R32A_ATR_OVERLAY")
        & comparison["cost_bps"].eq(STRESS_COST_BPS)
    ].iloc[0]

    improves = {
        "win_rate": bool(frozen_overlay["win_rate"] > frozen_base["win_rate"]),
        "profit_factor": bool(
            frozen_overlay["profit_factor"] > frozen_base["profit_factor"]
        ),
        "sharpe": bool(frozen_overlay["sharpe"] > frozen_base["sharpe"]),
        "max_drawdown": bool(
            frozen_overlay["max_drawdown_pct"] > frozen_base["max_drawdown_pct"]
        ),
    }
    promotion = bool(
        locked_pass
        and frozen_overlay["trades"] >= 150
        and frozen_overlay["win_rate"] >= 0.60
        and frozen_overlay["profit_factor"] >= 1.50
        and frozen_stress["profit_factor"] >= 1.20
        and frozen_overlay["sharpe"] >= 2.0
        and frozen_overlay["max_drawdown_pct"] >= -15.0
        and sum(improves.values()) >= 3
    )
    verdict = {
        "audit_id": "R32A_ATR_EXECUTION_OVERLAY",
        "entry_registry": "R31A_EXACT_R26A_PRODUCTION_PARITY",
        "path_count": len(paths),
        "calibration_candidates": len(calibration_rows),
        "locked_parameters": {
            "tp_atr": tp_atr,
            "sl_atr": sl_atr,
            "max_hold_fraction": hold_fraction,
            "same_bar_collision": "STOP_FIRST",
        },
        "calibration_gate_pass": locked_pass,
        "frozen_improvements": improves,
        "frozen_metrics_12bps": frozen_overlay.to_dict(),
        "frozen_metrics_20bps": frozen_stress.to_dict(),
        "promotion_gate_pass": promotion,
        "production_change_allowed": promotion,
        "decision": "PROMOTE_EXECUTION_OVERLAY" if promotion else "REJECT_EXECUTION_OVERLAY",
    }

    pd.DataFrame(calibration_rows).sort_values(
        ["selection_pass", "selection_score"], ascending=[False, False]
    ).to_csv(args.output_dir / "R32A_CALIBRATION.csv", index=False)
    comparison.to_csv(args.output_dir / "R32A_COMPARISON.csv", index=False)
    cells.to_csv(args.output_dir / "R32A_FROZEN_CELLS.csv", index=False)
    selected.to_csv(args.output_dir / "R32A_OVERLAY_TRADES.csv", index=False)
    (args.output_dir / "R32A_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )

    report_columns = [
        "period",
        "model",
        "cost_bps",
        "trades",
        "trades_per_week",
        "win_rate",
        "profit_factor",
        "sharpe",
        "max_drawdown_pct",
        "avg_net_bps",
    ]
    cell_columns = [
        "asset",
        "timeframe",
        "side",
        "trades",
        "trades_per_week",
        "win_rate",
        "profit_factor",
        "sharpe",
        "max_drawdown_pct",
    ]
    report = [
        "# R32A ATR Execution Overlay",
        "",
        "## Decision",
        "",
        f"**{verdict['decision']}**",
        "",
        f"Locked on pre-2025 data: TP `{tp_atr:.2f} ATR`, SL `{sl_atr:.2f} ATR`, "
        f"maximum hold `{hold_fraction:.2f}` of the original horizon.",
        "",
        "The 2025 through July 2026 window was opened only after the overlay was locked. "
        "Same-candle TP/SL collisions are charged as stop losses.",
        "",
        "## Baseline versus overlay",
        "",
        markdown_table(comparison, report_columns),
        "",
        "## Frozen cells",
        "",
        markdown_table(cells, cell_columns),
        "",
    ]
    (args.output_dir / "R32A_REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
