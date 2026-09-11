"""Risk-defined CORE4 weekly trade-plan backtest with explicit SL/TP/time exits."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest_allocator import (
    DATA_ROOT,
    MOMENTUM_DAYS,
    SECONDS_PER_DAY,
    SMA_DAYS,
    SYMBOLS,
    load_symbol,
    max_drawdown,
)
from signal_contract import TradePlan


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "risk_defined_v2"
ATR_DAYS = 20
ATR_MULTIPLIER = 2.0
RISK_PER_TRADE = 0.005
MAX_SYMBOL_NOTIONAL = 0.30
MAX_INITIAL_GROSS = 0.90
MAX_HOLD_DAYS = 28
BASE_COST = 0.0010
STRESS_COST = 0.0020
TP_FRACTIONS = (0.25, 0.25, 0.50)


@dataclass
class Position:
    plan: TradePlan
    initial_quantity: float
    remaining_quantity: float
    initial_notional: float
    initial_risk_cash: float
    entry_fee: float
    current_stop: float
    next_target_index: int = 0
    exits: list[dict[str, Any]] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    components = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return components.max(axis=1)


def load_markets(data_root: Path) -> dict[str, pd.DataFrame]:
    markets: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        frame = load_symbol(data_root, symbol)[["open", "high", "low", "close"]].copy()
        frame["sma200"] = frame["close"].rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
        frame["momentum90"] = frame["close"].pct_change(MOMENTUM_DAYS[0], fill_method=None)
        frame["momentum180"] = frame["close"].pct_change(MOMENTUM_DAYS[1], fill_method=None)
        frame["atr20"] = true_range(frame).rolling(ATR_DAYS, min_periods=ATR_DAYS).mean()
        markets[symbol] = frame
    return markets


def row_at(markets: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp) -> pd.Series | None:
    frame = markets[symbol]
    if date not in frame.index:
        return None
    return frame.loc[date]


def systemic_risk_on(markets: dict[str, pd.DataFrame], date: pd.Timestamp) -> bool:
    row = row_at(markets, "BTCUSDT", date)
    if row is None:
        return False
    return bool(
        pd.notna(row["sma200"])
        and pd.notna(row["momentum90"])
        and row["close"] > row["sma200"]
        and row["momentum90"] > 0.0
    )


def asset_risk_on(markets: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp) -> bool:
    row = row_at(markets, symbol, date)
    if row is None:
        return False
    return bool(
        pd.notna(row["sma200"])
        and pd.notna(row["momentum90"])
        and pd.notna(row["momentum180"])
        and row["close"] > row["sma200"]
        and row["momentum90"] > 0.0
        and row["momentum180"] > 0.0
    )


def build_plan(
    symbol: str,
    generated_at: pd.Timestamp,
    entry: float,
    atr: float,
) -> TradePlan:
    risk_distance = ATR_MULTIPLIER * atr
    if not math.isfinite(risk_distance) or risk_distance <= 0.0 or risk_distance >= entry:
        raise ValueError("invalid ATR stop distance")
    generated = generated_at.to_pydatetime()
    plan = TradePlan(
        symbol=symbol,
        side="LONG",
        timeframe="1d",
        generated_at_utc=generated,
        entry=entry,
        stop_loss=entry - risk_distance,
        take_profit_1=entry + risk_distance,
        take_profit_2=entry + 2.0 * risk_distance,
        take_profit_3=entry + 3.0 * risk_distance,
        invalid_after_utc=(generated_at + pd.Timedelta(days=1)).to_pydatetime(),
        max_holding_hours=MAX_HOLD_DAYS * 24,
        review_interval_hours=7 * 24,
        capital_at_risk_fraction=RISK_PER_TRADE,
        early_exit_condition="BTC systemic gate or asset trend gate turns off",
        paper_only=True,
    )
    plan.validate()
    return plan


def target_prices(plan: TradePlan) -> tuple[float, float, float]:
    return plan.take_profit_1, plan.take_profit_2, plan.take_profit_3


def close_quantity(
    position: Position,
    date: pd.Timestamp,
    quantity: float,
    price: float,
    reason: str,
    cost_rate: float,
) -> tuple[float, float]:
    quantity = min(quantity, position.remaining_quantity)
    gross = quantity * price
    fee = gross * cost_rate
    position.remaining_quantity -= quantity
    position.exits.append(
        {
            "time": date,
            "quantity": quantity,
            "price": price,
            "reason": reason,
            "fee": fee,
        }
    )
    return gross - fee, fee


def process_known_open(
    position: Position,
    date: pd.Timestamp,
    open_price: float,
    cost_rate: float,
) -> tuple[float, float]:
    cash_added = 0.0
    fees = 0.0
    if open_price <= position.current_stop:
        return close_quantity(
            position,
            date,
            position.remaining_quantity,
            open_price,
            "STOP_GAP",
            cost_rate,
        )
    targets = target_prices(position.plan)
    while position.next_target_index < 3 and open_price >= targets[position.next_target_index]:
        index = position.next_target_index
        quantity = position.initial_quantity * TP_FRACTIONS[index]
        added, fee = close_quantity(
            position,
            date,
            quantity,
            targets[index],
            f"TP{index + 1}_GAP",
            cost_rate,
        )
        cash_added += added
        fees += fee
        position.next_target_index += 1
        if position.next_target_index == 1:
            position.current_stop = position.plan.entry
        elif position.next_target_index == 2:
            position.current_stop = position.plan.take_profit_1
    return cash_added, fees


def process_intraday(
    position: Position,
    date: pd.Timestamp,
    low: float,
    high: float,
    cost_rate: float,
) -> tuple[float, float]:
    if position.remaining_quantity <= 1e-12:
        return 0.0, 0.0
    if low <= position.current_stop:
        return close_quantity(
            position,
            date,
            position.remaining_quantity,
            position.current_stop,
            "STOP",
            cost_rate,
        )
    cash_added = 0.0
    fees = 0.0
    targets = target_prices(position.plan)
    while position.next_target_index < 3 and high >= targets[position.next_target_index]:
        index = position.next_target_index
        quantity = position.initial_quantity * TP_FRACTIONS[index]
        added, fee = close_quantity(
            position,
            date,
            quantity,
            targets[index],
            f"TP{index + 1}",
            cost_rate,
        )
        cash_added += added
        fees += fee
        position.next_target_index += 1
        if position.next_target_index == 1:
            position.current_stop = position.plan.entry
        elif position.next_target_index == 2:
            position.current_stop = position.plan.take_profit_1
    return cash_added, fees


def trade_record(position: Position) -> dict[str, Any]:
    exit_gross = sum(exit_row["quantity"] * exit_row["price"] for exit_row in position.exits)
    exit_fees = sum(exit_row["fee"] for exit_row in position.exits)
    net_pnl = exit_gross - exit_fees - position.initial_notional - position.entry_fee
    last_exit = position.exits[-1]
    holding_days = (
        pd.Timestamp(last_exit["time"]) - pd.Timestamp(position.plan.generated_at_utc)
    ).total_seconds() / SECONDS_PER_DAY
    reasons = "+".join(dict.fromkeys(str(row["reason"]) for row in position.exits))
    return {
        "symbol": position.plan.symbol,
        "side": position.plan.side,
        "timeframe": position.plan.timeframe,
        "entry_time": position.plan.generated_at_utc,
        "entry": position.plan.entry,
        "initial_stop": position.plan.stop_loss,
        "tp1": position.plan.take_profit_1,
        "tp2": position.plan.take_profit_2,
        "tp3": position.plan.take_profit_3,
        "max_holding_hours": position.plan.max_holding_hours,
        "initial_notional": position.initial_notional,
        "initial_risk_cash": position.initial_risk_cash,
        "exit_time": last_exit["time"],
        "holding_days": holding_days,
        "exit_reason": reasons,
        "entry_fee": position.entry_fee,
        "exit_fees": exit_fees,
        "net_pnl": net_pnl,
        "net_return_on_notional": net_pnl / position.initial_notional,
        "realized_r": net_pnl / position.initial_risk_cash,
        "tp1_hit": any(str(row["reason"]).startswith("TP1") for row in position.exits),
        "tp2_hit": any(str(row["reason"]).startswith("TP2") for row in position.exits),
        "tp3_hit": any(str(row["reason"]).startswith("TP3") for row in position.exits),
        "paper_only": True,
    }


def simulate(
    markets: dict[str, pd.DataFrame],
    cost_rate: float,
    candidate_limit: int | None = None,
    entry_start: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float]]:
    calendar = pd.date_range(
        min(frame.index.min() for frame in markets.values()),
        max(frame.index.max() for frame in markets.values()),
        freq="1D",
        tz="UTC",
    )
    cash = 1.0
    positions: dict[str, Position] = {}
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    previous_equity = 1.0
    minimum_cash = cash
    maximum_initial_gross = 0.0

    for calendar_index in range(1, len(calendar)):
        date = calendar[calendar_index]
        decision_date = calendar[calendar_index - 1]
        is_review = date.weekday() == 0

        for symbol in list(positions):
            position = positions[symbol]
            market_row = row_at(markets, symbol, date)
            if market_row is None or pd.isna(market_row["open"]):
                continue
            open_price = float(market_row["open"])
            holding_days = (date - pd.Timestamp(position.plan.generated_at_utc)).days
            reason = None
            if holding_days >= MAX_HOLD_DAYS:
                reason = "MAX_HOLD"
            elif is_review and (
                not systemic_risk_on(markets, decision_date)
                or not asset_risk_on(markets, symbol, decision_date)
            ):
                reason = "EARLY_RISK_OFF"
            if reason is not None:
                added, fee = close_quantity(
                    position,
                    date,
                    position.remaining_quantity,
                    open_price,
                    reason,
                    cost_rate,
                )
                cash += added
                events.append({"time": date, "symbol": symbol, "event": reason, "price": open_price, "fee": fee})
            else:
                added, fee = process_known_open(position, date, open_price, cost_rate)
                cash += added
                if added:
                    events.append({"time": date, "symbol": symbol, "event": "OPEN_BARRIER", "price": open_price, "fee": fee})
            if position.remaining_quantity <= 1e-12:
                trades.append(trade_record(position))
                del positions[symbol]

        entries_enabled = entry_start is None or date >= entry_start
        if is_review and entries_enabled and systemic_risk_on(markets, decision_date):
            candidates: list[tuple[str, TradePlan, float, float]] = []
            for symbol in SYMBOLS:
                if symbol in positions or not asset_risk_on(markets, symbol, decision_date):
                    continue
                row = row_at(markets, symbol, date)
                decision_row = row_at(markets, symbol, decision_date)
                if row is None or decision_row is None:
                    continue
                if pd.isna(row["open"]) or pd.isna(decision_row["atr20"]):
                    continue
                plan = build_plan(symbol, date, float(row["open"]), float(decision_row["atr20"]))
                stop_fraction = (plan.entry - plan.stop_loss) / plan.entry
                requested_weight = min(MAX_SYMBOL_NOTIONAL, RISK_PER_TRADE / stop_fraction)
                relative_strength = 0.5 * float(decision_row["momentum90"]) + 0.5 * float(
                    decision_row["momentum180"]
                )
                candidates.append((symbol, plan, requested_weight, relative_strength))
            candidates.sort(key=lambda item: (-item[3], item[0]))
            if candidate_limit is not None:
                candidates = candidates[:candidate_limit]

            existing_value = 0.0
            for symbol, position in positions.items():
                row = row_at(markets, symbol, date)
                mark = float(row["open"]) if row is not None and pd.notna(row["open"]) else position.plan.entry
                existing_value += position.remaining_quantity * mark
            equity_at_open = cash + existing_value
            existing_gross = existing_value / equity_at_open if equity_at_open > 0.0 else 1.0
            available_weight = max(0.0, MAX_INITIAL_GROSS - existing_gross)
            requested_total = sum(weight for _, _, weight, _ in candidates)
            scale = min(1.0, available_weight / requested_total) if requested_total > 0.0 else 0.0
            scale = min(scale, cash / (equity_at_open * requested_total)) if requested_total > 0.0 and equity_at_open > 0.0 else 0.0
            initial_gross = existing_gross
            for symbol, plan, requested_weight, _ in candidates:
                weight = requested_weight * scale
                notional = equity_at_open * weight
                if notional <= 0.0:
                    continue
                quantity = notional / plan.entry
                entry_fee = notional * cost_rate
                if notional + entry_fee > cash:
                    notional = cash / (1.0 + cost_rate)
                    quantity = notional / plan.entry
                    entry_fee = notional * cost_rate
                cash -= notional + entry_fee
                position = Position(
                    plan=plan,
                    initial_quantity=quantity,
                    remaining_quantity=quantity,
                    initial_notional=notional,
                    initial_risk_cash=quantity * (plan.entry - plan.stop_loss),
                    entry_fee=entry_fee,
                    current_stop=plan.stop_loss,
                )
                positions[symbol] = position
                initial_gross += notional / equity_at_open
                events.append({"time": date, "symbol": symbol, "event": "ENTRY", "price": plan.entry, "fee": entry_fee})
            maximum_initial_gross = max(maximum_initial_gross, initial_gross)

        for symbol in list(positions):
            position = positions[symbol]
            row = row_at(markets, symbol, date)
            if row is None or pd.isna(row["low"]) or pd.isna(row["high"]):
                continue
            added, fee = process_intraday(
                position,
                date,
                float(row["low"]),
                float(row["high"]),
                cost_rate,
            )
            cash += added
            if added:
                events.append({"time": date, "symbol": symbol, "event": "INTRADAY_BARRIER", "price": float(row["close"]), "fee": fee})
            if position.remaining_quantity <= 1e-12:
                trades.append(trade_record(position))
                del positions[symbol]

        marked_value = 0.0
        for symbol, position in positions.items():
            row = row_at(markets, symbol, date)
            mark = float(row["close"]) if row is not None and pd.notna(row["close"]) else position.plan.entry
            marked_value += position.remaining_quantity * mark
        equity = cash + marked_value
        daily_return = equity / previous_equity - 1.0 if previous_equity > 0.0 else 0.0
        daily_rows.append(
            {
                "date": date,
                "return": daily_return,
                "equity": equity,
                "cash": cash,
                "gross_exposure": marked_value / equity if equity > 0.0 else 0.0,
                "open_positions": len(positions),
            }
        )
        previous_equity = equity
        minimum_cash = min(minimum_cash, cash)

    final_date = calendar[-1]
    for symbol in list(positions):
        position = positions[symbol]
        row = row_at(markets, symbol, final_date)
        price = float(row["close"]) if row is not None else position.plan.entry
        added, fee = close_quantity(
            position,
            final_date,
            position.remaining_quantity,
            price,
            "END_OF_DATA",
            cost_rate,
        )
        cash += added
        events.append({"time": final_date, "symbol": symbol, "event": "END_OF_DATA", "price": price, "fee": fee})
        trades.append(trade_record(position))
        del positions[symbol]
    if daily_rows:
        final_equity_before = float(daily_rows[-1]["equity"])
        daily_rows[-1]["return"] = cash / float(daily_rows[-2]["equity"]) - 1.0 if len(daily_rows) > 1 else cash - 1.0
        daily_rows[-1]["equity"] = cash
        daily_rows[-1]["cash"] = cash
        daily_rows[-1]["gross_exposure"] = 0.0
        daily_rows[-1]["open_positions"] = 0
        del final_equity_before
    diagnostics = {
        "minimum_cash": minimum_cash,
        "maximum_initial_gross": maximum_initial_gross,
    }
    return (
        pd.DataFrame(daily_rows).set_index("date"),
        pd.DataFrame(trades),
        pd.DataFrame(events),
        diagnostics,
    )


def profit_factor(values: pd.Series) -> float:
    gains = float(values.loc[values > 0.0].sum())
    losses = float(-values.loc[values < 0.0].sum())
    return gains / losses if losses > 0.0 else math.inf if gains > 0.0 else 0.0


def path_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    returns = frame["return"].dropna()
    if returns.empty:
        return {"days": 0, "total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    years = max((returns.index[-1] - returns.index[0]).total_seconds() / (365.0 * SECONDS_PER_DAY), 1.0 / 365.0)
    total = float((1.0 + returns).prod() - 1.0)
    standard_deviation = float(returns.std(ddof=0))
    return {
        "days": len(returns),
        "total_return": total,
        "cagr": float((1.0 + total) ** (1.0 / years) - 1.0),
        "sharpe": float(returns.mean() / standard_deviation * math.sqrt(365.0)) if standard_deviation > 0.0 else 0.0,
        "max_drawdown": max_drawdown(returns),
    }


def trade_metrics(trades: pd.DataFrame) -> dict[str, float | int]:
    if trades.empty:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "mean_r": 0.0, "median_holding_days": 0.0}
    return {
        "trades": len(trades),
        "win_rate": float((trades["net_pnl"] > 0.0).mean()),
        "profit_factor": profit_factor(trades["net_pnl"]),
        "mean_r": float(trades["realized_r"].mean()),
        "median_holding_days": float(trades["holding_days"].median()),
    }


def period_tables(path: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    end = path.index.max() + pd.Timedelta(days=1)
    periods = {
        "FULL": (pd.Timestamp("2018-01-01", tz="UTC"), end),
        "DEVELOPMENT": (pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2022-01-01", tz="UTC")),
        "BEAR_2022": (pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2023-01-01", tz="UTC")),
        "VALIDATION_2022_2023": (pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
        "EVALUATION_2024_PLUS": (pd.Timestamp("2024-01-01", tz="UTC"), end),
        "RECENT_2025_PLUS": (pd.Timestamp("2025-01-01", tz="UTC"), end),
    }
    entry_times = pd.to_datetime(trades["entry_time"], utc=True) if not trades.empty else pd.Series(dtype="datetime64[ns, UTC]")
    rows = []
    for name, (start, stop) in periods.items():
        selected_path = path.loc[(path.index >= start) & (path.index < stop)]
        selected_trades = trades.loc[(entry_times >= start) & (entry_times < stop)] if not trades.empty else trades
        rows.append({"period": name, **path_metrics(selected_path), **trade_metrics(selected_trades)})
    return pd.DataFrame(rows)


def yearly_table(path: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    entry_years = pd.to_datetime(trades["entry_time"], utc=True).dt.year if not trades.empty else pd.Series(dtype=int)
    for year, group in path.loc[path.index.year >= 2018].groupby(path.loc[path.index.year >= 2018].index.year):
        year_trades = trades.loc[entry_years.eq(year)] if not trades.empty else trades
        rows.append({"year": int(year), **path_metrics(group), **trade_metrics(year_trades)})
    return pd.DataFrame(rows)


def gates(periods: pd.DataFrame, years: pd.DataFrame, diagnostics: dict[str, float]) -> dict[str, bool]:
    table = periods.set_index("period")
    full = table.loc["FULL"]
    evaluation = table.loc["EVALUATION_2024_PLUS"]
    bear = table.loc["BEAR_2022"]
    active_years = years.loc[years["trades"] > 0]
    return {
        "full_trades_ge_80": bool(full["trades"] >= 80),
        "evaluation_trades_ge_20": bool(evaluation["trades"] >= 20),
        "full_pf_ge_1p30": bool(full["profit_factor"] >= 1.30),
        "evaluation_pf_ge_1p20": bool(evaluation["profit_factor"] >= 1.20),
        "full_sharpe_ge_1": bool(full["sharpe"] >= 1.0),
        "evaluation_sharpe_ge_1": bool(evaluation["sharpe"] >= 1.0),
        "full_drawdown_le_20pct": bool(full["max_drawdown"] >= -0.20),
        "evaluation_drawdown_le_15pct": bool(evaluation["max_drawdown"] >= -0.15),
        "bear_2022_nonnegative": bool(bear["total_return"] >= 0.0),
        "positive_active_years_ge_70pct": bool((active_years["total_return"] > 0.0).mean() >= 0.70),
        "average_realized_r_positive": bool(full["mean_r"] > 0.0),
        "no_capital_violation": bool(diagnostics["minimum_cash"] >= -1e-9 and diagnostics["maximum_initial_gross"] <= MAX_INITIAL_GROSS + 1e-9),
    }


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
    markets = load_markets(args.data_root)
    base_path, base_trades, _, _ = simulate(markets, BASE_COST)
    stress_path, stress_trades, stress_events, diagnostics = simulate(markets, STRESS_COST)
    final_equity = float(stress_path["equity"].iloc[-1])
    reconciled_equity = 1.0 + float(stress_trades["net_pnl"].sum())
    diagnostics.update(
        {
            "final_equity": final_equity,
            "reconciled_equity": reconciled_equity,
            "equity_reconciliation_error": abs(final_equity - reconciled_equity),
            "maximum_realized_holding_days": float(stress_trades["holding_days"].max()),
            "incomplete_trade_plan_rows": int(
                stress_trades[
                    ["entry", "initial_stop", "tp1", "tp2", "tp3", "max_holding_hours"]
                ].isna().any(axis=1).sum()
            ),
        }
    )
    periods = period_tables(stress_path, stress_trades)
    years = yearly_table(stress_path, stress_trades)
    checks = gates(periods, years, diagnostics)
    verdict = {
        "experiment": "CORE4_CLEANROOM_RISK_DEFINED_V2",
        "parameter_search_count": 0,
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "diagnostics": diagnostics,
        "decision": "PAPER_OBSERVATION_ALLOWED" if all(checks.values()) else "REJECT_V2",
        "production_changed": False,
    }
    base_path.to_csv(args.report_root / "base_daily_path.csv")
    base_trades.to_csv(args.report_root / "base_trades.csv", index=False)
    stress_path.to_csv(args.report_root / "stress_daily_path.csv")
    stress_trades.to_csv(args.report_root / "stress_trades.csv", index=False)
    stress_events.to_csv(args.report_root / "stress_events.csv", index=False)
    periods.to_csv(args.report_root / "stress_periods.csv", index=False)
    years.to_csv(args.report_root / "stress_years.csv", index=False)
    (args.report_root / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), encoding="utf-8"
    )
    lines = [
        "# CORE4 Risk-Defined V2",
        "",
        f"**{verdict['decision']}**",
        "",
        "Fixed LONG/cash rules with explicit ATR stop, TP1/TP2/TP3, and 28-day maximum hold.",
        "Stress results include 20 bps cost per one-way traded notional.",
        "",
        "| Period | Trades | Win | PF | Mean R | Return | CAGR | Sharpe | Max DD | Median hold |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in periods.itertuples(index=False):
        lines.append(
            f"| {row.period} | {row.trades} | {row.win_rate:.1%} | {row.profit_factor:.3f} | "
            f"{row.mean_r:.3f} | {row.total_return:.2%} | {row.cagr:.2%} | {row.sharpe:.3f} | "
            f"{row.max_drawdown:.2%} | {row.median_holding_days:.1f}d |"
        )
    lines.extend(
        [
            "",
            f"Gate: {sum(checks.values())}/{len(checks)} checks passed.",
            f"Equity reconciliation error: {diagnostics['equity_reconciliation_error']:.3e}.",
            f"Maximum realized holding time: {diagnostics['maximum_realized_holding_days']:.1f} days.",
            f"Incomplete trade plans: {diagnostics['incomplete_trade_plan_rows']}.",
            "No paper alerts, deployment, Telegram message, or live order was enabled.",
        ]
    )
    (args.report_root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_safe(verdict), indent=2), flush=True)
    print(periods.to_string(index=False), flush=True)
    print(years.to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
