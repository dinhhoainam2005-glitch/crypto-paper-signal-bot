"""Exact portfolio simulation for V24 staged early execution."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from audit_beta_regime_v7 import audit_exit, enrich
from audit_early_execution_overlay_v24 import (
    COST_RATE,
    POLICY,
    START,
    build_event_frame,
    daily_decisions,
    first_intraday_events,
)
from audit_early_trend_continuation_v23 import build_four_hour
from audit_execution_timing_v7 import load_exact_inputs, row_at
from audit_full_history_tournament_v18 import json_safe, period_metrics
from audit_regime_allocator_v19 import build_context
from backtest_allocator import SYMBOLS
from backtest_breakout_v5 import (
    MAX_HOLD_DAYS,
    MAX_INITIAL_GROSS,
    MAX_SYMBOL_NOTIONAL,
    RISK_PER_TRADE,
    TP_FRACTIONS,
    TP_R_MULTIPLES,
)
from backtest_symmetric_v4 import (
    DATA_ROOT,
    FuturesPosition,
    build_plan,
    exit_quantity,
    process_intraday,
    process_open,
    trade_record,
)


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "early_execution_tranche_v24"
V20_REPORT_ROOT = ROOT / "reports" / "trend_breadth_robustness_v20"
DEVELOPMENT_END = pd.Timestamp("2024-01-01", tz="UTC")
VALIDATION_END = pd.Timestamp("2025-01-01", tz="UTC")
STRESS_COST = 0.0030
CORE_WEIGHT_WITH_FAST = 0.60
FAST_WEIGHT = 1.0 - CORE_WEIGHT_WITH_FAST
FRACTIONS = (0.25, 0.50)


@dataclass(frozen=True)
class TrancheSpec:
    name: str
    early_fraction: float


SPECS = tuple(TrancheSpec(f"EARLY_{fraction:.0%}", fraction) for fraction in FRACTIONS)


def active_keys(
    metadata: dict[tuple[str, str, str, str], dict[str, Any]], sleeve: str, symbol: str
) -> list[tuple[str, str, str, str]]:
    return [key for key, item in metadata.items() if item["sleeve"] == sleeve and item["symbol"] == symbol]


def aggregate_clusters(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    rows: list[dict[str, Any]] = []
    for cluster_id, group in trades.groupby("cluster_id", sort=True):
        risk = float(group["initial_risk_cash"].sum())
        net = float(group["net_pnl"].sum())
        entry_time = pd.to_datetime(group["entry_time"], utc=True).min()
        exit_time = pd.to_datetime(group["exit_time"], utc=True).max()
        rows.append(
            {
                "cluster_id": cluster_id,
                "sleeve": group.iloc[0]["sleeve"],
                "symbol": group.iloc[0]["symbol"],
                "side": group.iloc[0]["side"],
                "entry_time": entry_time,
                "exit_time": exit_time,
                "holding_days": (exit_time - entry_time).total_seconds() / 86_400.0,
                "net_pnl": net,
                "initial_risk_cash": risk,
                "realized_r": net / risk if risk > 0.0 else 0.0,
                "tranches": len(group),
                "had_early": bool((group["tranche"] == "EARLY").any()),
                "confirmed": bool(group["confirmed"].any()),
            }
        )
    return pd.DataFrame(rows)


def simulate(
    spec: TrancheSpec,
    core_daily: dict[str, pd.DataFrame],
    fast_daily: dict[str, pd.DataFrame],
    hourly: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    context: pd.DataFrame,
    early_events: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_rate: float = COST_RATE,
    early_delay_hours: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    event_map: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for row in early_events.to_dict("records"):
        timestamp = pd.Timestamp(row["early_entry_time"]) + pd.Timedelta(hours=early_delay_hours)
        daily_time = pd.Timestamp(row["daily_entry_time"])
        if timestamp >= daily_time:
            continue
        item = dict(row)
        item["early_entry_time"] = timestamp
        event_map.setdefault(timestamp, []).append(item)

    calendar = pd.date_range(start, end - pd.Timedelta(hours=1), freq="1h", tz="UTC")
    cash = 1.0
    previous_equity = 1.0
    positions: dict[tuple[str, str, str, str], FuturesPosition] = {}
    metadata: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    path: list[dict[str, Any]] = []
    maximum_initial_gross = 0.0

    def finish(key: tuple[str, str, str, str]) -> None:
        item = metadata[key]
        record = trade_record(positions[key])
        record.update(
            {
                "sleeve": item["sleeve"],
                "cluster_id": item["cluster_id"],
                "tranche": item["tranche"],
                "confirmed": item["confirmed"],
            }
        )
        records.append(record)
        del positions[key]
        del metadata[key]

    def open_requests(requests: list[dict[str, Any]], rows: dict[str, pd.Series | None]) -> None:
        nonlocal cash, maximum_initial_gross
        if not requests:
            return
        unrealized = sum(
            position.direction
            * position.remaining_quantity
            * (float(rows[metadata[key]["symbol"]]["open"]) - position.plan.entry)
            for key, position in positions.items()
            if rows[metadata[key]["symbol"]] is not None
        )
        equity_at_open = cash + unrealized
        existing_gross = sum(
            position.remaining_quantity * float(rows[metadata[key]["symbol"]]["open"])
            for key, position in positions.items()
            if rows[metadata[key]["symbol"]] is not None
        ) / equity_at_open
        requested_total = sum(item["weight"] for item in requests)
        available = max(0.0, MAX_INITIAL_GROSS - existing_gross)
        scale = min(1.0, available / requested_total) if requested_total > 0.0 else 0.0
        initial_gross = existing_gross
        for item in requests:
            notional = equity_at_open * item["weight"] * scale
            if notional <= 0.0:
                continue
            position = item["position"]
            quantity = notional / position.plan.entry
            fee = notional * cost_rate
            cash -= fee
            position.initial_quantity = quantity
            position.remaining_quantity = quantity
            position.initial_notional = notional
            position.initial_risk_cash = quantity * abs(position.plan.entry - position.plan.stop_loss)
            position.entry_fee = fee
            positions[item["key"]] = position
            metadata[item["key"]] = item["metadata"]
            initial_gross += notional / equity_at_open
        maximum_initial_gross = max(maximum_initial_gross, initial_gross)

    for calendar_index, timestamp in enumerate(calendar):
        day = timestamp.floor("D")
        decision_day = day - pd.Timedelta(days=1)
        rows = {symbol: row_at(hourly[symbol], timestamp) for symbol in SYMBOLS}
        decisions = daily_decisions(core_daily, fast_daily, context, decision_day) if timestamp.hour == 0 else None

        for key in list(positions):
            position = positions[key]
            item = metadata[key]
            symbol = item["symbol"]
            row = rows[symbol]
            if row is None:
                continue
            reason = None
            if timestamp.hour == 0 and item["tranche"] == "EARLY" and not item["confirmed"]:
                confirmed = (symbol, position.plan.side) in decisions[item["sleeve"]]
                if confirmed:
                    item["confirmed"] = True
                else:
                    reason = "FALSE_BREAK"
            if reason is None and item["confirmed"] and timestamp.hour == 0:
                held = (timestamp - pd.Timestamp(position.plan.generated_at_utc)).total_seconds() / 86_400.0
                source = core_daily if item["sleeve"] == "CORE" else fast_daily
                if held >= MAX_HOLD_DAYS:
                    reason = "MAX_HOLD"
                elif audit_exit(source, symbol, decision_day, position.plan.side):
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
                finish(key)

        requests: list[dict[str, Any]] = []
        if timestamp.hour == 0 and decisions is not None:
            fast_active = bool(decisions["FAST"])
            for sleeve in ("CORE", "FAST"):
                source = core_daily if sleeve == "CORE" else fast_daily
                target_scale = (CORE_WEIGHT_WITH_FAST if fast_active else 1.0) if sleeve == "CORE" else FAST_WEIGHT
                for symbol, side in decisions[sleeve]:
                    existing = active_keys(metadata, sleeve, symbol)
                    other_cluster = [key for key in existing if metadata[key]["signal_day"] != decision_day]
                    if other_cluster:
                        continue
                    cluster_id = f"{sleeve}:{symbol}:{side}:{decision_day.date()}"
                    active_early_budget = sum(
                        metadata[key]["risk_budget"]
                        for key in existing
                        if metadata[key]["cluster_id"] == cluster_id
                    )
                    risk_budget = max(0.0, RISK_PER_TRADE * target_scale - active_early_budget)
                    if risk_budget <= 1e-12 or rows[symbol] is None:
                        continue
                    decision_row = row_at(source[symbol], decision_day)
                    if decision_row is None or pd.isna(decision_row["atr20"]):
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
                            risk_fraction=risk_budget,
                        )
                    except ValueError:
                        continue
                    distance_fraction = abs(plan.entry - plan.stop_loss) / plan.entry
                    weight = min(MAX_SYMBOL_NOTIONAL * target_scale, risk_budget / distance_fraction)
                    key = (sleeve, symbol, cluster_id, "DAILY")
                    requests.append(
                        {
                            "key": key,
                            "position": FuturesPosition(
                                plan, 0.0, 0.0, 0.0, 0.0, 0.0, plan.stop_loss, tp_fractions=TP_FRACTIONS
                            ),
                            "weight": weight,
                            "metadata": {
                                "sleeve": sleeve,
                                "symbol": symbol,
                                "signal_day": decision_day,
                                "cluster_id": cluster_id,
                                "tranche": "DAILY",
                                "confirmed": True,
                                "risk_budget": risk_budget,
                            },
                        }
                    )

        for event in event_map.get(timestamp, []):
            sleeve = str(event["sleeve"])
            symbol = str(event["symbol"])
            side = str(event["side"])
            signal_day = pd.Timestamp(event["signal_day"])
            if active_keys(metadata, sleeve, symbol) or rows[symbol] is None:
                continue
            source = core_daily if sleeve == "CORE" else fast_daily
            prior_row = row_at(source[symbol], signal_day - pd.Timedelta(days=1))
            if prior_row is None or pd.isna(prior_row["atr20"]):
                continue
            sleeve_scale = 1.0 if sleeve == "CORE" else FAST_WEIGHT
            risk_budget = RISK_PER_TRADE * sleeve_scale * spec.early_fraction
            try:
                plan = build_plan(
                    symbol,
                    side,
                    timestamp,
                    float(rows[symbol]["open"]),
                    float(prior_row["atr20"]),
                    tp_r_multiples=TP_R_MULTIPLES,
                    max_hold_days=MAX_HOLD_DAYS,
                    risk_fraction=risk_budget,
                )
            except ValueError:
                continue
            cluster_id = f"{sleeve}:{symbol}:{side}:{signal_day.date()}"
            distance_fraction = abs(plan.entry - plan.stop_loss) / plan.entry
            weight = min(
                MAX_SYMBOL_NOTIONAL * sleeve_scale * spec.early_fraction,
                risk_budget / distance_fraction,
            )
            key = (sleeve, symbol, cluster_id, "EARLY")
            requests.append(
                {
                    "key": key,
                    "position": FuturesPosition(
                        plan, 0.0, 0.0, 0.0, 0.0, 0.0, plan.stop_loss, tp_fractions=TP_FRACTIONS
                    ),
                    "weight": weight,
                    "metadata": {
                        "sleeve": sleeve,
                        "symbol": symbol,
                        "signal_day": signal_day,
                        "cluster_id": cluster_id,
                        "tranche": "EARLY",
                        "confirmed": False,
                        "risk_budget": risk_budget,
                    },
                }
            )
        open_requests(requests, rows)

        for key, position in positions.items():
            symbol = metadata[key]["symbol"]
            row = rows[symbol]
            if row is None:
                continue
            rate = float(funding[symbol].get(timestamp, 0.0))
            funding_cash = -position.direction * position.remaining_quantity * float(row["open"]) * rate
            position.funding_pnl += funding_cash
            cash += funding_cash

        for key in list(positions):
            symbol = metadata[key]["symbol"]
            row = rows[symbol]
            if row is None:
                continue
            position = positions[key]
            delta, _ = process_intraday(
                position, timestamp, float(row["low"]), float(row["high"]), cost_rate
            )
            cash += delta
            if position.remaining_quantity <= 1e-12:
                finish(key)

        is_day_end = calendar_index == len(calendar) - 1 or calendar[calendar_index + 1].floor("D") != day
        if is_day_end:
            unrealized = 0.0
            gross = 0.0
            for key, position in positions.items():
                symbol = metadata[key]["symbol"]
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

    final_timestamp = calendar[-1]
    for key in list(positions):
        symbol = metadata[key]["symbol"]
        position = positions[key]
        row = row_at(hourly[symbol], final_timestamp)
        price = float(row["close"]) if row is not None else position.plan.entry
        delta, _ = exit_quantity(
            position, final_timestamp, position.remaining_quantity, price, "END_OF_AUDIT", cost_rate
        )
        cash += delta
        finish(key)
    if path:
        prior = float(path[-2]["equity"]) if len(path) > 1 else 1.0
        path[-1].update(
            {"return": cash / prior - 1.0, "equity": cash, "cash": cash, "gross_exposure": 0.0, "open_positions": 0}
        )
    tranche_ledger = pd.DataFrame(records)
    clusters = aggregate_clusters(tranche_ledger)
    reconciled = 1.0 + float(tranche_ledger["net_pnl"].sum()) if not tranche_ledger.empty else 1.0
    return (
        pd.DataFrame(path).set_index("date"),
        clusters,
        tranche_ledger,
        {
            "maximum_initial_gross": maximum_initial_gross,
            "final_equity": cash,
            "reconciled_equity": reconciled,
            "reconciliation_error": abs(cash - reconciled),
        },
    )


def load_v20() -> tuple[pd.DataFrame, pd.DataFrame]:
    path = pd.read_csv(V20_REPORT_ROOT / "base_path.csv", parse_dates=["date"]).set_index("date")
    path.index = pd.to_datetime(path.index, utc=True)
    trades = pd.read_csv(V20_REPORT_ROOT / "base_trades.csv")
    for column in ("entry_time", "exit_time"):
        trades[column] = pd.to_datetime(trades[column], utc=True)
    return path, trades


def selection_checks(
    baseline_dev: dict[str, Any],
    baseline_val: dict[str, Any],
    candidate_dev: dict[str, Any],
    candidate_val: dict[str, Any],
) -> dict[str, bool]:
    return {
        "development_pf_floor": candidate_dev["profit_factor"] >= 0.90 * baseline_dev["profit_factor"],
        "development_sharpe_floor": candidate_dev["sharpe"] >= 0.90 * baseline_dev["sharpe"],
        "development_return_positive": candidate_dev["total_return"] > 0.0,
        "validation_pf_floor": candidate_val["profit_factor"] >= 0.90 * baseline_val["profit_factor"],
        "validation_sharpe_floor": candidate_val["sharpe"] >= 0.90 * baseline_val["sharpe"],
        "validation_return_not_worse": candidate_val["total_return"] >= baseline_val["total_return"],
        "validation_dd_not_25pct_worse": candidate_val["max_drawdown"] >= 1.25 * baseline_val["max_drawdown"],
    }


def replacement_checks(
    baseline: dict[str, Any], candidate: dict[str, Any], frozen: dict[str, Any], stress: dict[str, Any]
) -> dict[str, bool]:
    return {
        "frequency_improves_10pct": candidate["trades_per_week"] >= 1.10 * baseline["trades_per_week"],
        "win_within_2pct": candidate["win_rate"] >= baseline["win_rate"] - 0.02,
        "pf_not_worse": candidate["profit_factor"] >= baseline["profit_factor"],
        "mean_r_not_worse": candidate["mean_r"] >= baseline["mean_r"],
        "return_not_worse": candidate["total_return"] >= baseline["total_return"],
        "sharpe_not_worse": candidate["sharpe"] >= baseline["sharpe"],
        "drawdown_not_worse": candidate["max_drawdown"] >= baseline["max_drawdown"],
        "frozen_pf_ge_1p20": frozen["profit_factor"] >= 1.20,
        "frozen_return_positive": frozen["total_return"] > 0.0,
        "stress_pf_ge_1p30": stress["profit_factor"] >= 1.30,
        "stress_return_positive": stress["total_return"] > 0.0,
        "stress_dd_le_7pct": stress["max_drawdown"] >= -0.07,
    }


def main() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    official, hourly, funding = load_exact_inputs(DATA_ROOT)
    raw = {
        symbol: frame[["open", "high", "low", "close", "atr20"]].copy()
        for symbol, frame in official.items()
    }
    core_daily = enrich(raw, entry_channel=55, exit_channel=20, sma_days=200)
    fast_daily = enrich(raw, entry_channel=20, exit_channel=10, sma_days=200)
    context = build_context(core_daily)
    four_hour = build_four_hour(hourly)
    end = min(frame.index.max().floor("D") for frame in hourly.values()) + pd.Timedelta(days=1)
    sources = {"CORE": core_daily, "FAST": fast_daily}
    intraday = first_intraday_events(sources, four_hour, START, end)
    from audit_early_execution_overlay_v24 import Configuration

    routed = Configuration("ROUTED_C1", True, 1)
    early_events, _ = build_event_frame(
        routed, core_daily, fast_daily, hourly, context, intraday, START, end
    )
    baseline_path, baseline_trades = load_v20()
    periods = {
        "DEVELOPMENT_2021_2023": (START, DEVELOPMENT_END),
        "VALIDATION_2024": (DEVELOPMENT_END, VALIDATION_END),
        "FROZEN_2025_PLUS": (VALIDATION_END, end),
        "FULL": (START, end),
    }
    baseline_metrics = {
        name: period_metrics(baseline_path, baseline_trades, period_start, period_end)["full"]
        for name, (period_start, period_end) in periods.items()
    }
    results: list[dict[str, Any]] = []
    raw_results: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]] = {}
    for spec in SPECS:
        print(f"V24 TRANCHE {spec.name}", flush=True)
        path, clusters, tranches, diagnostics = simulate(
            spec, core_daily, fast_daily, hourly, funding, context, early_events, START, end
        )
        raw_results[spec.name] = (path, clusters, tranches, diagnostics)
        metrics_by_period = {
            name: period_metrics(path, clusters, period_start, period_end)["full"]
            for name, (period_start, period_end) in periods.items()
        }
        checks = selection_checks(
            baseline_metrics["DEVELOPMENT_2021_2023"],
            baseline_metrics["VALIDATION_2024"],
            metrics_by_period["DEVELOPMENT_2021_2023"],
            metrics_by_period["VALIDATION_2024"],
        )
        results.append(
            {
                "spec": {"name": spec.name, "early_fraction": spec.early_fraction},
                "metrics": metrics_by_period,
                "diagnostics": diagnostics,
                "selection_checks": checks,
                "selection_pass": all(checks.values()),
            }
        )
    eligible = [item for item in results if item["selection_pass"]]
    selected = max(
        eligible if eligible else results,
        key=lambda item: (
            item["selection_pass"],
            sum(item["selection_checks"].values()),
            item["metrics"]["VALIDATION_2024"]["sharpe"],
            item["metrics"]["VALIDATION_2024"]["profit_factor"],
        ),
    )
    selected_spec = next(spec for spec in SPECS if spec.name == selected["spec"]["name"])
    print(f"V24 STRESS {selected_spec.name}", flush=True)
    stress_path, stress_clusters, stress_tranches, stress_diagnostics = simulate(
        selected_spec,
        core_daily,
        fast_daily,
        hourly,
        funding,
        context,
        early_events,
        START,
        end,
        cost_rate=STRESS_COST,
        early_delay_hours=4,
    )
    stress_metrics = period_metrics(stress_path, stress_clusters, START, end)["full"]
    final_checks = replacement_checks(
        baseline_metrics["FULL"],
        selected["metrics"]["FULL"],
        selected["metrics"]["FROZEN_2025_PLUS"],
        stress_metrics,
    )
    historical_pass = bool(selected["selection_pass"] and all(final_checks.values()))
    path, clusters, tranches, _ = raw_results[selected_spec.name]
    path.to_csv(REPORT_ROOT / "selected_path.csv")
    clusters.to_csv(REPORT_ROOT / "selected_clusters.csv", index=False)
    tranches.to_csv(REPORT_ROOT / "selected_tranches.csv", index=False)
    stress_path.to_csv(REPORT_ROOT / "selected_stress_path.csv")
    stress_clusters.to_csv(REPORT_ROOT / "selected_stress_clusters.csv", index=False)
    stress_tranches.to_csv(REPORT_ROOT / "selected_stress_tranches.csv", index=False)
    verdict = {
        "experiment": "CORE4_V24_EARLY_EXECUTION_TRANCHE",
        "period": f"{START.date()}/{(end - pd.Timedelta(days=1)).date()}",
        "selected_spec": selected_spec.name,
        "selected_from_eligible_set": bool(eligible),
        "baseline_metrics": baseline_metrics,
        "selected": selected,
        "stress": {"metrics": stress_metrics, "diagnostics": stress_diagnostics},
        "replacement_checks": final_checks,
        "historical_gate_pass": historical_pass,
        "decision": "AUTHORIZE_V24_PAPER_SHADOW" if historical_pass else "REJECT_V24_KEEP_V20",
        "runtime_changed": False,
        "real_money_authorized": False,
        "candidates": results,
    }
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8"
    )
    base = baseline_metrics["FULL"]
    full = selected["metrics"]["FULL"]
    lines = [
        "# CORE4 V24 EARLY EXECUTION TRANCHE",
        "",
        f"**{verdict['decision']}**",
        "",
        f"Selected: `{selected_spec.name}`.",
        f"Selection checks: {sum(selected['selection_checks'].values())}/{len(selected['selection_checks'])}.",
        f"Replacement checks: {sum(final_checks.values())}/{len(final_checks)}.",
        "",
        "| System | Trades/week | Win | PF | Mean R | Return | Sharpe | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| V20 | {base['trades_per_week']:.3f} | {base['win_rate']:.1%} | {base['profit_factor']:.3f} | {base['mean_r']:.3f} | {base['total_return']:.2%} | {base['sharpe']:.3f} | {base['max_drawdown']:.2%} |",
        f"| V24 | {full['trades_per_week']:.3f} | {full['win_rate']:.1%} | {full['profit_factor']:.3f} | {full['mean_r']:.3f} | {full['total_return']:.2%} | {full['sharpe']:.3f} | {full['max_drawdown']:.2%} |",
        f"| V24 stress | {stress_metrics['trades_per_week']:.3f} | {stress_metrics['win_rate']:.1%} | {stress_metrics['profit_factor']:.3f} | {stress_metrics['mean_r']:.3f} | {stress_metrics['total_return']:.2%} | {stress_metrics['sharpe']:.3f} | {stress_metrics['max_drawdown']:.2%} |",
        "",
        "V20 production and real-money locks were not changed.",
    ]
    (REPORT_ROOT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_safe({key: value for key, value in verdict.items() if key != "candidates"}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
