"""Frozen robustness audit for the V19 TREND_BREADTH2 policy."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_beta_regime_v7 import enrich
from audit_execution_timing_v7 import load_exact_inputs
from audit_full_history_tournament_v18 import json_safe, period_metrics
from audit_regime_allocator_v19 import (
    FULL_START,
    POLICIES,
    build_context,
    routed_exact_simulate,
)
from backtest_allocator import SYMBOLS
from backtest_risk_defined import path_metrics, trade_metrics
from backtest_symmetric_v4 import DATA_ROOT


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "trend_breadth_robustness_v20"
POLICY = next(item for item in POLICIES if item.name == "TREND_BREADTH2")
TRADE_BOOTSTRAPS = 5_000
PATH_BOOTSTRAPS = 2_000
BOOTSTRAP_SEED = 1920

SCENARIOS = {
    "BASE_20BPS": {},
    "COST_30BPS": {"cost_rate": 0.0030},
    "COST_40BPS": {"cost_rate": 0.0040},
    "ENTRY_DELAY_1D": {"entry_delay_days": 1},
    "FUNDING_ADVERSE": {
        "funding_credit_fraction": 0.0,
        "funding_cost_multiplier": 1.5,
    },
    "COMBINED_STRESS": {
        "cost_rate": 0.0030,
        "entry_delay_days": 1,
        "funding_credit_fraction": 0.0,
        "funding_cost_multiplier": 1.5,
    },
}


def profit_factor(values: np.ndarray) -> float:
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    if losses <= 0.0:
        return math.inf if gains > 0.0 else 0.0
    return gains / losses


def clustered_trade_bootstrap(trades: pd.DataFrame) -> dict[str, float]:
    frame = trades.copy()
    frame["cluster"] = pd.to_datetime(frame["entry_time"], utc=True).dt.floor("D")
    clusters = [group["realized_r"].to_numpy(dtype=float) for _, group in frame.groupby("cluster")]
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    wins = np.empty(TRADE_BOOTSTRAPS)
    pfs = np.empty(TRADE_BOOTSTRAPS)
    means = np.empty(TRADE_BOOTSTRAPS)
    for index in range(TRADE_BOOTSTRAPS):
        selected = generator.integers(0, len(clusters), size=len(clusters))
        sample = np.concatenate([clusters[item] for item in selected])
        wins[index] = float((sample > 0.0).mean())
        pfs[index] = profit_factor(sample)
        means[index] = float(sample.mean())
    return {
        "win_rate_lower_5pct": float(np.quantile(wins, 0.05)),
        "profit_factor_lower_5pct": float(np.quantile(pfs, 0.05)),
        "mean_r_lower_5pct": float(np.quantile(means, 0.05)),
    }


def moving_block_drawdown(path: pd.DataFrame, block_length: int = 20) -> dict[str, float]:
    values = path["return"].fillna(0.0).to_numpy(dtype=float)
    generator = np.random.default_rng(BOOTSTRAP_SEED + 1)
    blocks = math.ceil(len(values) / block_length)
    offsets = np.arange(block_length)
    drawdowns = np.empty(PATH_BOOTSTRAPS)
    for index in range(PATH_BOOTSTRAPS):
        starts = generator.integers(0, len(values), size=blocks)
        locations = (starts[:, None] + offsets[None, :]) % len(values)
        sample = values[locations.ravel()[: len(values)]]
        equity = np.cumprod(1.0 + sample)
        peak = np.maximum.accumulate(equity)
        drawdowns[index] = float(np.min(equity / peak - 1.0))
    return {
        "max_drawdown_median": float(np.quantile(drawdowns, 0.50)),
        "max_drawdown_worst_95pct": float(np.quantile(drawdowns, 0.05)),
    }


def symbol_metrics(trades: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    output: dict[str, dict[str, float | int]] = {}
    for symbol in SYMBOLS:
        selected = trades.loc[trades["symbol"] == symbol]
        output[symbol] = trade_metrics(selected)
    return output


def scenario_checks(metrics: dict[str, Any]) -> dict[str, bool]:
    return {
        "pf_ge_1p30": metrics["profit_factor"] >= 1.30,
        "mean_r_positive": metrics["mean_r"] > 0.0,
        "max_drawdown_le_7pct": metrics["max_drawdown"] >= -0.07,
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
    end = min(frame.index.max().floor("D") for frame in hourly.values()) + pd.Timedelta(days=1)

    scenarios: dict[str, Any] = {}
    base_path = pd.DataFrame()
    base_trades = pd.DataFrame()
    for name, parameters in SCENARIOS.items():
        print(f"V20 SCENARIO {name}", flush=True)
        path, trades, diagnostics = routed_exact_simulate(
            core_daily,
            fast_daily,
            hourly,
            funding,
            context,
            POLICY,
            FULL_START,
            end,
            **parameters,
        )
        metrics = period_metrics(path, trades, FULL_START, end)
        scenarios[name] = {
            "parameters": parameters,
            "metrics": metrics,
            "diagnostics": diagnostics,
            "checks": scenario_checks(metrics["full"]),
        }
        if name == "BASE_20BPS":
            base_path, base_trades = path, trades

    leave_one_out: dict[str, Any] = {}
    for excluded in SYMBOLS:
        print(f"V20 LEAVE OUT {excluded}", flush=True)
        universe = tuple(symbol for symbol in SYMBOLS if symbol != excluded)
        path, trades, diagnostics = routed_exact_simulate(
            core_daily,
            fast_daily,
            hourly,
            funding,
            context,
            POLICY,
            FULL_START,
            end,
            tradable_symbols=universe,
        )
        metrics = period_metrics(path, trades, FULL_START, end)
        leave_one_out[excluded] = {
            "metrics": metrics,
            "diagnostics": diagnostics,
            "passes": (
                metrics["full"]["profit_factor"] >= 1.20
                and metrics["full"]["total_return"] > 0.0
            ),
        }

    base = scenarios["BASE_20BPS"]["metrics"]
    completed_years = [
        item for item in base["years"].values() if item["days"] >= 330
    ]
    base_checks = {
        "frequency_ge_0p66": base["full"]["trades_per_week"] >= 0.66,
        "win_rate_ge_58pct": base["full"]["win_rate"] >= 0.58,
        "pf_ge_1p70": base["full"]["profit_factor"] >= 1.70,
        "sharpe_ge_1": base["full"]["sharpe"] >= 1.0,
        "max_drawdown_le_5pct": base["full"]["max_drawdown"] >= -0.05,
        "completed_year_return_floor": bool(completed_years)
        and all(item["total_return"] >= -0.01 for item in completed_years),
    }
    stress_checks = {
        name: all(detail["checks"].values())
        for name, detail in scenarios.items()
        if name != "BASE_20BPS"
    }
    combined_positive = (
        scenarios["COMBINED_STRESS"]["metrics"]["full"]["total_return"] > 0.0
    )
    leave_one_out_passes = sum(item["passes"] for item in leave_one_out.values())
    uncertainty = {
        **clustered_trade_bootstrap(base_trades),
        **moving_block_drawdown(base_path),
    }
    gates = {
        "all_base_checks": all(base_checks.values()),
        "all_stress_checks": all(stress_checks.values()),
        "combined_return_positive": combined_positive,
        "leave_one_out_3_of_4": leave_one_out_passes >= 3,
        "bootstrap_mean_r_lower_positive": uncertainty["mean_r_lower_5pct"] > 0.0,
        "bootstrap_pf_lower_ge_1": uncertainty["profit_factor_lower_5pct"] >= 1.0,
        "bootstrap_drawdown_worst_le_7pct": (
            uncertainty["max_drawdown_worst_95pct"] >= -0.07
        ),
    }
    decision = "PAPER_SHADOW_AUTHORIZED" if all(gates.values()) else "REJECT_V20"
    verdict = {
        "experiment": "CORE4_V20_TREND_BREADTH2_ROBUSTNESS",
        "period": f"{FULL_START.date()}/{(end - pd.Timedelta(days=1)).date()}",
        "candidate": POLICY.name,
        "scenarios": scenarios,
        "leave_one_out": leave_one_out,
        "symbol_metrics": symbol_metrics(base_trades),
        "uncertainty": uncertainty,
        "base_checks": base_checks,
        "stress_checks": stress_checks,
        "gates": gates,
        "gates_passed": sum(gates.values()),
        "gates_total": len(gates),
        "decision": decision,
        "production_changed": False,
    }
    safe = json_safe(verdict)
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(safe, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    rows = []
    for name, detail in scenarios.items():
        rows.append({"scenario": name, **detail["metrics"]["full"]})
    pd.DataFrame(rows).to_csv(REPORT_ROOT / "scenario_summary.csv", index=False)
    base_path.to_csv(REPORT_ROOT / "base_path.csv")
    base_trades.to_csv(REPORT_ROOT / "base_trades.csv", index=False)
    report = [
        "# CORE4 V20 TREND_BREADTH2 ROBUSTNESS",
        "",
        f"**{decision}**",
        "",
        "| Scenario | Trades/wk | Win | PF | Mean R | Return | Sharpe | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, detail in scenarios.items():
        metric = detail["metrics"]["full"]
        report.append(
            f"| {name} | {metric['trades_per_week']:.3f} | {metric['win_rate']:.1%} | "
            f"{metric['profit_factor']:.3f} | {metric['mean_r']:.3f} | "
            f"{metric['total_return']:.2%} | {metric['sharpe']:.3f} | "
            f"{metric['max_drawdown']:.2%} |"
        )
    report.extend(
        [
            "",
            f"Robustness gates: {sum(gates.values())}/{len(gates)}.",
            f"Leave-one-coin-out passes: {leave_one_out_passes}/4.",
            f"Bootstrap PF lower 5%: {uncertainty['profit_factor_lower_5pct']:.3f}.",
            f"Bootstrap mean R lower 5%: {uncertainty['mean_r_lower_5pct']:.3f}.",
            f"Bootstrap worst 95% max DD: {uncertainty['max_drawdown_worst_95pct']:.2%}.",
            "",
            "Production remains unchanged.",
        ]
    )
    (REPORT_ROOT / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "experiment": safe["experiment"],
                "period": safe["period"],
                "base": safe["scenarios"]["BASE_20BPS"]["metrics"]["full"],
                "stress_checks": safe["stress_checks"],
                "uncertainty": safe["uncertainty"],
                "gates": safe["gates"],
                "decision": safe["decision"],
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
