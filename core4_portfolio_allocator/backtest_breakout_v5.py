"""Daily Donchian breakout OOS audit using the verified V4 futures engine."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest_risk_defined import path_metrics, trade_metrics
from backtest_symmetric_v4 import (
    BOOTSTRAP_SAMPLES,
    DATA_ROOT,
    FIRST_OOS_YEAR,
    STRESS_COST,
    bootstrap_lower,
    load_inputs,
    simulate,
    truncate,
)


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "breakout_v5"
ENTRY_CHANNEL = 55
EXIT_CHANNEL = 20
TP_R_MULTIPLES = (1.0, 2.0, 4.0)
TP_FRACTIONS = (0.20, 0.30, 0.50)
MAX_HOLD_DAYS = 90
RISK_PER_TRADE = 0.0035
MAX_SYMBOL_NOTIONAL = 0.20
MAX_INITIAL_GROSS = 0.60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def add_channels(markets: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    for symbol, source in markets.items():
        frame = source.copy()
        frame["entry_high"] = frame["high"].shift(1).rolling(ENTRY_CHANNEL).max()
        frame["entry_low"] = frame["low"].shift(1).rolling(ENTRY_CHANNEL).min()
        frame["exit_high"] = frame["high"].shift(1).rolling(EXIT_CHANNEL).max()
        frame["exit_low"] = frame["low"].shift(1).rolling(EXIT_CHANNEL).min()
        output[symbol] = frame
    return output


def breakout_side(
    markets: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> str | None:
    frame = markets[symbol]
    if date not in frame.index:
        return None
    row = frame.loc[date]
    if row[["close", "entry_high", "entry_low"]].isna().any():
        return None
    if row["close"] > row["entry_high"]:
        return "LONG"
    if row["close"] < row["entry_low"]:
        return "SHORT"
    return None


def channel_exit(
    markets: dict[str, pd.DataFrame],
    symbol: str,
    date: pd.Timestamp,
    side: str,
) -> bool:
    frame = markets[symbol]
    if date not in frame.index:
        return False
    row = frame.loc[date]
    if side == "LONG":
        return bool(pd.notna(row["exit_low"]) and row["close"] < row["exit_low"])
    return bool(pd.notna(row["exit_high"]) and row["close"] > row["exit_high"])


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def main() -> int:
    args = parse_args()
    args.report_root.mkdir(parents=True, exist_ok=True)
    base_markets, funding = load_inputs(args.data_root)
    markets = add_channels(base_markets)
    last_date = max(frame.index.max() for frame in markets.values())
    paths: list[pd.DataFrame] = []
    ledgers: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    for year in range(FIRST_OOS_YEAR, int(last_date.year) + 1):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = min(pd.Timestamp(f"{year + 1}-01-01", tz="UTC"), last_date + pd.Timedelta(days=1))
        bounded_markets, bounded_funding = truncate(markets, funding, end)
        path, trades, diagnostics = simulate(
            bounded_markets,
            bounded_funding,
            STRESS_COST,
            start,
            entry_side_function=breakout_side,
            exit_function=channel_exit,
            entry_weekday=None,
            review_weekday=None,
            tp_r_multiples=TP_R_MULTIPLES,
            tp_fractions=TP_FRACTIONS,
            max_hold_days=MAX_HOLD_DAYS,
            risk_per_trade=RISK_PER_TRADE,
            max_symbol_notional=MAX_SYMBOL_NOTIONAL,
            max_initial_gross=MAX_INITIAL_GROSS,
        )
        path = path.loc[(path.index >= start) & (path.index < end)].copy()
        path["fold_year"] = year
        if not trades.empty:
            entry_times = pd.to_datetime(trades["entry_time"], utc=True)
            trades = trades.loc[(entry_times >= start) & (entry_times < end)].copy()
            trades["fold_year"] = year
            ledgers.append(trades)
        paths.append(path)
        fold_rows.append(
            {"year": year, **path_metrics(path), **trade_metrics(trades), **diagnostics}
        )
    combined_path = pd.concat(paths).sort_index()
    combined_trades = pd.concat(ledgers, ignore_index=True)
    folds = pd.DataFrame(fold_rows)
    overall = {**path_metrics(combined_path), **trade_metrics(combined_trades)}
    entry_times = pd.to_datetime(combined_trades["entry_time"], utc=True)
    recent_start = pd.Timestamp("2025-01-01", tz="UTC")
    recent_path = combined_path.loc[combined_path.index >= recent_start]
    recent_trades = combined_trades.loc[entry_times >= recent_start]
    recent = {**path_metrics(recent_path), **trade_metrics(recent_trades)}
    lower = bootstrap_lower(combined_trades["realized_r"])
    completed = folds.loc[folds["days"] >= 360]
    checks = {
        "oos_trades_ge_100": bool(overall["trades"] >= 100),
        "oos_pf_ge_1p30": bool(overall["profit_factor"] >= 1.30),
        "oos_sharpe_ge_1": bool(overall["sharpe"] >= 1.0),
        "oos_drawdown_le_15pct": bool(overall["max_drawdown"] >= -0.15),
        "positive_completed_years_ge_70pct": bool((completed["total_return"] > 0.0).mean() >= 0.70),
        "bootstrap_lower_mean_r_positive": bool(lower > 0.0),
        "recent_pf_ge_1p20": bool(recent["profit_factor"] >= 1.20),
        "recent_sharpe_ge_1": bool(recent["sharpe"] >= 1.0),
        "recent_return_positive": bool(recent["total_return"] > 0.0),
        "funding_included": bool(combined_trades["funding_pnl"].abs().sum() > 0.0),
        "all_reconciled": bool(folds["reconciliation_error"].max() < 1e-10),
        "gross_cap_respected": bool(folds["maximum_initial_gross"].max() <= MAX_INITIAL_GROSS + 1e-9),
    }
    verdict = {
        "experiment": "CORE4_CLEANROOM_DONCHIAN_BREAKOUT_V5",
        "parameter_search_count": 0,
        "oos_start": combined_path.index.min(),
        "oos_end": combined_path.index.max(),
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "bootstrap_mean_r_lower_5pct": lower,
        "overall": overall,
        "recent_2025_plus": recent,
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "decision": "SECOND_AUDIT_REQUIRED" if all(checks.values()) else "REJECT_V5",
        "production_changed": False,
    }
    combined_path.to_csv(args.report_root / "oos_daily_path.csv")
    combined_trades.to_csv(args.report_root / "oos_trades.csv", index=False)
    folds.to_csv(args.report_root / "oos_folds.csv", index=False)
    (args.report_root / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, default=str), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, default=str), flush=True)
    print(folds.to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
