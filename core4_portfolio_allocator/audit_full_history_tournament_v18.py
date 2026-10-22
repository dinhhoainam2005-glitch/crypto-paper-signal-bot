"""Finite exact-path tournament across the full common CORE4 history."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_beta_regime_v7 import enrich
from audit_execution_timing_v7 import exact_simulate, hourly_data_quality, load_exact_inputs
from audit_fast_confirmed_v16 import combine_paths, json_safe, scale_trades
from backtest_allocator import SYMBOLS
from backtest_risk_defined import path_metrics, trade_metrics
from backtest_symmetric_v4 import DATA_ROOT, STRESS_COST


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "full_history_tournament_v18"
FULL_START = pd.Timestamp("2021-01-01", tz="UTC")
CHALLENGER_WEIGHTS = (0.10, 0.20, 0.30, 0.40)
OBJECTIVES = (
    "trades_per_week",
    "win_rate",
    "profit_factor",
    "mean_r",
    "total_return",
    "sharpe",
    "max_drawdown",
)


@dataclass(frozen=True)
class SleeveSpec:
    name: str
    entry_channel: int
    exit_channel: int
    confirmations: int
    tradable_symbols: tuple[str, ...] = SYMBOLS


SLEEVES = (
    SleeveSpec("V7_55_20_C1_ALL", 55, 20, 1),
    SleeveSpec("FAST_20_10_C1_ALL", 20, 10, 1),
    SleeveSpec("FAST_20_10_C2_ALL", 20, 10, 2),
    SleeveSpec("FAST_20_10_C2_BNBSOL", 20, 10, 2, ("BNBUSDT", "SOLUSDT")),
    SleeveSpec("MID_40_15_C1_ALL", 40, 15, 1),
    SleeveSpec("MID_40_15_C2_ALL", 40, 15, 2),
    SleeveSpec("MID_40_15_C2_BNBSOL", 40, 15, 2, ("BNBUSDT", "SOLUSDT")),
    SleeveSpec("V14_55_20_C2_ALL", 55, 20, 2),
    SleeveSpec("TREND_70_25_C1_ALL", 70, 25, 1),
    SleeveSpec("TREND_70_25_C2_ALL", 70, 25, 2),
    SleeveSpec("SLOW_90_30_C1_ALL", 90, 30, 1),
    SleeveSpec("SLOW_90_30_C2_ALL", 90, 30, 2),
)


def trim_result(
    path: pd.DataFrame,
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected_path = path.loc[(path.index >= start) & (path.index < end)].copy()
    if trades.empty:
        return selected_path, trades.copy()
    entries = pd.to_datetime(trades["entry_time"], utc=True)
    return selected_path, trades.loc[(entries >= start) & (entries < end)].copy()


def summarize(
    path: pd.DataFrame,
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, float | int]:
    result = {**path_metrics(path), **trade_metrics(trades)}
    weeks = max((end - start).total_seconds() / (7.0 * 86_400.0), 1.0 / 7.0)
    result["trades_per_week"] = result["trades"] / weeks
    return result


def period_metrics(
    path: pd.DataFrame,
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    full_path, full_trades = trim_result(path, trades, start, end)
    years: dict[str, Any] = {}
    for year in range(start.year, end.year + 1):
        year_start = max(start, pd.Timestamp(f"{year}-01-01", tz="UTC"))
        year_end = min(end, pd.Timestamp(f"{year + 1}-01-01", tz="UTC"))
        if year_start >= year_end:
            continue
        year_path, year_trades = trim_result(path, trades, year_start, year_end)
        if year_path.empty:
            continue
        years[str(year)] = summarize(year_path, year_trades, year_start, year_end)
    return {
        "full": summarize(full_path, full_trades, start, end),
        "years": years,
    }


def replacement_checks(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, bool]:
    baseline = base["full"]
    test = candidate["full"]
    years = candidate["years"]
    completed = [item for item in years.values() if item["days"] >= 330]
    positive_fraction = (
        sum(item["total_return"] > 0.0 for item in completed) / len(completed)
        if completed
        else 0.0
    )
    return {
        "frequency_improves_25pct": test["trades_per_week"] >= 1.25 * baseline["trades_per_week"],
        "win_not_worse": test["win_rate"] >= baseline["win_rate"],
        "pf_not_worse": test["profit_factor"] >= baseline["profit_factor"],
        "mean_r_not_worse": test["mean_r"] >= baseline["mean_r"],
        "return_not_worse": test["total_return"] >= baseline["total_return"],
        "sharpe_not_worse": test["sharpe"] >= baseline["sharpe"],
        "drawdown_not_worse": test["max_drawdown"] >= baseline["max_drawdown"],
        "pf_ge_1p30": test["profit_factor"] >= 1.30,
        "sharpe_ge_1": test["sharpe"] >= 1.0,
        "mean_r_positive": test["mean_r"] > 0.0,
        "drawdown_le_10pct": test["max_drawdown"] >= -0.10,
        "positive_completed_years_ge_70pct": positive_fraction >= 0.70,
        "yearly_pf_floor": bool(completed) and all(item["profit_factor"] >= 1.0 for item in completed),
        "yearly_return_floor": bool(completed) and all(item["total_return"] >= -0.01 for item in completed),
    }


def dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_full = left["metrics"]["full"]
    right_full = right["metrics"]["full"]
    comparisons = [left_full[key] >= right_full[key] for key in OBJECTIVES]
    strict = [left_full[key] > right_full[key] for key in OBJECTIVES]
    return all(comparisons) and any(strict)


def pareto_names(candidates: list[dict[str, Any]]) -> list[str]:
    return sorted(
        candidate["name"]
        for candidate in candidates
        if not any(
            other is not candidate and dominates(other, candidate)
            for other in candidates
        )
    )


def report_table(candidates: list[dict[str, Any]], names: list[str]) -> list[str]:
    lookup = {candidate["name"]: candidate for candidate in candidates}
    lines = [
        "| Candidate | Trades/wk | Win | PF | Mean R | Return | Sharpe | Max DD | Gates |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in names:
        item = lookup[name]
        metric = item["metrics"]["full"]
        checks = item.get("checks", {})
        lines.append(
            f"| {name} | {metric['trades_per_week']:.3f} | {metric['win_rate']:.1%} | "
            f"{metric['profit_factor']:.3f} | {metric['mean_r']:.3f} | "
            f"{metric['total_return']:.2%} | {metric['sharpe']:.3f} | "
            f"{metric['max_drawdown']:.2%} | {sum(checks.values())}/{len(checks)} |"
        )
    return lines


def main() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    official, hourly, funding = load_exact_inputs(DATA_ROOT)
    last_complete_day = min(frame.index.max().floor("D") for frame in hourly.values())
    full_end = last_complete_day + pd.Timedelta(days=1)
    raw = {
        symbol: frame[["open", "high", "low", "close", "atr20"]].copy()
        for symbol, frame in official.items()
    }

    sleeve_results: dict[str, dict[str, Any]] = {}
    for spec in SLEEVES:
        print(f"V18 SLEEVE {spec.name}", flush=True)
        daily = enrich(
            raw,
            entry_channel=spec.entry_channel,
            exit_channel=spec.exit_channel,
            sma_days=200,
        )
        path, trades, diagnostics = exact_simulate(
            daily,
            hourly,
            funding,
            FULL_START,
            full_end,
            cost_rate=STRESS_COST,
            min_concurrent_signals=spec.confirmations,
            tradable_symbols=spec.tradable_symbols,
        )
        path, trades = trim_result(path, trades, FULL_START, full_end)
        sleeve_results[spec.name] = {
            "spec": asdict(spec),
            "path": path,
            "trades": trades,
            "diagnostics": diagnostics,
            "metrics": period_metrics(path, trades, FULL_START, full_end),
        }

    base = sleeve_results[SLEEVES[0].name]
    candidates: list[dict[str, Any]] = [
        {
            "name": "V7_BASELINE",
            "composition": {"V7_55_20_C1_ALL": 1.0},
            "metrics": base["metrics"],
            "checks": {},
        }
    ]
    for spec in SLEEVES[1:]:
        challenger = sleeve_results[spec.name]
        candidates.append(
            {
                "name": f"PURE_{spec.name}",
                "composition": {spec.name: 1.0},
                "metrics": challenger["metrics"],
            }
        )
        for weight in CHALLENGER_WEIGHTS:
            core_weight = 1.0 - weight
            path = combine_paths(base["path"], challenger["path"], core_weight)
            trades = pd.concat(
                [
                    scale_trades(base["trades"], "V7_55_20_C1_ALL", core_weight),
                    scale_trades(challenger["trades"], spec.name, weight),
                ],
                ignore_index=True,
            )
            candidates.append(
                {
                    "name": f"V7_{core_weight:.0%}+{spec.name}_{weight:.0%}",
                    "composition": {
                        "V7_55_20_C1_ALL": core_weight,
                        spec.name: weight,
                    },
                    "metrics": period_metrics(path, trades, FULL_START, full_end),
                }
            )

    for candidate in candidates:
        candidate["checks"] = (
            {"baseline_reference": True}
            if candidate["name"] == "V7_BASELINE"
            else replacement_checks(base["metrics"], candidate["metrics"])
        )
        candidate["all_replacement_checks_pass"] = (
            candidate["name"] != "V7_BASELINE" and all(candidate["checks"].values())
        )

    eligible = [candidate for candidate in candidates if candidate["all_replacement_checks_pass"]]
    frontier = pareto_names(candidates)
    challengers = [candidate for candidate in candidates if candidate["name"] != "V7_BASELINE"]
    shadow_nominee = max(
        challengers,
        key=lambda item: (
            sum(item["checks"].values()),
            item["metrics"]["full"]["sharpe"],
            item["metrics"]["full"]["profit_factor"],
            item["metrics"]["full"]["trades_per_week"],
        ),
    )
    shadow_failed_checks = [
        name for name, passed in shadow_nominee["checks"].items() if not passed
    ]
    selected = None
    if eligible:
        selected = max(
            eligible,
            key=lambda item: (
                min(
                    item["metrics"]["full"]["profit_factor"] / base["metrics"]["full"]["profit_factor"],
                    item["metrics"]["full"]["sharpe"] / base["metrics"]["full"]["sharpe"],
                    item["metrics"]["full"]["trades_per_week"] / base["metrics"]["full"]["trades_per_week"],
                ),
                item["metrics"]["full"]["total_return"],
            ),
        )["name"]

    public_sleeves = {
        name: {
            "spec": result["spec"],
            "metrics": result["metrics"],
            "diagnostics": result["diagnostics"],
        }
        for name, result in sleeve_results.items()
    }
    verdict = {
        "experiment": "CORE4_V18_FULL_HISTORY_TOURNAMENT",
        "data_period": f"{FULL_START.date()}/{(full_end - pd.Timedelta(days=1)).date()}",
        "data_quality": hourly_data_quality(official, hourly),
        "candidate_count": len(candidates),
        "sleeves": public_sleeves,
        "pareto_frontier": frontier,
        "eligible_replacements": [candidate["name"] for candidate in eligible],
        "selected_replacement": selected,
        "shadow_nominee": {
            "name": shadow_nominee["name"],
            "failed_checks": shadow_failed_checks,
            "metrics": shadow_nominee["metrics"]["full"],
        },
        "decision": "PAPER_SHADOW_AUDIT_REQUIRED" if selected else "NO_REPLACEMENT_RETAIN_V7",
        "recommended_action": (
            "AUDIT_SELECTED_REPLACEMENT_IN_PAPER_SHADOW"
            if selected
            else "RETAIN_V7_AND_KEEP_SHADOW_NOMINEE_PAPER_ONLY"
        ),
        "production_changed": False,
        "candidates": candidates,
    }
    safe = json_safe(verdict)
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(safe, indent=2, ensure_ascii=True), encoding="utf-8"
    )

    rows = []
    for candidate in candidates:
        metric = candidate["metrics"]["full"]
        rows.append(
            {
                "name": candidate["name"],
                **metric,
                "checks_passed": sum(candidate["checks"].values()),
                "checks_total": len(candidate["checks"]),
                "eligible": candidate["all_replacement_checks_pass"],
                "pareto": candidate["name"] in frontier,
            }
        )
    pd.DataFrame(rows).sort_values(
        ["eligible", "sharpe", "profit_factor", "trades_per_week"],
        ascending=[False, False, False, False],
    ).to_csv(REPORT_ROOT / "candidate_summary.csv", index=False)

    display_names = ["V7_BASELINE"] + [name for name in frontier if name != "V7_BASELINE"]
    lines = [
        "# CORE4 V18 FULL-HISTORY TOURNAMENT",
        "",
        f"**{verdict['decision']}**",
        "",
        f"Data: {verdict['data_period']}; {len(candidates)} portfolio candidates from {len(SLEEVES)} exact sleeves.",
        "",
        *report_table(candidates, display_names),
        "",
        f"Eligible replacements: {', '.join(verdict['eligible_replacements']) or 'none'}.",
        f"Selected replacement: {selected or 'none'}.",
        f"Best paper-shadow nominee: {shadow_nominee['name']}.",
        f"Failed replacement checks: {', '.join(shadow_failed_checks) or 'none'}.",
        f"Recommended action: {verdict['recommended_action']}.",
        "",
        "The whole common history was used, so no result is untouched. A selected candidate would still require fresh paper-forward evidence.",
        "",
    ]
    (REPORT_ROOT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({key: safe[key] for key in (
        "experiment", "data_period", "candidate_count", "pareto_frontier",
        "eligible_replacements", "selected_replacement", "decision",
    )}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
