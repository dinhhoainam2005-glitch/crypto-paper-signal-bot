"""Causal annual symbol-side sleeve council over the frozen V5 engine."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest_breakout_v5 import (
    MAX_HOLD_DAYS,
    MAX_INITIAL_GROSS,
    MAX_SYMBOL_NOTIONAL,
    RISK_PER_TRADE,
    TP_FRACTIONS,
    TP_R_MULTIPLES,
    add_channels,
    breakout_side,
    channel_exit,
)
from backtest_risk_defined import path_metrics, profit_factor, trade_metrics
from backtest_symmetric_v4 import (
    DATA_ROOT,
    STRESS_COST,
    bootstrap_lower,
    load_inputs,
    simulate,
    truncate,
)


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "sleeve_council_v6"
FIRST_OOS_YEAR = 2022
TRAINING_YEARS = 3
MIN_SLEEVE_TRADES = 8
MIN_SLEEVE_PF = 1.20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def run_v5(
    markets: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    start: pd.Timestamp,
    allowed_sleeves: set[tuple[str, str]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    return simulate(
        markets,
        funding,
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
        allowed_sleeves=allowed_sleeves,
    )


def eligible_sleeves(trades: pd.DataFrame) -> tuple[set[tuple[str, str]], pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for (symbol, side), group in trades.groupby(["symbol", "side"]):
        pf = profit_factor(group["net_pnl"])
        mean_r = float(group["realized_r"].mean())
        eligible = len(group) >= MIN_SLEEVE_TRADES and pf >= MIN_SLEEVE_PF and mean_r > 0.0
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "trades": len(group),
                "profit_factor": pf,
                "mean_r": mean_r,
                "eligible": eligible,
            }
        )
    table = pd.DataFrame(rows)
    selected = {
        (str(row.symbol), str(row.side))
        for row in table.loc[table["eligible"]].itertuples(index=False)
    }
    return selected, table


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
    raw_markets, funding = load_inputs(args.data_root)
    markets = add_channels(raw_markets)
    last_date = max(frame.index.max() for frame in markets.values())
    paths: list[pd.DataFrame] = []
    ledgers: list[pd.DataFrame] = []
    selection_rows: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []

    for year in range(FIRST_OOS_YEAR, int(last_date.year) + 1):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = min(pd.Timestamp(f"{year + 1}-01-01", tz="UTC"), last_date + pd.Timedelta(days=1))
        train_start = pd.Timestamp(f"{year - TRAINING_YEARS}-01-01", tz="UTC")
        training_markets, training_funding = truncate(markets, funding, start)
        _, training_trades, _ = run_v5(training_markets, training_funding, train_start)
        entry_times = pd.to_datetime(training_trades["entry_time"], utc=True)
        training_trades = training_trades.loc[entry_times >= train_start]
        selected, table = eligible_sleeves(training_trades)
        table.insert(0, "test_year", year)
        selection_rows.append(table)

        test_markets, test_funding = truncate(markets, funding, end)
        path, trades, diagnostics = run_v5(
            test_markets, test_funding, start, allowed_sleeves=selected
        )
        path = path.loc[(path.index >= start) & (path.index < end)].copy()
        path["fold_year"] = year
        if not trades.empty:
            test_entries = pd.to_datetime(trades["entry_time"], utc=True)
            trades = trades.loc[(test_entries >= start) & (test_entries < end)].copy()
            trades["fold_year"] = year
            ledgers.append(trades)
        paths.append(path)
        fold_rows.append(
            {
                "year": year,
                "selected_sleeves": ",".join(f"{symbol}_{side}" for symbol, side in sorted(selected)),
                **path_metrics(path),
                **trade_metrics(trades),
                **diagnostics,
            }
        )

    combined_path = pd.concat(paths).sort_index()
    combined_trades = pd.concat(ledgers, ignore_index=True) if ledgers else pd.DataFrame()
    folds = pd.DataFrame(fold_rows)
    selections = pd.concat(selection_rows, ignore_index=True)
    overall = {**path_metrics(combined_path), **trade_metrics(combined_trades)}
    recent_start = pd.Timestamp("2025-01-01", tz="UTC")
    entry_times = pd.to_datetime(combined_trades["entry_time"], utc=True)
    recent = {
        **path_metrics(combined_path.loc[combined_path.index >= recent_start]),
        **trade_metrics(combined_trades.loc[entry_times >= recent_start]),
    }
    lower = bootstrap_lower(combined_trades["realized_r"])
    completed = folds.loc[folds["days"] >= 360]
    checks = {
        "oos_trades_ge_80": bool(overall["trades"] >= 80),
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
        "experiment": "CORE4_CLEANROOM_CAUSAL_SLEEVE_COUNCIL_V6",
        "selection_uses_past_only": True,
        "oos_start": combined_path.index.min(),
        "oos_end": combined_path.index.max(),
        "bootstrap_mean_r_lower_5pct": lower,
        "overall": overall,
        "recent_2025_plus": recent,
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "decision": "SECOND_AUDIT_REQUIRED" if all(checks.values()) else "REJECT_V6",
        "production_changed": False,
    }
    combined_path.to_csv(args.report_root / "oos_daily_path.csv")
    combined_trades.to_csv(args.report_root / "oos_trades.csv", index=False)
    folds.to_csv(args.report_root / "oos_folds.csv", index=False)
    selections.to_csv(args.report_root / "annual_sleeve_evidence.csv", index=False)
    (args.report_root / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, default=str), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, default=str), flush=True)
    print(folds.to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
