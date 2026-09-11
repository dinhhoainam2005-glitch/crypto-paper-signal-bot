"""Hourly execution-path and event-time funding audit for frozen CORE4 V7."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_beta_regime_v7 import (
    audit_exit,
    audit_side,
    enrich,
    hac_one_sided_p,
    ledger_is_valid,
    moving_block_lower,
)
from backtest_allocator import SYMBOLS
from backtest_breakout_v5 import (
    MAX_HOLD_DAYS,
    MAX_INITIAL_GROSS,
    MAX_SYMBOL_NOTIONAL,
    RISK_PER_TRADE,
    TP_FRACTIONS,
    TP_R_MULTIPLES,
)
from backtest_risk_defined import path_metrics, trade_metrics
from backtest_symmetric_v4 import (
    DATA_ROOT,
    FIRST_OOS_YEAR,
    STRESS_COST,
    FuturesPosition,
    build_plan,
    exit_quantity,
    process_intraday,
    process_open,
    trade_record,
)
from futures_data import (
    load_funding_events,
    load_futures_daily,
    load_futures_hourly,
    load_futures_klines,
    load_futures_mark_price_hourly,
)


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "beta_regime_v7_execution_audit"
SECOND_AUDIT = ROOT / "reports" / "beta_regime_v7_second_audit" / "verdict.json"
HOURLY_MANIFEST = ROOT / "reports" / "futures_hourly_data_manifest.csv"
GAP_REPAIR_MANIFEST = ROOT / "reports" / "sol_hourly_gap_repair_manifest.csv"
RECENT_START = pd.Timestamp("2025-01-01", tz="UTC")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def load_exact_inputs(
    data_root: Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.Series]]:
    daily: dict[str, pd.DataFrame] = {}
    hourly: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for symbol in SYMBOLS:
        day = load_futures_daily(symbol, data_root)[["open", "high", "low", "close"]].copy()
        true_range = pd.concat(
            [
                day["high"] - day["low"],
                (day["high"] - day["close"].shift(1)).abs(),
                (day["low"] - day["close"].shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        day["atr20"] = true_range.rolling(20, min_periods=20).mean()
        daily[symbol] = day
        hour = load_futures_hourly(symbol, data_root)[["open", "high", "low", "close"]].copy()
        if symbol == "SOLUSDT":
            fine = load_futures_klines(symbol, "5m", data_root)[["open", "high", "low", "close"]]
            repaired = fine.resample("1h").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}
            ).dropna()
            mark = load_futures_mark_price_hourly(symbol, data_root)[
                ["open", "high", "low", "close"]
            ]
            hour = pd.concat([hour, repaired, mark])
            hour = hour.loc[~hour.index.duplicated(keep="first")].sort_index()
        hourly[symbol] = hour
        events = load_funding_events(symbol, data_root)
        event_hour = events["event_time"].dt.floor("h")
        funding[symbol] = events.groupby(event_hour)["funding_rate"].sum().sort_index()
    return enrich(daily), hourly, funding


def hourly_data_quality(
    daily: dict[str, pd.DataFrame], hourly: dict[str, pd.DataFrame]
) -> dict[str, dict[str, float | bool]]:
    quality: dict[str, dict[str, float | bool]] = {}
    for symbol in SYMBOLS:
        source = hourly[symbol]
        aggregated = source.resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}
        )
        common = daily[symbol].index.intersection(aggregated.index)
        official = daily[symbol].loc[common, ["open", "high", "low", "close"]]
        rebuilt = aggregated.loc[common, ["open", "high", "low", "close"]]
        matches = bool(np.allclose(official.to_numpy(), rebuilt.to_numpy(), rtol=0.0, atol=1e-10))
        expected = int((source.index.max() - source.index.min()) / pd.Timedelta(hours=1)) + 1
        quality[symbol] = {
            "bars": len(source),
            "coverage": len(source) / expected,
            "daily_ohlc_exact": matches,
        }
    return quality


def row_at(frame: pd.DataFrame, timestamp: pd.Timestamp) -> pd.Series | None:
    return frame.loc[timestamp] if timestamp in frame.index else None


def exact_simulate(
    daily: dict[str, pd.DataFrame],
    hourly: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_rate: float = STRESS_COST,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    calendar = pd.date_range(
        min(frame.index.min() for frame in hourly.values()),
        end - pd.Timedelta(hours=1),
        freq="1h",
        tz="UTC",
    )
    cash = 1.0
    previous_equity = 1.0
    positions: dict[str, FuturesPosition] = {}
    trades: list[dict[str, Any]] = []
    path: list[dict[str, Any]] = []
    minimum_equity = 1.0
    maximum_initial_gross = 0.0

    for calendar_index, timestamp in enumerate(calendar):
        day = timestamp.floor("D")
        decision_day = day - pd.Timedelta(days=1)
        rows = {symbol: row_at(hourly[symbol], timestamp) for symbol in SYMBOLS}

        for symbol in list(positions):
            position = positions[symbol]
            row = rows[symbol]
            if row is None:
                continue
            reason = None
            if timestamp.hour == 0:
                holding_days = (
                    timestamp - pd.Timestamp(position.plan.generated_at_utc)
                ).total_seconds() / 86_400.0
                if holding_days >= MAX_HOLD_DAYS:
                    reason = "MAX_HOLD"
                elif audit_exit(daily, symbol, decision_day, position.plan.side):
                    reason = "SIGNAL_INVALID"
            if reason is not None:
                delta, _ = exit_quantity(
                    position,
                    timestamp,
                    position.remaining_quantity,
                    float(row["open"]),
                    reason,
                    cost_rate,
                )
                cash += delta
            else:
                delta, _ = process_open(position, timestamp, float(row["open"]), cost_rate)
                cash += delta
            if position.remaining_quantity <= 1e-12:
                trades.append(trade_record(position))
                del positions[symbol]

        if timestamp.hour == 0 and timestamp >= start:
            requests: list[tuple[str, Any, float]] = []
            for symbol in SYMBOLS:
                if symbol in positions or rows[symbol] is None:
                    continue
                side = audit_side(daily, symbol, decision_day)
                decision_row = row_at(daily[symbol], decision_day)
                if side is None or decision_row is None or pd.isna(decision_row["atr20"]):
                    continue
                try:
                    plan = build_plan(
                        symbol,
                        side,
                        timestamp,
                        float(rows[symbol]["open"]),
                        float(decision_row["atr20"]),
                        tp_r_multiples=TP_R_MULTIPLES,
                        max_hold_days=MAX_HOLD_DAYS,
                        risk_fraction=RISK_PER_TRADE,
                    )
                except ValueError:
                    continue
                risk_fraction = abs(plan.entry - plan.stop_loss) / plan.entry
                requests.append(
                    (symbol, plan, min(MAX_SYMBOL_NOTIONAL, RISK_PER_TRADE / risk_fraction))
                )

            unrealized = sum(
                position.direction
                * position.remaining_quantity
                * (float(rows[symbol]["open"]) - position.plan.entry)
                for symbol, position in positions.items()
                if rows[symbol] is not None
            )
            equity_at_open = cash + unrealized
            existing_gross = sum(
                position.remaining_quantity * float(rows[symbol]["open"])
                for symbol, position in positions.items()
                if rows[symbol] is not None
            ) / equity_at_open
            requested_total = sum(weight for _, _, weight in requests)
            available = max(0.0, MAX_INITIAL_GROSS - existing_gross)
            scale = min(1.0, available / requested_total) if requested_total > 0.0 else 0.0
            initial_gross = existing_gross
            for symbol, plan, weight in requests:
                notional = equity_at_open * weight * scale
                if notional <= 0.0:
                    continue
                quantity = notional / plan.entry
                fee = notional * cost_rate
                cash -= fee
                positions[symbol] = FuturesPosition(
                    plan=plan,
                    initial_quantity=quantity,
                    remaining_quantity=quantity,
                    initial_notional=notional,
                    initial_risk_cash=quantity * abs(plan.entry - plan.stop_loss),
                    entry_fee=fee,
                    current_stop=plan.stop_loss,
                    tp_fractions=TP_FRACTIONS,
                )
                initial_gross += notional / equity_at_open
            maximum_initial_gross = max(maximum_initial_gross, initial_gross)

        for symbol, position in positions.items():
            row = rows[symbol]
            if row is None:
                continue
            rate = float(funding[symbol].get(timestamp, 0.0))
            funding_cash = -position.direction * position.remaining_quantity * float(row["open"]) * rate
            position.funding_pnl += funding_cash
            cash += funding_cash

        for symbol in list(positions):
            row = rows[symbol]
            if row is None:
                continue
            position = positions[symbol]
            delta, _ = process_intraday(
                position,
                timestamp,
                float(row["low"]),
                float(row["high"]),
                cost_rate,
            )
            cash += delta
            if position.remaining_quantity <= 1e-12:
                trades.append(trade_record(position))
                del positions[symbol]

        is_day_end = calendar_index == len(calendar) - 1 or calendar[calendar_index + 1].floor("D") != day
        if is_day_end:
            unrealized = 0.0
            gross = 0.0
            for symbol, position in positions.items():
                row = rows[symbol]
                mark = float(row["close"]) if row is not None else position.plan.entry
                unrealized += position.direction * position.remaining_quantity * (mark - position.plan.entry)
                gross += position.remaining_quantity * mark
            equity = cash + unrealized
            path.append(
                {
                    "date": day,
                    "return": equity / previous_equity - 1.0,
                    "equity": equity,
                    "cash": cash,
                    "gross_exposure": gross / equity if equity > 0.0 else math.inf,
                    "open_positions": len(positions),
                }
            )
            previous_equity = equity
            minimum_equity = min(minimum_equity, equity)

    final_timestamp = calendar[-1]
    for symbol in list(positions):
        position = positions[symbol]
        row = row_at(hourly[symbol], final_timestamp)
        price = float(row["close"]) if row is not None else position.plan.entry
        delta, _ = exit_quantity(
            position,
            final_timestamp,
            position.remaining_quantity,
            price,
            "END_OF_FOLD",
            cost_rate,
        )
        cash += delta
        trades.append(trade_record(position))
        del positions[symbol]
    if path:
        prior = float(path[-2]["equity"]) if len(path) > 1 else 1.0
        path[-1].update(
            {"return": cash / prior - 1.0, "equity": cash, "cash": cash, "gross_exposure": 0.0, "open_positions": 0}
        )
    ledger = pd.DataFrame(trades)
    reconciled = 1.0 + float(ledger["net_pnl"].sum()) if not ledger.empty else 1.0
    return (
        pd.DataFrame(path).set_index("date"),
        ledger,
        {
            "minimum_equity": minimum_equity,
            "maximum_initial_gross": maximum_initial_gross,
            "final_equity": cash,
            "reconciled_equity": reconciled,
            "reconciliation_error": abs(cash - reconciled),
        },
    )


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


def main() -> int:
    args = parse_args()
    args.report_root.mkdir(parents=True, exist_ok=True)
    daily, hourly, funding = load_exact_inputs(args.data_root)
    quality = hourly_data_quality(daily, hourly)
    last_day = min(frame.index.max().floor("D") for frame in hourly.values())
    paths: list[pd.DataFrame] = []
    ledgers: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    for year in range(FIRST_OOS_YEAR, int(last_day.year) + 1):
        print(f"EXACT AUDIT {year}", flush=True)
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = min(pd.Timestamp(f"{year + 1}-01-01", tz="UTC"), last_day + pd.Timedelta(days=1))
        path, ledger, diagnostics = exact_simulate(daily, hourly, funding, start, end)
        path = path.loc[(path.index >= start) & (path.index < end)].copy()
        if not ledger.empty:
            entries = pd.to_datetime(ledger["entry_time"], utc=True)
            ledger = ledger.loc[(entries >= start) & (entries < end)].copy()
            ledger["fold_year"] = year
            ledgers.append(ledger)
        paths.append(path)
        fold_rows.append({"year": year, **path_metrics(path), **trade_metrics(ledger), **diagnostics})

    combined_path = pd.concat(paths).sort_index()
    combined_trades = pd.concat(ledgers, ignore_index=True)
    folds = pd.DataFrame(fold_rows)
    entries = pd.to_datetime(combined_trades["entry_time"], utc=True)
    overall = {**path_metrics(combined_path), **trade_metrics(combined_trades)}
    recent = {
        **path_metrics(combined_path.loc[combined_path.index >= RECENT_START]),
        **trade_metrics(combined_trades.loc[entries >= RECENT_START]),
    }
    completed = folds.loc[folds["days"] >= 360]
    block_lower = moving_block_lower(combined_trades["realized_r"])
    hac_p, adjusted_p = hac_one_sided_p(combined_trades["realized_r"])
    daily_reference = json.loads(SECOND_AUDIT.read_text(encoding="utf-8"))["variants"]["frozen_20bps"]
    manifest = pd.read_csv(HOURLY_MANIFEST)
    repair_manifest = pd.read_csv(GAP_REPAIR_MANIFEST)
    checks = {
        "archives_checksum_verified": bool((manifest["status"].str.endswith("VERIFIED")).sum() == 311),
        "gap_repair_checksum_verified": bool(repair_manifest["status"].str.endswith("VERIFIED").all()),
        "hourly_coverage_ge_99p9pct": all(item["coverage"] >= 0.999 for item in quality.values()),
        "hourly_rebuilds_daily_ohlc": all(item["daily_ohlc_exact"] for item in quality.values()),
        "oos_trades_ge_100": overall["trades"] >= 100,
        "oos_pf_ge_1p30": overall["profit_factor"] >= 1.30,
        "oos_sharpe_ge_1": overall["sharpe"] >= 1.0,
        "oos_drawdown_le_15pct": overall["max_drawdown"] >= -0.15,
        "positive_completed_years_ge_70pct": (completed["total_return"] > 0.0).mean() >= 0.70,
        "recent_pf_ge_1p20": recent["profit_factor"] >= 1.20,
        "recent_sharpe_ge_1": recent["sharpe"] >= 1.0,
        "recent_return_positive": recent["total_return"] > 0.0,
        "moving_block_lower_positive": block_lower > 0.0,
        "hac_bonferroni_p_lt_0p05": adjusted_p < 0.05,
        "signal_contract_complete": ledger_is_valid(combined_trades),
        "all_folds_reconciled": folds["reconciliation_error"].max() < 1e-10,
        "gross_cap_respected": folds["maximum_initial_gross"].max() <= MAX_INITIAL_GROSS + 1e-9,
        "pf_retains_70pct_of_daily": overall["profit_factor"] >= 0.70 * daily_reference["overall"]["profit_factor"],
        "return_retains_70pct_of_daily": overall["total_return"] >= 0.70 * daily_reference["overall"]["total_return"],
    }
    verdict = {
        "experiment": "CORE4_V7_EXECUTION_TIMING_AUDIT",
        "data_quality": quality,
        "moving_block_mean_r_lower_5pct": block_lower,
        "hac_one_sided_p": hac_p,
        "hac_bonferroni_adjusted_p": adjusted_p,
        "overall": overall,
        "recent_2025_plus": recent,
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "decision": "PAPER_FORWARD_CANDIDATE" if all(checks.values()) else "REJECT_V7_EXECUTION_AUDIT",
        "production_changed": False,
    }
    combined_path.to_csv(args.report_root / "oos_daily_path.csv")
    combined_trades.to_csv(args.report_root / "oos_trades.csv", index=False)
    folds.to_csv(args.report_root / "oos_folds.csv", index=False)
    (args.report_root / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
