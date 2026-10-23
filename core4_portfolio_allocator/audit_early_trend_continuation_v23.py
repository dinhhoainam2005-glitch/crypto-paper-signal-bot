"""Finite causal audit for the CORE4 V23 4h continuation challenger."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_execution_timing_v7 import load_exact_inputs, row_at
from audit_full_history_tournament_v18 import json_safe, trim_result
from audit_regime_allocator_v19 import build_context
from backtest_allocator import SYMBOLS
from backtest_risk_defined import path_metrics, trade_metrics, true_range
from backtest_symmetric_v4 import (
    DATA_ROOT,
    FuturesPosition,
    exit_quantity,
    process_intraday,
    process_open,
    trade_record,
)
from signal_contract import TradePlan


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "early_trend_continuation_v23"
START = pd.Timestamp("2021-01-01", tz="UTC")
DEVELOPMENT_END = pd.Timestamp("2024-01-01", tz="UTC")
VALIDATION_END = pd.Timestamp("2025-01-01", tz="UTC")
BASE_COST = 0.0020
STRESS_COST = 0.0030
RISK_PER_TRADE = 0.0010
MAX_SYMBOL_NOTIONAL = 0.15
MAX_INITIAL_GROSS = 0.40
MAX_HOLD_HOURS = 10 * 24
ATR_MULTIPLIER = 2.0
TP_MULTIPLES = (1.0, 2.0, 3.0)
TP_FRACTIONS = (0.25, 0.25, 0.50)


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    parameter: int
    confirmations: int


CANDIDATES = tuple(
    [
        Candidate(f"DONCHIAN_{lookback}_C{confirmations}", "DONCHIAN", lookback, confirmations)
        for lookback in (6, 12, 18)
        for confirmations in (1, 2)
    ]
    + [
        Candidate(f"EMA_RECLAIM_{span}_C{confirmations}", "EMA_RECLAIM", span, confirmations)
        for span in (20, 40)
        for confirmations in (1, 2)
    ]
)


def build_four_hour(hourly: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    for symbol, source in hourly.items():
        counts = source["close"].resample("4h").count()
        frame = source.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}
        )
        frame = frame.loc[counts.eq(4)].dropna().copy()
        frame["atr20"] = true_range(frame).rolling(20, min_periods=20).mean()
        for lookback in (6, 12, 18):
            frame[f"high_{lookback}"] = frame["high"].shift(1).rolling(lookback).max()
            frame[f"low_{lookback}"] = frame["low"].shift(1).rolling(lookback).min()
        for span in (20, 40):
            frame[f"ema_{span}"] = frame["close"].ewm(span=span, adjust=False).mean()
        output[symbol] = frame
    return output


def context_side(context: pd.DataFrame, signal_close: pd.Timestamp) -> str | None:
    decision_day = signal_close.floor("D") - pd.Timedelta(days=1)
    row = row_at(context, decision_day)
    if row is None:
        return None
    required = row[["side", "slope", "breadth"]]
    if required.isna().any() or not bool(row["trend_aligned"]):
        return None
    if float(row["breadth"]) < 2.0:
        return None
    return str(row["side"])


def raw_trigger(frame: pd.DataFrame, index: int, candidate: Candidate) -> str | None:
    if index < 2:
        return None
    row = frame.iloc[index]
    previous = frame.iloc[index - 1]
    if pd.isna(row["atr20"]):
        return None
    if candidate.family == "DONCHIAN":
        high = row[f"high_{candidate.parameter}"]
        low = row[f"low_{candidate.parameter}"]
        prior_high = previous[f"high_{candidate.parameter}"]
        prior_low = previous[f"low_{candidate.parameter}"]
        if pd.notna(high) and pd.notna(prior_high):
            if row["close"] > high and previous["close"] <= prior_high:
                return "LONG"
        if pd.notna(low) and pd.notna(prior_low):
            if row["close"] < low and previous["close"] >= prior_low:
                return "SHORT"
        return None
    ema = f"ema_{candidate.parameter}"
    if index < 6 or pd.isna(row[ema]) or pd.isna(previous[ema]):
        return None
    slope = float(row[ema] - frame.iloc[index - 6][ema])
    if (
        previous["close"] <= previous[ema]
        and row["close"] > row[ema]
        and row["close"] > previous["high"]
        and slope > 0.0
    ):
        return "LONG"
    if (
        previous["close"] >= previous[ema]
        and row["close"] < row[ema]
        and row["close"] < previous["low"]
        and slope < 0.0
    ):
        return "SHORT"
    return None


def build_signals(
    candidate: Candidate,
    four_hour: dict[str, pd.DataFrame],
    context: pd.DataFrame,
    entry_delay_bars: int = 0,
) -> dict[pd.Timestamp, list[tuple[str, str, float]]]:
    raw: dict[pd.Timestamp, list[tuple[str, str, float]]] = {}
    for symbol, frame in four_hour.items():
        for index in range(len(frame)):
            side = raw_trigger(frame, index, candidate)
            if side is None:
                continue
            signal_close = frame.index[index] + pd.Timedelta(hours=4)
            if context_side(context, signal_close) != side:
                continue
            entry_time = signal_close + pd.Timedelta(hours=4 * entry_delay_bars)
            raw.setdefault(entry_time, []).append((symbol, side, float(frame.iloc[index]["atr20"])))
    confirmed: dict[pd.Timestamp, list[tuple[str, str, float]]] = {}
    for timestamp, items in raw.items():
        side_counts = {
            side: len({symbol for symbol, item_side, _ in items if item_side == side})
            for side in ("LONG", "SHORT")
        }
        selected = [item for item in items if side_counts[item[1]] >= candidate.confirmations]
        if selected:
            confirmed[timestamp] = selected
    return confirmed


def build_plan(symbol: str, side: str, timestamp: pd.Timestamp, entry: float, atr: float) -> TradePlan:
    distance = ATR_MULTIPLIER * atr
    if not math.isfinite(distance) or distance <= 0.0 or distance >= entry:
        raise ValueError("invalid 4h ATR distance")
    sign = 1.0 if side == "LONG" else -1.0
    if side == "SHORT" and entry - TP_MULTIPLES[-1] * distance <= 0.0:
        raise ValueError("short TP3 must remain positive")
    plan = TradePlan(
        symbol=symbol,
        side=side,
        timeframe="4h",
        generated_at_utc=timestamp.to_pydatetime(),
        entry=entry,
        stop_loss=entry - sign * distance,
        take_profit_1=entry + sign * TP_MULTIPLES[0] * distance,
        take_profit_2=entry + sign * TP_MULTIPLES[1] * distance,
        take_profit_3=entry + sign * TP_MULTIPLES[2] * distance,
        invalid_after_utc=(timestamp + pd.Timedelta(hours=4)).to_pydatetime(),
        max_holding_hours=MAX_HOLD_HOURS,
        review_interval_hours=4,
        capital_at_risk_fraction=RISK_PER_TRADE,
        early_exit_condition="D1 TREND_BREADTH2 invalid or 4h close crosses EMA20",
        paper_only=True,
    )
    plan.validate()
    return plan


def exit_required(
    four_hour: dict[str, pd.DataFrame],
    context: pd.DataFrame,
    symbol: str,
    timestamp: pd.Timestamp,
    side: str,
) -> bool:
    if timestamp.hour % 4 != 0:
        return False
    if context_side(context, timestamp) != side:
        return True
    frame = four_hour[symbol]
    last_bar = timestamp - pd.Timedelta(hours=4)
    row = row_at(frame, last_bar)
    if row is None or pd.isna(row["ema_20"]):
        return False
    return bool(row["close"] < row["ema_20"]) if side == "LONG" else bool(row["close"] > row["ema_20"])


def simulate(
    candidate: Candidate,
    hourly: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    four_hour: dict[str, pd.DataFrame],
    context: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_rate: float = BASE_COST,
    entry_delay_bars: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int]]:
    signals = build_signals(candidate, four_hour, context, entry_delay_bars)
    calendar = pd.date_range(start, end - pd.Timedelta(hours=1), freq="1h", tz="UTC")
    cash = 1.0
    previous_equity = 1.0
    positions: dict[str, FuturesPosition] = {}
    trades: list[dict[str, Any]] = []
    path: list[dict[str, Any]] = []
    maximum_initial_gross = 0.0

    def finish(symbol: str) -> None:
        record = trade_record(positions[symbol])
        record["timeframe"] = "4h"
        record["candidate"] = candidate.name
        trades.append(record)
        del positions[symbol]

    for calendar_index, timestamp in enumerate(calendar):
        rows = {symbol: row_at(hourly[symbol], timestamp) for symbol in SYMBOLS}
        for symbol in list(positions):
            position = positions[symbol]
            row = rows[symbol]
            if row is None:
                continue
            held = (timestamp - pd.Timestamp(position.plan.generated_at_utc)).total_seconds() / 3600.0
            reason = None
            if held >= MAX_HOLD_HOURS:
                reason = "MAX_HOLD"
            elif exit_required(four_hour, context, symbol, timestamp, position.plan.side):
                reason = "SIGNAL_INVALID"
            if reason is not None:
                delta, _ = exit_quantity(
                    position, timestamp, position.remaining_quantity, float(row["open"]), reason, cost_rate
                )
                cash += delta
            else:
                delta, _ = process_open(position, timestamp, float(row["open"]), cost_rate)
                cash += delta
            if position.remaining_quantity <= 1e-12:
                finish(symbol)

        requests: list[tuple[str, FuturesPosition, float]] = []
        for symbol, side, atr in signals.get(timestamp, []):
            row = rows[symbol]
            if symbol in positions or row is None:
                continue
            try:
                plan = build_plan(symbol, side, timestamp, float(row["open"]), atr)
            except ValueError:
                continue
            distance_fraction = abs(plan.entry - plan.stop_loss) / plan.entry
            weight = min(MAX_SYMBOL_NOTIONAL, RISK_PER_TRADE / distance_fraction)
            requests.append(
                (
                    symbol,
                    FuturesPosition(
                        plan=plan,
                        initial_quantity=0.0,
                        remaining_quantity=0.0,
                        initial_notional=0.0,
                        initial_risk_cash=0.0,
                        entry_fee=0.0,
                        current_stop=plan.stop_loss,
                        tp_fractions=TP_FRACTIONS,
                    ),
                    weight,
                )
            )

        if requests:
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
            requested = sum(weight for _, _, weight in requests)
            available = max(0.0, MAX_INITIAL_GROSS - existing_gross)
            scale = min(1.0, available / requested) if requested > 0.0 else 0.0
            initial_gross = existing_gross
            for symbol, position, weight in requests:
                notional = equity_at_open * weight * scale
                if notional <= 0.0:
                    continue
                quantity = notional / position.plan.entry
                fee = notional * cost_rate
                cash -= fee
                position.initial_quantity = quantity
                position.remaining_quantity = quantity
                position.initial_notional = notional
                position.initial_risk_cash = quantity * abs(position.plan.entry - position.plan.stop_loss)
                position.entry_fee = fee
                positions[symbol] = position
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
                position, timestamp, float(row["low"]), float(row["high"]), cost_rate
            )
            cash += delta
            if position.remaining_quantity <= 1e-12:
                finish(symbol)

        is_day_end = calendar_index == len(calendar) - 1 or calendar[calendar_index + 1].floor("D") != timestamp.floor("D")
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
                    "date": timestamp.floor("D"),
                    "return": equity / previous_equity - 1.0,
                    "equity": equity,
                    "cash": cash,
                    "gross_exposure": gross / equity if equity > 0.0 else math.inf,
                    "open_positions": len(positions),
                }
            )
            previous_equity = equity

    final_timestamp = calendar[-1]
    for symbol in list(positions):
        position = positions[symbol]
        row = row_at(hourly[symbol], final_timestamp)
        price = float(row["close"]) if row is not None else position.plan.entry
        delta, _ = exit_quantity(
            position, final_timestamp, position.remaining_quantity, price, "END_OF_AUDIT", cost_rate
        )
        cash += delta
        finish(symbol)
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
            "signals": sum(len(items) for items in signals.values()),
            "maximum_initial_gross": maximum_initial_gross,
            "final_equity": cash,
            "reconciled_equity": reconciled,
            "reconciliation_error": abs(cash - reconciled),
        },
    )


def summarize(path: pd.DataFrame, trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    scoped_path, scoped_trades = trim_result(path, trades, start, end)
    metrics = {**path_metrics(scoped_path), **trade_metrics(scoped_trades)}
    weeks = max((end - start).total_seconds() / (7.0 * 86_400.0), 1.0 / 7.0)
    metrics["trades_per_week"] = metrics["trades"] / weeks
    return metrics


def selection_checks(development: dict[str, Any], validation: dict[str, Any]) -> dict[str, bool]:
    return {
        "development_trades_ge_120": development["trades"] >= 120,
        "development_frequency_ge_1": development["trades_per_week"] >= 1.0,
        "development_win_ge_50pct": development["win_rate"] >= 0.50,
        "development_pf_ge_1p25": development["profit_factor"] >= 1.25,
        "development_mean_r_positive": development["mean_r"] > 0.0,
        "development_sharpe_ge_0p75": development["sharpe"] >= 0.75,
        "development_dd_le_8pct": development["max_drawdown"] >= -0.08,
        "validation_trades_ge_35": validation["trades"] >= 35,
        "validation_frequency_ge_0p75": validation["trades_per_week"] >= 0.75,
        "validation_win_ge_50pct": validation["win_rate"] >= 0.50,
        "validation_pf_ge_1p20": validation["profit_factor"] >= 1.20,
        "validation_mean_r_positive": validation["mean_r"] > 0.0,
        "validation_sharpe_ge_0p50": validation["sharpe"] >= 0.50,
        "validation_dd_le_6pct": validation["max_drawdown"] >= -0.06,
    }


def replacement_checks(
    full: dict[str, Any], frozen: dict[str, Any], combined_stress: dict[str, Any]
) -> dict[str, bool]:
    return {
        "frequency_ge_1_per_week": full["trades_per_week"] >= 1.0,
        "win_ge_52pct": full["win_rate"] >= 0.52,
        "pf_ge_1p50": full["profit_factor"] >= 1.50,
        "mean_r_ge_0p15": full["mean_r"] >= 0.15,
        "sharpe_ge_1": full["sharpe"] >= 1.0,
        "dd_le_6pct": full["max_drawdown"] >= -0.06,
        "frozen_trades_ge_50": frozen["trades"] >= 50,
        "frozen_pf_ge_1p20": frozen["profit_factor"] >= 1.20,
        "frozen_mean_r_positive": frozen["mean_r"] > 0.0,
        "stress_pf_ge_1p25": combined_stress["profit_factor"] >= 1.25,
        "stress_mean_r_positive": combined_stress["mean_r"] > 0.0,
        "stress_dd_le_7pct": combined_stress["max_drawdown"] >= -0.07,
    }


def main() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    daily, hourly, funding = load_exact_inputs(DATA_ROOT)
    four_hour = build_four_hour(hourly)
    context = build_context(daily)
    end = min(frame.index.max().floor("D") for frame in hourly.values()) + pd.Timedelta(days=1)
    periods = {
        "DEVELOPMENT_2021_2023": (START, DEVELOPMENT_END),
        "VALIDATION_2024": (DEVELOPMENT_END, VALIDATION_END),
        "FROZEN_2025_PLUS": (VALIDATION_END, end),
        "FULL": (START, end),
    }
    results: list[dict[str, Any]] = []
    raw_results: dict[str, tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]] = {}
    for candidate in CANDIDATES:
        print(f"V23 {candidate.name}", flush=True)
        path, trades, diagnostics = simulate(
            candidate, hourly, funding, four_hour, context, START, end
        )
        raw_results[candidate.name] = (path, trades, diagnostics)
        metrics = {
            name: summarize(path, trades, period_start, period_end)
            for name, (period_start, period_end) in periods.items()
        }
        checks = selection_checks(
            metrics["DEVELOPMENT_2021_2023"], metrics["VALIDATION_2024"]
        )
        results.append(
            {
                "candidate": asdict(candidate),
                "metrics": metrics,
                "diagnostics": diagnostics,
                "selection_checks": checks,
                "selection_pass": all(checks.values()),
            }
        )

    eligible = [item for item in results if item["selection_pass"]]
    ranked = eligible if eligible else results
    selected = max(
        ranked,
        key=lambda item: (
            item["selection_pass"],
            sum(item["selection_checks"].values()),
            item["metrics"]["VALIDATION_2024"]["profit_factor"],
            item["metrics"]["VALIDATION_2024"]["sharpe"],
            item["metrics"]["DEVELOPMENT_2021_2023"]["profit_factor"],
        ),
    )
    selected_spec = Candidate(**selected["candidate"])
    stress_path, stress_trades, stress_diagnostics = simulate(
        selected_spec,
        hourly,
        funding,
        four_hour,
        context,
        START,
        end,
        cost_rate=STRESS_COST,
        entry_delay_bars=1,
    )
    stress_metrics = summarize(stress_path, stress_trades, START, end)
    final_checks = replacement_checks(
        selected["metrics"]["FULL"],
        selected["metrics"]["FROZEN_2025_PLUS"],
        stress_metrics,
    )
    historical_pass = bool(selected["selection_pass"] and all(final_checks.values()))
    path, trades, _ = raw_results[selected_spec.name]
    path.to_csv(REPORT_ROOT / "selected_path.csv")
    trades.to_csv(REPORT_ROOT / "selected_trades.csv", index=False)
    stress_path.to_csv(REPORT_ROOT / "selected_combined_stress_path.csv")
    stress_trades.to_csv(REPORT_ROOT / "selected_combined_stress_trades.csv", index=False)

    summary_rows: list[dict[str, Any]] = []
    for item in results:
        row = {"candidate": item["candidate"]["name"], "selection_pass": item["selection_pass"]}
        for period, metrics in item["metrics"].items():
            for key in ("trades", "trades_per_week", "win_rate", "profit_factor", "mean_r", "sharpe", "max_drawdown"):
                row[f"{period}_{key}"] = metrics[key]
        row["selection_gates"] = sum(item["selection_checks"].values())
        row["selection_gates_total"] = len(item["selection_checks"])
        summary_rows.append(row)
    pd.DataFrame(summary_rows).to_csv(REPORT_ROOT / "candidate_summary.csv", index=False)

    verdict = {
        "experiment": "CORE4_V23_EARLY_TREND_CONTINUATION",
        "period": f"{START.date()}/{(end - pd.Timedelta(days=1)).date()}",
        "candidate_count": len(CANDIDATES),
        "selected_candidate": selected_spec.name,
        "selected_from_eligible_set": bool(eligible),
        "selected_metrics": selected["metrics"],
        "selection_checks": selected["selection_checks"],
        "replacement_checks": final_checks,
        "combined_stress": {
            "cost_rate": STRESS_COST,
            "entry_delay_bars": 1,
            "metrics": stress_metrics,
            "diagnostics": stress_diagnostics,
        },
        "historical_gate_pass": historical_pass,
        "decision": (
            "AUTHORIZE_SEPARATE_V23_PAPER_SHADOW"
            if historical_pass
            else "REJECT_V23_KEEP_V20_UNCHANGED"
        ),
        "runtime_changed": False,
        "real_money_authorized": False,
        "candidates": results,
    }
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )
    lines = [
        "# CORE4 V23 EARLY TREND CONTINUATION",
        "",
        f"**{verdict['decision']}**",
        "",
        f"Selected: `{selected_spec.name}` from {len(CANDIDATES)} preregistered candidates.",
        f"Selection gate: {sum(selected['selection_checks'].values())}/{len(selected['selection_checks'])}.",
        f"Replacement gate: {sum(final_checks.values())}/{len(final_checks)}.",
        "",
        "| Period | Trades/wk | Trades | Win | PF | Mean R | Sharpe | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for period, metrics in selected["metrics"].items():
        lines.append(
            f"| {period} | {metrics['trades_per_week']:.3f} | {metrics['trades']} | "
            f"{metrics['win_rate']:.1%} | {metrics['profit_factor']:.3f} | "
            f"{metrics['mean_r']:.3f} | {metrics['sharpe']:.3f} | {metrics['max_drawdown']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## Combined stress",
            "",
            f"30 bps per transaction plus one 4h entry delay: {stress_metrics['trades_per_week']:.3f}/week, "
            f"Win {stress_metrics['win_rate']:.1%}, PF {stress_metrics['profit_factor']:.3f}, "
            f"Mean {stress_metrics['mean_r']:.3f}R, Sharpe {stress_metrics['sharpe']:.3f}, "
            f"Max DD {stress_metrics['max_drawdown']:.2%}.",
            "",
            "V20 production and real-money locks were not changed.",
        ]
    )
    (REPORT_ROOT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_safe({key: value for key, value in verdict.items() if key != "candidates"}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
