"""Exact-path audit of V7 plus a small cross-market-confirmed fast sleeve.

This is a frozen research challenger. It cannot change the deployed V7 bot.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_beta_regime_v7 import enrich
from audit_execution_timing_v7 import exact_simulate, load_exact_inputs
from backtest_risk_defined import path_metrics, trade_metrics
from backtest_symmetric_v4 import DATA_ROOT, STRESS_COST


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "fast_confirmed_v16"
CORE_WEIGHT = 0.70
FAST_WEIGHT = 1.0 - CORE_WEIGHT
PERIODS = {
    "DEVELOPMENT_2021_2023": (
        pd.Timestamp("2021-01-01", tz="UTC"),
        pd.Timestamp("2024-01-01", tz="UTC"),
    ),
    "VALIDATION_2024": (
        pd.Timestamp("2024-01-01", tz="UTC"),
        pd.Timestamp("2025-01-01", tz="UTC"),
    ),
    "LATER_2025_2026": (
        pd.Timestamp("2025-01-01", tz="UTC"),
        pd.Timestamp("2026-09-01", tz="UTC"),
    ),
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def scale_trades(trades: pd.DataFrame, sleeve: str, weight: float) -> pd.DataFrame:
    result = trades.copy()
    result["sleeve"] = sleeve
    if not result.empty:
        for column in ("net_pnl", "initial_notional", "initial_risk_cash"):
            result[column] = result[column] * weight
    return result


def combine_paths(
    core_path: pd.DataFrame,
    fast_path: pd.DataFrame,
    core_weight: float,
) -> pd.DataFrame:
    index = core_path.index.union(fast_path.index).sort_values()
    core_equity = core_path["equity"].reindex(index).ffill().fillna(1.0)
    fast_equity = fast_path["equity"].reindex(index).ffill().fillna(1.0)
    equity = core_weight * core_equity + (1.0 - core_weight) * fast_equity
    previous = equity.shift(1).fillna(1.0)
    return pd.DataFrame(
        {"equity": equity, "return": equity / previous - 1.0}, index=index
    )


def summarize(
    path: pd.DataFrame,
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, float | int]:
    metrics = {**path_metrics(path), **trade_metrics(trades)}
    weeks = (end - start).total_seconds() / (7.0 * 86_400.0)
    metrics["trades_per_week"] = metrics["trades"] / weeks
    return metrics


def run_period(
    core_daily: dict[str, pd.DataFrame],
    fast_daily: dict[str, pd.DataFrame],
    hourly: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    core_path, core_trades, _ = exact_simulate(
        core_daily, hourly, funding, start, end, cost_rate=STRESS_COST
    )
    fast_path, fast_trades, _ = exact_simulate(
        fast_daily,
        hourly,
        funding,
        start,
        end,
        cost_rate=STRESS_COST,
        min_concurrent_signals=2,
    )
    core_path = core_path.loc[(core_path.index >= start) & (core_path.index < end)]
    fast_path = fast_path.loc[(fast_path.index >= start) & (fast_path.index < end)]
    core_entries = pd.to_datetime(core_trades["entry_time"], utc=True)
    fast_entries = pd.to_datetime(fast_trades["entry_time"], utc=True)
    core_trades = core_trades.loc[(core_entries >= start) & (core_entries < end)].copy()
    fast_trades = fast_trades.loc[(fast_entries >= start) & (fast_entries < end)].copy()

    combined_path = combine_paths(core_path, fast_path, CORE_WEIGHT)
    combined_trades = pd.concat(
        [
            scale_trades(core_trades, "V7_CORE_55_20", CORE_WEIGHT),
            scale_trades(fast_trades, "FAST_CONFIRMED_20_10", FAST_WEIGHT),
        ],
        ignore_index=True,
    )
    return {
        "baseline_v7": summarize(core_path, core_trades, start, end),
        "fast_confirmed": summarize(fast_path, fast_trades, start, end),
        "candidate_v16": summarize(combined_path, combined_trades, start, end),
        "candidate_path": combined_path,
        "candidate_trades": combined_trades,
        "fast_trades": fast_trades,
    }


def improvement_checks(result: dict[str, Any]) -> dict[str, bool]:
    base = result["baseline_v7"]
    test = result["candidate_v16"]
    fast = result["fast_confirmed"]
    return {
        "frequency_improves_25pct": test["trades_per_week"] >= 1.25 * base["trades_per_week"],
        "win_not_worse": test["win_rate"] >= base["win_rate"],
        "pf_not_worse": test["profit_factor"] >= base["profit_factor"],
        "sharpe_not_worse": test["sharpe"] >= base["sharpe"],
        "drawdown_not_worse": test["max_drawdown"] >= base["max_drawdown"],
        "return_not_worse": test["total_return"] >= base["total_return"],
        "fast_pf_ge_1p30": fast["profit_factor"] >= 1.30,
        "fast_mean_r_positive": fast["mean_r"] > 0.0,
    }


def public(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if not isinstance(value, pd.DataFrame)}


def main() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    core_daily, hourly, funding = load_exact_inputs(DATA_ROOT)
    raw_daily = {
        symbol: frame[["open", "high", "low", "close", "atr20"]].copy()
        for symbol, frame in core_daily.items()
    }
    fast_daily = enrich(raw_daily, entry_channel=20, exit_channel=10, sma_days=200)

    verdict: dict[str, Any] = {
        "experiment": "CORE4_V16_FAST_CONFIRMED_DAILY",
        "configuration": {
            "core": "V7 55/20",
            "fast": "20/10 with at least two concurrent CORE4 signals",
            "weights": {"core": CORE_WEIGHT, "fast": FAST_WEIGHT},
            "cost_each_way": STRESS_COST,
        },
        "periods": {},
        "production_changed": False,
    }
    all_pass = True
    for name, (start, end) in PERIODS.items():
        print(f"V16 {name}", flush=True)
        result = run_period(core_daily, fast_daily, hourly, funding, start, end)
        checks = improvement_checks(result)
        verdict["periods"][name] = {**public(result), "checks": checks}
        all_pass = all_pass and all(checks.values())
        result["candidate_path"].to_csv(REPORT_ROOT / f"{name.lower()}_path.csv")
        result["candidate_trades"].to_csv(
            REPORT_ROOT / f"{name.lower()}_trades.csv", index=False
        )
        result["fast_trades"].to_csv(
            REPORT_ROOT / f"{name.lower()}_fast_trades.csv", index=False
        )

    verdict["decision"] = (
        "PAPER_SHADOW_CANDIDATE" if all_pass else "REJECT_V16_NO_DEPLOY"
    )
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
