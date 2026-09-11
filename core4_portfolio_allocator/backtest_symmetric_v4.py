"""Symmetric CORE4 USD-M futures walk-forward with funding and risk plans."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from backtest_allocator import SECONDS_PER_DAY, SYMBOLS, max_drawdown
from backtest_risk_defined import path_metrics, profit_factor, trade_metrics, true_range
from fetch_binance_futures import DATA_ROOT
from futures_data import daily_funding_rates, load_funding_events, load_futures_daily
from signal_contract import TradePlan


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "symmetric_v4"
SMA_DAYS = 200
MOMENTUM_DAYS = (90, 180)
ATR_DAYS = 20
ATR_MULTIPLIER = 2.0
RISK_PER_TRADE = 0.0035
MAX_SYMBOL_NOTIONAL = 0.20
MAX_INITIAL_GROSS = 0.60
MAX_HOLD_DAYS = 28
BASE_COST = 0.0010
STRESS_COST = 0.0020
TP_FRACTIONS = (0.25, 0.25, 0.50)
FIRST_OOS_YEAR = 2021
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 5404


@dataclass
class FuturesPosition:
    plan: TradePlan
    initial_quantity: float
    remaining_quantity: float
    initial_notional: float
    initial_risk_cash: float
    entry_fee: float
    current_stop: float
    funding_pnl: float = 0.0
    next_target_index: int = 0
    exits: list[dict[str, Any]] = field(default_factory=list)
    tp_fractions: tuple[float, float, float] = TP_FRACTIONS

    @property
    def direction(self) -> float:
        return 1.0 if self.plan.side == "LONG" else -1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def load_inputs(
    data_root: Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    markets: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for symbol in SYMBOLS:
        frame = load_futures_daily(symbol, data_root)[["open", "high", "low", "close"]].copy()
        frame["sma200"] = frame["close"].rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
        frame["momentum90"] = frame["close"].pct_change(MOMENTUM_DAYS[0], fill_method=None)
        frame["momentum180"] = frame["close"].pct_change(MOMENTUM_DAYS[1], fill_method=None)
        frame["atr20"] = true_range(frame).rolling(ATR_DAYS, min_periods=ATR_DAYS).mean()
        markets[symbol] = frame
        funding[symbol] = daily_funding_rates(load_funding_events(symbol, data_root))
    return markets, funding


def row_at(
    markets: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> pd.Series | None:
    frame = markets[symbol]
    return frame.loc[date] if date in frame.index else None


def desired_side(
    markets: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> str | None:
    row = row_at(markets, symbol, date)
    if row is None or row[["close", "sma200", "momentum90", "momentum180"]].isna().any():
        return None
    if (
        row["close"] > row["sma200"]
        and row["momentum90"] > 0.0
        and row["momentum180"] > 0.0
    ):
        return "LONG"
    if (
        row["close"] < row["sma200"]
        and row["momentum90"] < 0.0
        and row["momentum180"] < 0.0
    ):
        return "SHORT"
    return None


def build_plan(
    symbol: str,
    side: str,
    generated_at: pd.Timestamp,
    entry: float,
    atr: float,
    tp_r_multiples: tuple[float, float, float] = (1.0, 2.0, 3.0),
    max_hold_days: int = MAX_HOLD_DAYS,
    risk_fraction: float = RISK_PER_TRADE,
) -> TradePlan:
    distance = ATR_MULTIPLIER * atr
    if not math.isfinite(distance) or distance <= 0.0 or distance >= entry:
        raise ValueError("invalid ATR distance")
    sign = 1.0 if side == "LONG" else -1.0
    if side == "SHORT" and entry - tp_r_multiples[-1] * distance <= 0.0:
        raise ValueError("short TP3 must remain positive")
    plan = TradePlan(
        symbol=symbol,
        side=side,
        timeframe="1d",
        generated_at_utc=generated_at.to_pydatetime(),
        entry=entry,
        stop_loss=entry - sign * distance,
        take_profit_1=entry + sign * tp_r_multiples[0] * distance,
        take_profit_2=entry + sign * tp_r_multiples[1] * distance,
        take_profit_3=entry + sign * tp_r_multiples[2] * distance,
        invalid_after_utc=(generated_at + pd.Timedelta(days=1)).to_pydatetime(),
        max_holding_hours=max_hold_days * 24,
        review_interval_hours=7 * 24,
        capital_at_risk_fraction=risk_fraction,
        early_exit_condition="Daily trend side is invalid at weekly review",
        paper_only=True,
    )
    plan.validate()
    return plan


def targets(position: FuturesPosition) -> tuple[float, float, float]:
    plan = position.plan
    return plan.take_profit_1, plan.take_profit_2, plan.take_profit_3


def exit_quantity(
    position: FuturesPosition,
    date: pd.Timestamp,
    quantity: float,
    price: float,
    reason: str,
    cost_rate: float,
) -> tuple[float, float]:
    quantity = min(quantity, position.remaining_quantity)
    gross_notional = quantity * price
    fee = gross_notional * cost_rate
    realized_price_pnl = position.direction * quantity * (price - position.plan.entry)
    position.remaining_quantity -= quantity
    position.exits.append(
        {
            "time": date,
            "quantity": quantity,
            "price": price,
            "reason": reason,
            "fee": fee,
            "price_pnl": realized_price_pnl,
        }
    )
    return realized_price_pnl - fee, fee


def move_stop(position: FuturesPosition) -> None:
    if position.next_target_index == 1:
        position.current_stop = position.plan.entry
    elif position.next_target_index == 2:
        position.current_stop = position.plan.take_profit_1


def stop_hit(position: FuturesPosition, low: float, high: float) -> bool:
    return low <= position.current_stop if position.plan.side == "LONG" else high >= position.current_stop


def target_hit(position: FuturesPosition, low: float, high: float) -> bool:
    if position.next_target_index >= 3:
        return False
    level = targets(position)[position.next_target_index]
    return high >= level if position.plan.side == "LONG" else low <= level


def process_open(
    position: FuturesPosition,
    date: pd.Timestamp,
    open_price: float,
    cost_rate: float,
) -> tuple[float, float]:
    adverse_gap = (
        open_price <= position.current_stop
        if position.plan.side == "LONG"
        else open_price >= position.current_stop
    )
    if adverse_gap:
        return exit_quantity(
            position,
            date,
            position.remaining_quantity,
            open_price,
            "STOP_GAP",
            cost_rate,
        )
    cash_delta = 0.0
    fees = 0.0
    while position.next_target_index < 3:
        level = targets(position)[position.next_target_index]
        favorable_gap = open_price >= level if position.plan.side == "LONG" else open_price <= level
        if not favorable_gap:
            break
        index = position.next_target_index
        delta, fee = exit_quantity(
            position,
            date,
            position.initial_quantity * position.tp_fractions[index],
            level,
            f"TP{index + 1}_GAP",
            cost_rate,
        )
        cash_delta += delta
        fees += fee
        position.next_target_index += 1
        move_stop(position)
    return cash_delta, fees


def process_intraday(
    position: FuturesPosition,
    date: pd.Timestamp,
    low: float,
    high: float,
    cost_rate: float,
) -> tuple[float, float]:
    if position.remaining_quantity <= 1e-12:
        return 0.0, 0.0
    if stop_hit(position, low, high):
        return exit_quantity(
            position,
            date,
            position.remaining_quantity,
            position.current_stop,
            "STOP",
            cost_rate,
        )
    cash_delta = 0.0
    fees = 0.0
    while target_hit(position, low, high):
        index = position.next_target_index
        level = targets(position)[index]
        delta, fee = exit_quantity(
            position,
            date,
            position.initial_quantity * position.tp_fractions[index],
            level,
            f"TP{index + 1}",
            cost_rate,
        )
        cash_delta += delta
        fees += fee
        position.next_target_index += 1
        move_stop(position)
    return cash_delta, fees


def trade_record(position: FuturesPosition) -> dict[str, Any]:
    price_pnl = sum(float(item["price_pnl"]) for item in position.exits)
    exit_fees = sum(float(item["fee"]) for item in position.exits)
    net_pnl = price_pnl + position.funding_pnl - position.entry_fee - exit_fees
    final_exit = position.exits[-1]
    holding_days = (
        pd.Timestamp(final_exit["time"]) - pd.Timestamp(position.plan.generated_at_utc)
    ).total_seconds() / SECONDS_PER_DAY
    return {
        "symbol": position.plan.symbol,
        "side": position.plan.side,
        "timeframe": "1d",
        "entry_time": position.plan.generated_at_utc,
        "entry": position.plan.entry,
        "initial_stop": position.plan.stop_loss,
        "tp1": position.plan.take_profit_1,
        "tp2": position.plan.take_profit_2,
        "tp3": position.plan.take_profit_3,
        "max_holding_hours": position.plan.max_holding_hours,
        "exit_time": final_exit["time"],
        "holding_days": holding_days,
        "exit_reason": "+".join(dict.fromkeys(str(item["reason"]) for item in position.exits)),
        "initial_notional": position.initial_notional,
        "initial_risk_cash": position.initial_risk_cash,
        "entry_fee": position.entry_fee,
        "exit_fees": exit_fees,
        "price_pnl": price_pnl,
        "funding_pnl": position.funding_pnl,
        "net_pnl": net_pnl,
        "net_return_on_notional": net_pnl / position.initial_notional,
        "realized_r": net_pnl / position.initial_risk_cash,
        "tp1_hit": any(str(item["reason"]).startswith("TP1") for item in position.exits),
        "tp2_hit": any(str(item["reason"]).startswith("TP2") for item in position.exits),
        "tp3_hit": any(str(item["reason"]).startswith("TP3") for item in position.exits),
        "paper_only": True,
    }


def simulate(
    markets: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    cost_rate: float,
    entry_start: pd.Timestamp,
    entry_side_function: Callable[[dict[str, pd.DataFrame], str, pd.Timestamp], str | None] = desired_side,
    exit_function: Callable[[dict[str, pd.DataFrame], str, pd.Timestamp, str], bool] | None = None,
    entry_weekday: int | None = 0,
    review_weekday: int | None = 0,
    tp_r_multiples: tuple[float, float, float] = (1.0, 2.0, 3.0),
    tp_fractions: tuple[float, float, float] = TP_FRACTIONS,
    max_hold_days: int = MAX_HOLD_DAYS,
    risk_per_trade: float = RISK_PER_TRADE,
    max_symbol_notional: float = MAX_SYMBOL_NOTIONAL,
    max_initial_gross: float = MAX_INITIAL_GROSS,
    allowed_sleeves: set[tuple[str, str]] | None = None,
    entry_signal_lag_days: int = 0,
    funding_credit_fraction: float = 1.0,
    funding_cost_multiplier: float = 1.0,
    symbol_universe: tuple[str, ...] = SYMBOLS,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    if entry_signal_lag_days < 0:
        raise ValueError("entry_signal_lag_days must be nonnegative")
    if not 0.0 <= funding_credit_fraction <= 1.0:
        raise ValueError("funding_credit_fraction must be between zero and one")
    if funding_cost_multiplier < 1.0:
        raise ValueError("funding_cost_multiplier must be at least one")
    calendar = pd.date_range(
        min(frame.index.min() for frame in markets.values()),
        max(frame.index.max() for frame in markets.values()),
        freq="1D",
        tz="UTC",
    )
    cash = 1.0
    positions: dict[str, FuturesPosition] = {}
    trades: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    previous_equity = 1.0
    minimum_equity = 1.0
    maximum_initial_gross = 0.0

    for index in range(1, len(calendar)):
        date = calendar[index]
        decision_date = calendar[index - 1]
        entry_decision_index = index - 1 - entry_signal_lag_days
        entry_decision_date = (
            calendar[entry_decision_index] if entry_decision_index >= 0 else None
        )
        is_entry_time = entry_weekday is None or date.weekday() == entry_weekday
        is_review = review_weekday is None or date.weekday() == review_weekday

        for symbol in list(positions):
            position = positions[symbol]
            row = row_at(markets, symbol, date)
            if row is None:
                continue
            holding_days = (date - pd.Timestamp(position.plan.generated_at_utc)).days
            reason = None
            if holding_days >= max_hold_days:
                reason = "MAX_HOLD"
            elif is_review and exit_function is not None and exit_function(
                markets, symbol, decision_date, position.plan.side
            ):
                reason = "SIGNAL_INVALID"
            elif is_review and exit_function is None and entry_side_function(
                markets, symbol, decision_date
            ) != position.plan.side:
                reason = "SIGNAL_INVALID"
            if reason:
                delta, _ = exit_quantity(
                    position,
                    date,
                    position.remaining_quantity,
                    float(row["open"]),
                    reason,
                    cost_rate,
                )
                cash += delta
            else:
                delta, _ = process_open(position, date, float(row["open"]), cost_rate)
                cash += delta
            if position.remaining_quantity <= 1e-12:
                trades.append(trade_record(position))
                del positions[symbol]

        if is_entry_time and date >= entry_start and entry_decision_date is not None:
            requests: list[tuple[str, TradePlan, float]] = []
            for symbol in symbol_universe:
                if symbol in positions:
                    continue
                side = entry_side_function(markets, symbol, entry_decision_date)
                if side is not None and allowed_sleeves is not None and (symbol, side) not in allowed_sleeves:
                    side = None
                row = row_at(markets, symbol, date)
                decision_row = row_at(markets, symbol, entry_decision_date)
                if side is None or row is None or decision_row is None or pd.isna(decision_row["atr20"]):
                    continue
                try:
                    plan = build_plan(
                        symbol,
                        side,
                        date,
                        float(row["open"]),
                        float(decision_row["atr20"]),
                        tp_r_multiples=tp_r_multiples,
                        max_hold_days=max_hold_days,
                        risk_fraction=risk_per_trade,
                    )
                except ValueError:
                    continue
                risk_fraction = abs(plan.entry - plan.stop_loss) / plan.entry
                weight = min(max_symbol_notional, risk_per_trade / risk_fraction)
                requests.append((symbol, plan, weight))

            marks = {
                symbol: float(row_at(markets, symbol, date)["open"])
                for symbol in positions
            }
            unrealized = sum(
                position.direction
                * position.remaining_quantity
                * (marks[symbol] - position.plan.entry)
                for symbol, position in positions.items()
            )
            equity_at_open = cash + unrealized
            existing_gross = sum(
                position.remaining_quantity * marks[symbol]
                for symbol, position in positions.items()
            ) / equity_at_open
            requested_total = sum(weight for _, _, weight in requests)
            available = max(0.0, max_initial_gross - existing_gross)
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
                    tp_fractions=tp_fractions,
                )
                initial_gross += notional / equity_at_open
            maximum_initial_gross = max(maximum_initial_gross, initial_gross)

        for symbol, position in positions.items():
            row = row_at(markets, symbol, date)
            if row is None:
                continue
            rate = float(funding[symbol].get(date, 0.0))
            funding_cash = -position.direction * position.remaining_quantity * float(row["close"]) * rate
            if funding_cash >= 0.0:
                funding_cash *= funding_credit_fraction
            else:
                funding_cash *= funding_cost_multiplier
            position.funding_pnl += funding_cash
            cash += funding_cash

        for symbol in list(positions):
            position = positions[symbol]
            row = row_at(markets, symbol, date)
            if row is None:
                continue
            delta, _ = process_intraday(
                position,
                date,
                float(row["low"]),
                float(row["high"]),
                cost_rate,
            )
            cash += delta
            if position.remaining_quantity <= 1e-12:
                trades.append(trade_record(position))
                del positions[symbol]

        unrealized = 0.0
        gross = 0.0
        for symbol, position in positions.items():
            row = row_at(markets, symbol, date)
            mark = float(row["close"]) if row is not None else position.plan.entry
            unrealized += position.direction * position.remaining_quantity * (mark - position.plan.entry)
            gross += position.remaining_quantity * mark
        equity = cash + unrealized
        daily.append(
            {
                "date": date,
                "return": equity / previous_equity - 1.0,
                "equity": equity,
                "cash": cash,
                "gross_exposure": gross / equity if equity > 0.0 else math.inf,
                "open_positions": len(positions),
            }
        )
        previous_equity = equity
        minimum_equity = min(minimum_equity, equity)

    final_date = calendar[-1]
    for symbol in list(positions):
        position = positions[symbol]
        row = row_at(markets, symbol, final_date)
        price = float(row["close"]) if row is not None else position.plan.entry
        delta, _ = exit_quantity(
            position,
            final_date,
            position.remaining_quantity,
            price,
            "END_OF_FOLD",
            cost_rate,
        )
        cash += delta
        trades.append(trade_record(position))
        del positions[symbol]
    if daily:
        prior = float(daily[-2]["equity"]) if len(daily) > 1 else 1.0
        daily[-1].update(
            {
                "return": cash / prior - 1.0,
                "equity": cash,
                "cash": cash,
                "gross_exposure": 0.0,
                "open_positions": 0,
            }
        )
    trade_frame = pd.DataFrame(trades)
    reconciled = 1.0 + float(trade_frame["net_pnl"].sum()) if not trade_frame.empty else 1.0
    return (
        pd.DataFrame(daily).set_index("date"),
        trade_frame,
        {
            "minimum_equity": minimum_equity,
            "maximum_initial_gross": maximum_initial_gross,
            "final_equity": cash,
            "reconciled_equity": reconciled,
            "reconciliation_error": abs(cash - reconciled),
        },
    )


def bootstrap_lower(values: pd.Series) -> float:
    clean = values.dropna().to_numpy(dtype=float)
    if len(clean) == 0:
        return 0.0
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    samples = generator.choice(clean, size=(BOOTSTRAP_SAMPLES, len(clean)), replace=True)
    return float(np.quantile(samples.mean(axis=1), 0.05))


def truncate(
    markets: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    end: pd.Timestamp,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    return (
        {symbol: frame.loc[frame.index < end].copy() for symbol, frame in markets.items()},
        {symbol: series.loc[series.index < end].copy() for symbol, series in funding.items()},
    )


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
    markets, funding = load_inputs(args.data_root)
    last_date = max(frame.index.max() for frame in markets.values())
    paths: list[pd.DataFrame] = []
    trades: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    for year in range(FIRST_OOS_YEAR, int(last_date.year) + 1):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = min(pd.Timestamp(f"{year + 1}-01-01", tz="UTC"), last_date + pd.Timedelta(days=1))
        bounded_markets, bounded_funding = truncate(markets, funding, end)
        path, ledger, diagnostics = simulate(
            bounded_markets,
            bounded_funding,
            STRESS_COST,
            start,
        )
        path = path.loc[(path.index >= start) & (path.index < end)].copy()
        path["fold_year"] = year
        if not ledger.empty:
            entry_times = pd.to_datetime(ledger["entry_time"], utc=True)
            ledger = ledger.loc[(entry_times >= start) & (entry_times < end)].copy()
            ledger["fold_year"] = year
            trades.append(ledger)
        paths.append(path)
        fold_rows.append(
            {
                "year": year,
                **path_metrics(path),
                **trade_metrics(ledger),
                **diagnostics,
            }
        )
    combined_path = pd.concat(paths).sort_index()
    combined_trades = pd.concat(trades, ignore_index=True)
    folds = pd.DataFrame(fold_rows)
    overall = {**path_metrics(combined_path), **trade_metrics(combined_trades)}
    entry_times = pd.to_datetime(combined_trades["entry_time"], utc=True)
    recent_trades = combined_trades.loc[entry_times >= pd.Timestamp("2025-01-01", tz="UTC")]
    recent_path = combined_path.loc[combined_path.index >= pd.Timestamp("2025-01-01", tz="UTC")]
    recent = {**path_metrics(recent_path), **trade_metrics(recent_trades)}
    lower = bootstrap_lower(combined_trades["realized_r"])
    completed = folds.loc[folds["days"] >= 360]
    checks = {
        "oos_trades_ge_160": bool(overall["trades"] >= 160),
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
        "experiment": "CORE4_CLEANROOM_SYMMETRIC_FUTURES_V4",
        "selection_uses_future": False,
        "oos_start": combined_path.index.min(),
        "oos_end": combined_path.index.max(),
        "overall": overall,
        "recent_2025_plus": recent,
        "bootstrap_mean_r_lower_5pct": lower,
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "decision": "SECOND_AUDIT_REQUIRED" if all(checks.values()) else "REJECT_V4",
        "production_changed": False,
    }
    combined_path.to_csv(args.report_root / "oos_daily_path.csv")
    combined_trades.to_csv(args.report_root / "oos_trades.csv", index=False)
    folds.to_csv(args.report_root / "oos_folds.csv", index=False)
    by_side_rows = []
    for side, group in combined_trades.groupby("side"):
        by_side_rows.append({"side": side, **trade_metrics(group)})
    by_symbol_rows = []
    for symbol, group in combined_trades.groupby("symbol"):
        by_symbol_rows.append({"symbol": symbol, **trade_metrics(group)})
    pd.DataFrame(by_side_rows).to_csv(args.report_root / "oos_by_side.csv", index=False)
    pd.DataFrame(by_symbol_rows).to_csv(args.report_root / "oos_by_symbol.csv", index=False)
    (args.report_root / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, default=str),
        encoding="utf-8",
    )
    lines = [
        "# CORE4 Symmetric Futures V4",
        "",
        f"**{verdict['decision']}**",
        "",
        "Annual reset OOS simulation, explicit LONG/SHORT SL and TP1/TP2/TP3,",
        "28-day maximum hold, realized funding, and 20 bps one-way stress cost.",
        "",
        "| Scope | Trades | Win | PF | Mean R | Return | CAGR | Sharpe | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| OOS full | {overall['trades']} | {overall['win_rate']:.1%} | {overall['profit_factor']:.3f} | {overall['mean_r']:.3f} | {overall['total_return']:.2%} | {overall['cagr']:.2%} | {overall['sharpe']:.3f} | {overall['max_drawdown']:.2%} |",
        f"| 2025+ | {recent['trades']} | {recent['win_rate']:.1%} | {recent['profit_factor']:.3f} | {recent['mean_r']:.3f} | {recent['total_return']:.2%} | {recent['cagr']:.2%} | {recent['sharpe']:.3f} | {recent['max_drawdown']:.2%} |",
        "",
        f"Bootstrap 5% lower mean-R bound: {lower:.3f}.",
        f"Gate: {sum(checks.values())}/{len(checks)} passed.",
        "Funding is aggregated by UTC day and valued at the daily close; this",
        "approximation would require event-time marks in any second audit.",
        "No Telegram, Render, paper alert, or live order was enabled.",
    ]
    (args.report_root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, default=str), flush=True)
    print(folds.to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
