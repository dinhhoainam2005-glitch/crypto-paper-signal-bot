"""Causal walk-forward regime allocator for the frozen CORE4 sleeves."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_beta_regime_v7 import audit_exit, audit_side, enrich
from audit_execution_timing_v7 import load_exact_inputs, row_at
from audit_full_history_tournament_v18 import (
    json_safe,
    period_metrics,
    replacement_checks,
    trim_result,
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
from backtest_symmetric_v4 import (
    DATA_ROOT,
    STRESS_COST,
    FuturesPosition,
    build_plan,
    exit_quantity,
    process_intraday,
    process_open,
    trade_record,
)


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "regime_allocator_v19"
FULL_START = pd.Timestamp("2021-01-01", tz="UTC")
INITIAL_TRAINING_YEARS = 2
CORE_WEIGHT_WITH_FAST = 0.60
FAST_WEIGHT = 1.0 - CORE_WEIGHT_WITH_FAST
FAST_SYMBOLS = ("BNBUSDT", "SOLUSDT")


@dataclass(frozen=True)
class Policy:
    name: str
    enabled: bool = True
    require_trend: bool = False
    volatility: str = "ANY"
    min_breadth: int = 0
    bearish_only: bool = False


POLICIES = (
    Policy("DISABLED", enabled=False),
    Policy("ALWAYS"),
    Policy("TREND_BREADTH2", require_trend=True, min_breadth=2),
    Policy(
        "HIGHVOL_TREND_BREADTH2",
        require_trend=True,
        volatility="HIGH",
        min_breadth=2,
    ),
    Policy(
        "LOWVOL_TREND_BREADTH2",
        require_trend=True,
        volatility="LOW",
        min_breadth=2,
    ),
    Policy("HIGHVOL_BREADTH2", volatility="HIGH", min_breadth=2),
    Policy(
        "BEAR_HIGHVOL_STRESS",
        require_trend=True,
        volatility="HIGH",
        min_breadth=2,
        bearish_only=True,
    ),
)


def build_context(markets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    btc = markets["BTCUSDT"]
    context = pd.DataFrame(index=btc.index)
    context["side"] = np.where(
        btc["close"] > btc["audit_sma"],
        "LONG",
        np.where(btc["close"] < btc["audit_sma"], "SHORT", None),
    )
    context["slope"] = btc["audit_sma"] - btc["audit_sma"].shift(20)
    returns = btc["close"].pct_change(fill_method=None)
    context["rv20"] = returns.rolling(20, min_periods=20).std() * math.sqrt(365.0)
    context["rv_median252"] = context["rv20"].rolling(252, min_periods=126).median()
    breadth: list[float] = []
    for date, side in context["side"].items():
        if side not in {"LONG", "SHORT"}:
            breadth.append(np.nan)
            continue
        aligned = 0
        for frame in markets.values():
            if date not in frame.index:
                continue
            row = frame.loc[date]
            if pd.isna(row["audit_sma"]):
                continue
            if side == "LONG" and row["close"] > row["audit_sma"]:
                aligned += 1
            elif side == "SHORT" and row["close"] < row["audit_sma"]:
                aligned += 1
        breadth.append(float(aligned))
    context["breadth"] = breadth
    context["trend_aligned"] = (
        ((context["side"] == "LONG") & (context["slope"] > 0.0))
        | ((context["side"] == "SHORT") & (context["slope"] < 0.0))
    )
    context["high_vol"] = context["rv20"] >= context["rv_median252"]
    return context


def policy_allows(policy: Policy, context: pd.DataFrame, date: pd.Timestamp) -> bool:
    if not policy.enabled or date not in context.index:
        return False
    row = context.loc[date]
    required = row[["side", "slope", "rv20", "rv_median252", "breadth"]]
    if required.isna().any():
        return False
    if policy.require_trend and not bool(row["trend_aligned"]):
        return False
    if policy.volatility == "HIGH" and not bool(row["high_vol"]):
        return False
    if policy.volatility == "LOW" and bool(row["high_vol"]):
        return False
    if float(row["breadth"]) < policy.min_breadth:
        return False
    if policy.bearish_only and row["side"] != "SHORT":
        return False
    return True


def _close_position(
    position: FuturesPosition,
    timestamp: pd.Timestamp,
    price: float,
    reason: str,
    cost_rate: float,
) -> float:
    delta, _ = exit_quantity(
        position,
        timestamp,
        position.remaining_quantity,
        price,
        reason,
        cost_rate,
    )
    return delta


def routed_exact_simulate(
    core_daily: dict[str, pd.DataFrame],
    fast_daily: dict[str, pd.DataFrame],
    hourly: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    context: pd.DataFrame,
    policy: Policy,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_rate: float = STRESS_COST,
    entry_delay_days: int = 0,
    funding_credit_fraction: float = 1.0,
    funding_cost_multiplier: float = 1.0,
    tradable_symbols: tuple[str, ...] = SYMBOLS,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int]]:
    if entry_delay_days < 0:
        raise ValueError("entry_delay_days must be non-negative")
    active_symbols = set(tradable_symbols)
    if not active_symbols or not active_symbols.issubset(SYMBOLS):
        raise ValueError("tradable_symbols must be a non-empty CORE4 subset")
    calendar = pd.date_range(start, end - pd.Timedelta(hours=1), freq="1h", tz="UTC")
    cash = 1.0
    previous_equity = 1.0
    positions: dict[tuple[str, str], FuturesPosition] = {}
    trades: list[dict[str, Any]] = []
    path: list[dict[str, Any]] = []
    minimum_equity = 1.0
    maximum_initial_gross = 0.0
    fast_eligible_days = 0
    fast_request_days = 0

    def finish(key: tuple[str, str]) -> None:
        record = trade_record(positions[key])
        record["sleeve"] = key[0]
        record["router_policy"] = policy.name
        trades.append(record)
        del positions[key]

    for calendar_index, timestamp in enumerate(calendar):
        day = timestamp.floor("D")
        decision_day = day - pd.Timedelta(days=1)
        rows = {symbol: row_at(hourly[symbol], timestamp) for symbol in SYMBOLS}

        for key in list(positions):
            sleeve, symbol = key
            position = positions[key]
            row = rows[symbol]
            if row is None:
                continue
            reason = None
            if timestamp.hour == 0:
                holding_days = (
                    timestamp - pd.Timestamp(position.plan.generated_at_utc)
                ).total_seconds() / 86_400.0
                route_daily = core_daily if sleeve == "CORE" else fast_daily
                if holding_days >= MAX_HOLD_DAYS:
                    reason = "MAX_HOLD"
                elif audit_exit(route_daily, symbol, decision_day, position.plan.side):
                    reason = "SIGNAL_INVALID"
            if reason is not None:
                cash += _close_position(
                    position, timestamp, float(row["open"]), reason, cost_rate
                )
            else:
                delta, _ = process_open(position, timestamp, float(row["open"]), cost_rate)
                cash += delta
            if position.remaining_quantity <= 1e-12:
                finish(key)

        if timestamp.hour == 0:
            signal_day = decision_day - pd.Timedelta(days=entry_delay_days)
            core_candidates: list[tuple[str, str, str, pd.Series]] = []
            fast_candidates: list[tuple[str, str, str, pd.Series]] = []
            for symbol in tradable_symbols:
                if rows[symbol] is None:
                    continue
                core_row = row_at(core_daily[symbol], signal_day)
                core_side = audit_side(core_daily, symbol, signal_day)
                if (
                    ("CORE", symbol) not in positions
                    and core_side is not None
                    and core_row is not None
                    and pd.notna(core_row["atr20"])
                ):
                    core_candidates.append(("CORE", symbol, core_side, core_row))
                fast_row = row_at(fast_daily[symbol], signal_day)
                fast_side = audit_side(fast_daily, symbol, signal_day)
                if (
                    ("FAST", symbol) not in positions
                    and fast_side is not None
                    and fast_row is not None
                    and pd.notna(fast_row["atr20"])
                ):
                    fast_candidates.append(("FAST", symbol, fast_side, fast_row))

            fast_confirmed = len({item[1] for item in fast_candidates}) >= 2
            regime_allowed = policy_allows(policy, context, signal_day)
            if regime_allowed:
                fast_eligible_days += 1
            fast_candidates = [
                item for item in fast_candidates if item[1] in FAST_SYMBOLS
            ]
            fast_active = bool(fast_confirmed and regime_allowed and fast_candidates)
            if fast_active:
                fast_request_days += 1
            else:
                fast_candidates = []

            core_weight = CORE_WEIGHT_WITH_FAST if fast_active else 1.0
            requests: list[tuple[tuple[str, str], FuturesPosition, float]] = []
            for sleeve, symbol, side, decision_row in core_candidates + fast_candidates:
                sleeve_weight = core_weight if sleeve == "CORE" else FAST_WEIGHT
                risk_budget = RISK_PER_TRADE * sleeve_weight
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
                notional_weight = min(
                    MAX_SYMBOL_NOTIONAL * sleeve_weight,
                    risk_budget / distance_fraction,
                )
                position = FuturesPosition(
                    plan=plan,
                    initial_quantity=0.0,
                    remaining_quantity=0.0,
                    initial_notional=0.0,
                    initial_risk_cash=0.0,
                    entry_fee=0.0,
                    current_stop=plan.stop_loss,
                    tp_fractions=TP_FRACTIONS,
                )
                requests.append(((sleeve, symbol), position, notional_weight))

            unrealized = sum(
                position.direction
                * position.remaining_quantity
                * (float(rows[key[1]]["open"]) - position.plan.entry)
                for key, position in positions.items()
                if rows[key[1]] is not None
            )
            equity_at_open = cash + unrealized
            existing_gross = sum(
                position.remaining_quantity * float(rows[key[1]]["open"])
                for key, position in positions.items()
                if rows[key[1]] is not None
            ) / equity_at_open
            requested_total = sum(weight for _, _, weight in requests)
            available = max(0.0, MAX_INITIAL_GROSS - existing_gross)
            scale = min(1.0, available / requested_total) if requested_total > 0.0 else 0.0
            initial_gross = existing_gross
            for key, position, weight in requests:
                notional = equity_at_open * weight * scale
                if notional <= 0.0:
                    continue
                quantity = notional / position.plan.entry
                fee = notional * cost_rate
                cash -= fee
                position.initial_quantity = quantity
                position.remaining_quantity = quantity
                position.initial_notional = notional
                position.initial_risk_cash = quantity * abs(
                    position.plan.entry - position.plan.stop_loss
                )
                position.entry_fee = fee
                positions[key] = position
                initial_gross += notional / equity_at_open
            maximum_initial_gross = max(maximum_initial_gross, initial_gross)

        for key, position in positions.items():
            symbol = key[1]
            row = rows[symbol]
            if row is None:
                continue
            rate = float(funding[symbol].get(timestamp, 0.0))
            funding_cash = (
                -position.direction
                * position.remaining_quantity
                * float(row["open"])
                * rate
            )
            if funding_cash >= 0.0:
                funding_cash *= funding_credit_fraction
            else:
                funding_cash *= funding_cost_multiplier
            position.funding_pnl += funding_cash
            cash += funding_cash

        for key in list(positions):
            symbol = key[1]
            row = rows[symbol]
            if row is None:
                continue
            position = positions[key]
            delta, _ = process_intraday(
                position,
                timestamp,
                float(row["low"]),
                float(row["high"]),
                cost_rate,
            )
            cash += delta
            if position.remaining_quantity <= 1e-12:
                finish(key)

        is_day_end = (
            calendar_index == len(calendar) - 1
            or calendar[calendar_index + 1].floor("D") != day
        )
        if is_day_end:
            unrealized = 0.0
            gross = 0.0
            for key, position in positions.items():
                row = rows[key[1]]
                mark = float(row["close"]) if row is not None else position.plan.entry
                unrealized += (
                    position.direction
                    * position.remaining_quantity
                    * (mark - position.plan.entry)
                )
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
    for key in list(positions):
        symbol = key[1]
        position = positions[key]
        row = row_at(hourly[symbol], final_timestamp)
        price = float(row["close"]) if row is not None else position.plan.entry
        cash += _close_position(position, final_timestamp, price, "END_OF_FOLD", cost_rate)
        finish(key)
    if path:
        prior = float(path[-2]["equity"]) if len(path) > 1 else 1.0
        path[-1].update(
            {
                "return": cash / prior - 1.0,
                "equity": cash,
                "cash": cash,
                "gross_exposure": 0.0,
                "open_positions": 0,
            }
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
            "fast_eligible_days": fast_eligible_days,
            "fast_request_days": fast_request_days,
        },
    )


def training_checks(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, bool]:
    return {
        "frequency_improves_10pct": (
            candidate["trades_per_week"] >= 1.10 * baseline["trades_per_week"]
        ),
        "pf_not_worse": candidate["profit_factor"] >= baseline["profit_factor"],
        "return_not_worse": candidate["total_return"] >= baseline["total_return"],
        "sharpe_not_worse": candidate["sharpe"] >= baseline["sharpe"],
        "drawdown_not_worse": candidate["max_drawdown"] >= baseline["max_drawdown"],
    }


def stitch_walk_forward(
    results: dict[str, dict[str, Any]],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    years = range(start.year, end.year + 1)
    selected_returns: list[pd.Series] = []
    selected_trades: list[pd.DataFrame] = []
    selections: list[dict[str, Any]] = []
    baseline = results["DISABLED"]
    for year in years:
        year_start = max(start, pd.Timestamp(f"{year}-01-01", tz="UTC"))
        year_end = min(end, pd.Timestamp(f"{year + 1}-01-01", tz="UTC"))
        selected_name = "DISABLED"
        training: dict[str, Any] = {}
        if year >= start.year + INITIAL_TRAINING_YEARS:
            base_train = period_metrics(
                baseline["path"], baseline["trades"], start, year_start
            )["full"]
            eligible: list[tuple[str, dict[str, Any]]] = []
            for name, result in results.items():
                if name == "DISABLED":
                    continue
                metric = period_metrics(
                    result["path"], result["trades"], start, year_start
                )["full"]
                checks = training_checks(base_train, metric)
                training[name] = {"metrics": metric, "checks": checks}
                if all(checks.values()):
                    eligible.append((name, metric))
            if eligible:
                selected_name = max(
                    eligible,
                    key=lambda item: (
                        item[1]["sharpe"],
                        item[1]["profit_factor"],
                        item[1]["total_return"],
                        item[1]["trades_per_week"],
                    ),
                )[0]
        selected = results[selected_name]
        segment = selected["path"].loc[
            (selected["path"].index >= year_start)
            & (selected["path"].index < year_end),
            "return",
        ]
        selected_returns.append(segment)
        trades = selected["trades"]
        if not trades.empty:
            entries = pd.to_datetime(trades["entry_time"], utc=True)
            chosen = trades.loc[(entries >= year_start) & (entries < year_end)].copy()
            chosen["walk_forward_year"] = year
            chosen["selected_policy"] = selected_name
            selected_trades.append(chosen)
        selections.append(
            {
                "year": year,
                "training_end": str(year_start.date()),
                "selected_policy": selected_name,
                "eligible_policies": sorted(
                    name
                    for name, detail in training.items()
                    if all(detail["checks"].values())
                ),
            }
        )
    returns = pd.concat(selected_returns).sort_index()
    equity = (1.0 + returns).cumprod()
    path = pd.DataFrame({"return": returns, "equity": equity}, index=returns.index)
    trades = pd.concat(selected_trades, ignore_index=True) if selected_trades else pd.DataFrame()
    return path, trades, selections


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
    end = min(frame.index.max().floor("D") for frame in hourly.values()) + pd.Timedelta(days=1)
    results: dict[str, dict[str, Any]] = {}
    for policy in POLICIES:
        print(f"V19 ROUTER {policy.name}", flush=True)
        path, trades, diagnostics = routed_exact_simulate(
            core_daily,
            fast_daily,
            hourly,
            funding,
            context,
            policy,
            FULL_START,
            end,
        )
        results[policy.name] = {
            "policy": asdict(policy),
            "path": path,
            "trades": trades,
            "diagnostics": diagnostics,
            "metrics": period_metrics(path, trades, FULL_START, end),
        }

    wf_path, wf_trades, selections = stitch_walk_forward(results, FULL_START, end)
    wf_metrics = period_metrics(wf_path, wf_trades, FULL_START, end)
    baseline_metrics = results["DISABLED"]["metrics"]
    fixed_policy_checks = {
        name: replacement_checks(baseline_metrics, result["metrics"])
        for name, result in results.items()
        if name != "DISABLED"
    }
    shadow_nominee_name = max(
        fixed_policy_checks,
        key=lambda name: (
            sum(fixed_policy_checks[name].values()),
            results[name]["metrics"]["full"]["sharpe"],
            results[name]["metrics"]["full"]["profit_factor"],
            results[name]["metrics"]["full"]["trades_per_week"],
        ),
    )
    checks = replacement_checks(baseline_metrics, wf_metrics)
    decision = (
        "PAPER_SHADOW_REQUIRED"
        if all(checks.values())
        else "REJECT_V19_RETAIN_V7"
    )
    verdict = {
        "experiment": "CORE4_V19_CAUSAL_REGIME_ALLOCATOR",
        "period": f"{FULL_START.date()}/{(end - pd.Timedelta(days=1)).date()}",
        "policies": {
            name: {
                "policy": result["policy"],
                "metrics": result["metrics"],
                "diagnostics": result["diagnostics"],
            }
            for name, result in results.items()
        },
        "walk_forward": {
            "selections": selections,
            "metrics": wf_metrics,
            "checks": checks,
            "checks_passed": sum(checks.values()),
            "checks_total": len(checks),
        },
        "fixed_policy_audit": {
            "checks": fixed_policy_checks,
            "shadow_nominee": shadow_nominee_name,
            "shadow_nominee_metrics": results[shadow_nominee_name]["metrics"]["full"],
            "shadow_nominee_failed_checks": [
                name
                for name, passed in fixed_policy_checks[shadow_nominee_name].items()
                if not passed
            ],
        },
        "decision": decision,
        "recommended_action": "RETAIN_V7_NO_V19_DEPLOY",
        "production_changed": False,
    }
    safe = json_safe(verdict)
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(safe, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    rows = []
    for name, result in results.items():
        rows.append({"policy": name, **result["metrics"]["full"]})
    rows.append({"policy": "WALK_FORWARD", **wf_metrics["full"]})
    pd.DataFrame(rows).to_csv(REPORT_ROOT / "policy_summary.csv", index=False)
    wf_path.to_csv(REPORT_ROOT / "walk_forward_path.csv")
    wf_trades.to_csv(REPORT_ROOT / "walk_forward_trades.csv", index=False)
    base = baseline_metrics["full"]
    test = wf_metrics["full"]
    shadow = results[shadow_nominee_name]["metrics"]["full"]
    shadow_checks = fixed_policy_checks[shadow_nominee_name]
    report = [
        "# CORE4 V19 CAUSAL REGIME ALLOCATOR",
        "",
        f"**{decision}**",
        "",
        "| Portfolio | Trades/wk | Win | PF | Mean R | Return | Sharpe | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| V7 baseline | {base['trades_per_week']:.3f} | {base['win_rate']:.1%} | {base['profit_factor']:.3f} | {base['mean_r']:.3f} | {base['total_return']:.2%} | {base['sharpe']:.3f} | {base['max_drawdown']:.2%} |",
        f"| V19 walk-forward | {test['trades_per_week']:.3f} | {test['win_rate']:.1%} | {test['profit_factor']:.3f} | {test['mean_r']:.3f} | {test['total_return']:.2%} | {test['sharpe']:.3f} | {test['max_drawdown']:.2%} |",
        f"| Fixed {shadow_nominee_name} | {shadow['trades_per_week']:.3f} | {shadow['win_rate']:.1%} | {shadow['profit_factor']:.3f} | {shadow['mean_r']:.3f} | {shadow['total_return']:.2%} | {shadow['sharpe']:.3f} | {shadow['max_drawdown']:.2%} |",
        "",
        f"Replacement gates: {sum(checks.values())}/{len(checks)}.",
        f"Best fixed-policy shadow nominee: {shadow_nominee_name} ({sum(shadow_checks.values())}/{len(shadow_checks)} gates).",
        "Failed fixed-policy checks: "
        + ", ".join(name for name, passed in shadow_checks.items() if not passed)
        + ".",
        "",
        "Walk-forward selections:",
        *[
            f"- {item['year']}: {item['selected_policy']}"
            for item in selections
        ],
        "",
        "Production remains unchanged.",
    ]
    (REPORT_ROOT / "REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "experiment": safe["experiment"],
                "period": safe["period"],
                "walk_forward": safe["walk_forward"],
                "decision": safe["decision"],
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
