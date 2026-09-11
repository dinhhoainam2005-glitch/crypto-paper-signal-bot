"""Finite annual walk-forward tournament for the clean-room CORE4 project."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest_allocator import DATA_ROOT
from backtest_risk_defined import (
    REPORT_ROOT as V2_REPORT_ROOT,
    STRESS_COST,
    load_markets,
    path_metrics,
    profit_factor,
    simulate,
    trade_metrics,
)


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "walkforward_v3"
CANDIDATES = {"ALL": None, "TOP1": 1, "TOP2": 2}
FIRST_TEST_YEAR = 2020
TRAINING_YEARS = 3
MIN_TRAINING_TRADES = 20
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 5203


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def truncate_markets(
    markets: dict[str, pd.DataFrame], end: pd.Timestamp
) -> dict[str, pd.DataFrame]:
    return {symbol: frame.loc[frame.index < end].copy() for symbol, frame in markets.items()}


def select_training_candidate(
    markets: dict[str, pd.DataFrame],
    training_start: pd.Timestamp,
    training_end: pd.Timestamp,
) -> tuple[str, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    bounded = truncate_markets(markets, training_end)
    for name, limit in CANDIDATES.items():
        path, trades, _, _ = simulate(
            bounded,
            STRESS_COST,
            candidate_limit=limit,
            entry_start=training_start,
        )
        selected_path = path.loc[(path.index >= training_start) & (path.index < training_end)]
        entry_times = pd.to_datetime(trades["entry_time"], utc=True) if not trades.empty else pd.Series(dtype="datetime64[ns, UTC]")
        selected_trades = trades.loc[
            (entry_times >= training_start) & (entry_times < training_end)
        ] if not trades.empty else trades
        rows.append(
            {
                "candidate": name,
                **path_metrics(selected_path),
                **trade_metrics(selected_trades),
            }
        )
    scores = pd.DataFrame(rows)
    eligible = scores.loc[
        scores["trades"].ge(MIN_TRAINING_TRADES) & scores["profit_factor"].gt(1.0)
    ].copy()
    if eligible.empty:
        return "ALL", scores
    eligible = eligible.sort_values(
        ["sharpe", "profit_factor", "candidate"],
        ascending=[False, False, True],
    )
    return str(eligible.iloc[0]["candidate"]), scores


def bootstrap_mean_r_lower_bound(values: pd.Series) -> float:
    clean = values.dropna().to_numpy(dtype=float)
    if len(clean) == 0:
        return 0.0
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    chunk_size = 500
    means: list[np.ndarray] = []
    remaining = BOOTSTRAP_SAMPLES
    while remaining > 0:
        count = min(chunk_size, remaining)
        sample = generator.choice(clean, size=(count, len(clean)), replace=True)
        means.append(sample.mean(axis=1))
        remaining -= count
    return float(np.quantile(np.concatenate(means), 0.05))


def combined_period_metrics(
    path: pd.DataFrame,
    trades: pd.DataFrame,
    start: pd.Timestamp,
) -> dict[str, float | int]:
    selected_path = path.loc[path.index >= start]
    entry_times = pd.to_datetime(trades["entry_time"], utc=True)
    selected_trades = trades.loc[entry_times >= start]
    return {**path_metrics(selected_path), **trade_metrics(selected_trades)}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
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
    markets = load_markets(args.data_root)
    last_date = max(frame.index.max() for frame in markets.values())
    last_year = int(last_date.year)
    fold_paths: list[pd.DataFrame] = []
    fold_trades: list[pd.DataFrame] = []
    selections: list[dict[str, Any]] = []
    training_tables: list[pd.DataFrame] = []

    for test_year in range(FIRST_TEST_YEAR, last_year + 1):
        test_start = pd.Timestamp(f"{test_year}-01-01", tz="UTC")
        requested_test_end = pd.Timestamp(f"{test_year + 1}-01-01", tz="UTC")
        test_end = min(requested_test_end, last_date + pd.Timedelta(days=1))
        training_start = pd.Timestamp(f"{test_year - TRAINING_YEARS}-01-01", tz="UTC")
        selected, scores = select_training_candidate(markets, training_start, test_start)
        scores.insert(0, "test_year", test_year)
        scores["selected"] = scores["candidate"].eq(selected)
        training_tables.append(scores)

        bounded = truncate_markets(markets, test_end)
        path, trades, _, diagnostics = simulate(
            bounded,
            STRESS_COST,
            candidate_limit=CANDIDATES[selected],
            entry_start=test_start,
        )
        path = path.loc[(path.index >= test_start) & (path.index < test_end)].copy()
        path["test_year"] = test_year
        path["selected_candidate"] = selected
        if not trades.empty:
            entry_times = pd.to_datetime(trades["entry_time"], utc=True)
            trades = trades.loc[(entry_times >= test_start) & (entry_times < test_end)].copy()
            trades["test_year"] = test_year
            trades["selected_candidate"] = selected
            fold_trades.append(trades)
        fold_paths.append(path)
        selections.append(
            {
                "test_year": test_year,
                "training_start": training_start,
                "training_end": test_start,
                "test_end": test_end,
                "selected_candidate": selected,
                "test_trades": len(trades),
                "minimum_cash": diagnostics["minimum_cash"],
                "maximum_initial_gross": diagnostics["maximum_initial_gross"],
            }
        )

    combined_path = pd.concat(fold_paths).sort_index()
    combined_trades = pd.concat(fold_trades, ignore_index=True) if fold_trades else pd.DataFrame()
    selection_table = pd.DataFrame(selections)
    training_table = pd.concat(training_tables, ignore_index=True)
    overall = {**path_metrics(combined_path), **trade_metrics(combined_trades)}
    evaluation = combined_period_metrics(
        combined_path, combined_trades, pd.Timestamp("2024-01-01", tz="UTC")
    )
    recent = combined_period_metrics(
        combined_path, combined_trades, pd.Timestamp("2025-01-01", tz="UTC")
    )
    year_rows: list[dict[str, Any]] = []
    entry_years = pd.to_datetime(combined_trades["entry_time"], utc=True).dt.year
    for year, year_path in combined_path.groupby(combined_path.index.year):
        year_trades = combined_trades.loc[entry_years.eq(year)]
        year_rows.append({"year": int(year), **path_metrics(year_path), **trade_metrics(year_trades)})
    years = pd.DataFrame(year_rows)
    bootstrap_lower = bootstrap_mean_r_lower_bound(combined_trades["realized_r"])
    completed_years = years.loc[years["days"] >= 360]
    checks = {
        "oos_trades_ge_120": bool(overall["trades"] >= 120),
        "oos_pf_ge_1p30": bool(overall["profit_factor"] >= 1.30),
        "oos_sharpe_ge_1": bool(overall["sharpe"] >= 1.0),
        "oos_drawdown_le_15pct": bool(overall["max_drawdown"] >= -0.15),
        "positive_completed_years_ge_70pct": bool((completed_years["total_return"] > 0.0).mean() >= 0.70),
        "oos_mean_r_positive": bool(overall["mean_r"] > 0.0),
        "bootstrap_mean_r_lower_5pct_positive": bool(bootstrap_lower > 0.0),
        "evaluation_pf_ge_1p20": bool(evaluation["profit_factor"] >= 1.20),
        "evaluation_sharpe_ge_1": bool(evaluation["sharpe"] >= 1.0),
        "recent_return_positive": bool(recent["total_return"] > 0.0),
    }
    verdict = {
        "experiment": "CORE4_CLEANROOM_WALKFORWARD_V3",
        "candidate_count": len(CANDIDATES),
        "candidate_names": list(CANDIDATES),
        "selection_uses_past_only": True,
        "oos_start": combined_path.index.min(),
        "oos_end": combined_path.index.max(),
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "bootstrap_mean_r_lower_5pct": bootstrap_lower,
        "overall": overall,
        "evaluation_2024_plus": evaluation,
        "recent_2025_plus": recent,
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "decision": "PAPER_OBSERVATION_ALLOWED" if all(checks.values()) else "REJECT_V3",
        "production_changed": False,
    }
    combined_path.to_csv(args.report_root / "oos_daily_path.csv")
    combined_trades.to_csv(args.report_root / "oos_trades.csv", index=False)
    selection_table.to_csv(args.report_root / "annual_selections.csv", index=False)
    training_table.to_csv(args.report_root / "training_scores.csv", index=False)
    years.to_csv(args.report_root / "oos_years.csv", index=False)
    (args.report_root / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, default=str),
        encoding="utf-8",
    )
    lines = [
        "# CORE4 Walk-Forward V3",
        "",
        f"**{verdict['decision']}**",
        "",
        "Three frozen candidates. Annual selection uses only the preceding three calendar years.",
        "All reported paths and trades are out of sample with 20 bps one-way stress cost.",
        "",
        "| Scope | Trades | Win | PF | Mean R | Return | CAGR | Sharpe | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, row in (("OOS full", overall), ("2024+", evaluation), ("2025+", recent)):
        lines.append(
            f"| {label} | {row['trades']} | {row['win_rate']:.1%} | {row['profit_factor']:.3f} | "
            f"{row['mean_r']:.3f} | {row['total_return']:.2%} | {row['cagr']:.2%} | "
            f"{row['sharpe']:.3f} | {row['max_drawdown']:.2%} |"
        )
    lines.extend(
        [
            "",
            f"Bootstrap 5% lower bound of mean R: {bootstrap_lower:.3f}.",
            f"Gate: {sum(checks.values())}/{len(checks)} checks passed.",
            "No Telegram, deployment, paper alert, or live order was enabled.",
        ]
    )
    (args.report_root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, default=str), flush=True)
    print(selection_table.to_string(index=False), flush=True)
    print(years.to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
