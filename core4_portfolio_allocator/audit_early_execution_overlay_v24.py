"""Causal viability gate for entering frozen V20 signals on completed 4h bars."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_beta_regime_v7 import audit_side, enrich
from audit_early_trend_continuation_v23 import build_four_hour
from audit_execution_timing_v7 import load_exact_inputs, row_at
from audit_full_history_tournament_v18 import json_safe
from audit_regime_allocator_v19 import POLICIES, build_context, policy_allows
from backtest_allocator import SYMBOLS
from backtest_symmetric_v4 import DATA_ROOT


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "early_execution_overlay_v24"
START = pd.Timestamp("2021-01-01", tz="UTC")
DEVELOPMENT_END = pd.Timestamp("2024-01-01", tz="UTC")
VALIDATION_END = pd.Timestamp("2025-01-01", tz="UTC")
COST_RATE = 0.0020
POLICY = next(item for item in POLICIES if item.name == "TREND_BREADTH2")
FAST_SYMBOLS = ("BNBUSDT", "SOLUSDT")


@dataclass(frozen=True)
class Configuration:
    name: str
    include_fast: bool
    core_confirmations: int


CONFIGURATIONS = (
    Configuration("CORE_C1", False, 1),
    Configuration("CORE_C2", False, 2),
    Configuration("ROUTED_C1", True, 1),
    Configuration("ROUTED_C2", True, 2),
)


def intraday_side(
    daily: dict[str, pd.DataFrame],
    four_hour: dict[str, pd.DataFrame],
    symbol: str,
    bar_open: pd.Timestamp,
    channel: int,
) -> str | None:
    day = bar_open.floor("D")
    prior_day = day - pd.Timedelta(days=1)
    frame = daily[symbol]
    btc_daily = daily["BTCUSDT"]
    bar = row_at(four_hour[symbol], bar_open)
    beta_bar = row_at(four_hour["BTCUSDT"], bar_open)
    level_row = row_at(frame, day)
    beta = row_at(btc_daily, prior_day)
    if bar is None or beta_bar is None or level_row is None or beta is None:
        return None
    high_column = "audit_entry_high"
    low_column = "audit_entry_low"
    required = [level_row[high_column], level_row[low_column], beta["audit_sma"]]
    if any(pd.isna(value) for value in required):
        return None
    if bar["close"] > level_row[high_column] and beta_bar["close"] > beta["audit_sma"]:
        return "LONG"
    if bar["close"] < level_row[low_column] and beta_bar["close"] < beta["audit_sma"]:
        return "SHORT"
    return None


def first_intraday_events(
    daily: dict[str, pd.DataFrame],
    four_hour: dict[str, pd.DataFrame],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[tuple[str, pd.Timestamp], dict[str, list[tuple[str, str, pd.Timestamp]]]]:
    events: dict[tuple[str, pd.Timestamp], dict[str, list[tuple[str, str, pd.Timestamp]]]] = {}
    seen: set[tuple[str, str, pd.Timestamp]] = set()
    common = sorted(set.intersection(*(set(frame.index) for frame in four_hour.values())))
    for bar_open in common:
        signal_close = bar_open + pd.Timedelta(hours=4)
        signal_day = bar_open.floor("D")
        if signal_day < start or signal_day >= end:
            continue
        for sleeve, source in (("CORE", daily["CORE"]), ("FAST", daily["FAST"])):
            raw: list[tuple[str, str, pd.Timestamp]] = []
            for symbol in SYMBOLS:
                key = (sleeve, symbol, signal_day)
                if key in seen:
                    continue
                side = intraday_side(source, four_hour, symbol, bar_open, 55 if sleeve == "CORE" else 20)
                if side is not None:
                    raw.append((symbol, side, signal_close))
            for symbol, side, timestamp in raw:
                seen.add((sleeve, symbol, signal_day))
            if raw:
                events.setdefault((sleeve, signal_day), {}).setdefault(str(signal_close), []).extend(raw)
    return events


def daily_decisions(
    core_daily: dict[str, pd.DataFrame],
    fast_daily: dict[str, pd.DataFrame],
    context: pd.DataFrame,
    day: pd.Timestamp,
) -> dict[str, list[tuple[str, str]]]:
    core = [
        (symbol, side)
        for symbol in SYMBOLS
        if (side := audit_side(core_daily, symbol, day)) is not None
    ]
    fast_raw = [
        (symbol, side)
        for symbol in SYMBOLS
        if (side := audit_side(fast_daily, symbol, day)) is not None
    ]
    counts = {
        side: len({symbol for symbol, item_side in fast_raw if item_side == side})
        for side in ("LONG", "SHORT")
    }
    fast_confirmed = [item for item in fast_raw if counts[item[1]] >= 2]
    if not policy_allows(POLICY, context, day):
        fast_confirmed = []
    fast = [item for item in fast_confirmed if item[0] in FAST_SYMBOLS]
    return {"CORE": core, "FAST": fast}


def choose_early(
    configuration: Configuration,
    intraday: dict[str, list[tuple[str, str, pd.Timestamp]]],
    sleeve: str,
    prior_day_context_allowed: bool,
) -> list[tuple[str, str, pd.Timestamp]]:
    selected: list[tuple[str, str, pd.Timestamp]] = []
    for timestamp in sorted(intraday):
        items = intraday[timestamp]
        counts = {
            side: len({symbol for symbol, item_side, _ in items if item_side == side})
            for side in ("LONG", "SHORT")
        }
        minimum = configuration.core_confirmations if sleeve == "CORE" else 2
        for item in items:
            symbol, side, _ = item
            if counts[side] < minimum:
                continue
            if sleeve == "FAST" and (not configuration.include_fast or not prior_day_context_allowed):
                continue
            if sleeve == "FAST" and symbol not in FAST_SYMBOLS:
                continue
            selected.append(item)
    unique: dict[tuple[str, str], tuple[str, str, pd.Timestamp]] = {}
    for item in selected:
        unique.setdefault((item[0], item[1]), item)
    return list(unique.values())


def build_event_frame(
    configuration: Configuration,
    core_daily: dict[str, pd.DataFrame],
    fast_daily: dict[str, pd.DataFrame],
    hourly: dict[str, pd.DataFrame],
    context: pd.DataFrame,
    intraday_events: dict[tuple[str, pd.Timestamp], dict[str, list[tuple[str, str, pd.Timestamp]]]],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, int]:
    rows: list[dict[str, Any]] = []
    daily_total = 0
    for day in pd.date_range(start, end - pd.Timedelta(days=1), freq="1D", tz="UTC"):
        decisions = daily_decisions(core_daily, fast_daily, context, day)
        allowed_sleeves = ("CORE", "FAST") if configuration.include_fast else ("CORE",)
        daily_total += sum(len(decisions[sleeve]) for sleeve in allowed_sleeves)
        prior_day = day - pd.Timedelta(days=1)
        prior_allowed = policy_allows(POLICY, context, prior_day)
        for sleeve in allowed_sleeves:
            intraday = intraday_events.get((sleeve, day), {})
            early = choose_early(configuration, intraday, sleeve, prior_allowed)
            final = set(decisions[sleeve])
            benchmark_time = day + pd.Timedelta(days=1)
            for symbol, side, entry_time in early:
                entry_row = row_at(hourly[symbol], entry_time)
                exit_row = row_at(hourly[symbol], benchmark_time)
                if entry_row is None or exit_row is None:
                    continue
                entry = float(entry_row["open"])
                benchmark = float(exit_row["open"])
                direction = 1.0 if side == "LONG" else -1.0
                confirmed = (symbol, side) in final
                confirmed_advantage = direction * (benchmark - entry) / benchmark * 10_000.0
                false_break_net = direction * (benchmark - entry) / entry * 10_000.0 - 2.0 * COST_RATE * 10_000.0
                rows.append(
                    {
                        "configuration": configuration.name,
                        "sleeve": sleeve,
                        "symbol": symbol,
                        "side": side,
                        "signal_day": day,
                        "early_entry_time": entry_time,
                        "daily_entry_time": benchmark_time,
                        "lead_hours": (benchmark_time - entry_time).total_seconds() / 3600.0,
                        "early_entry": entry,
                        "daily_entry": benchmark,
                        "confirmed": confirmed,
                        "confirmed_advantage_bps": confirmed_advantage if confirmed else 0.0,
                        "false_break_net_bps": false_break_net if not confirmed else 0.0,
                        "opportunity_benefit_bps": confirmed_advantage if confirmed else false_break_net,
                    }
                )
    return pd.DataFrame(rows), daily_total


def metrics(events: pd.DataFrame, daily_total: int, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    scoped = events.loc[(events["signal_day"] >= start) & (events["signal_day"] < end)].copy()
    confirmed = scoped.loc[scoped["confirmed"]]
    false_breaks = scoped.loc[~scoped["confirmed"]]
    weeks = (end - start).total_seconds() / (7.0 * 86_400.0)
    return {
        "early_events": len(scoped),
        "daily_signals": daily_total,
        "early_events_per_week": len(scoped) / weeks,
        "precision": float(scoped["confirmed"].mean()) if not scoped.empty else 0.0,
        "coverage": len(confirmed) / daily_total if daily_total else 0.0,
        "median_lead_hours": float(scoped["lead_hours"].median()) if not scoped.empty else 0.0,
        "mean_confirmed_advantage_bps": float(confirmed["confirmed_advantage_bps"].mean()) if not confirmed.empty else 0.0,
        "mean_false_break_net_bps": float(false_breaks["false_break_net_bps"].mean()) if not false_breaks.empty else 0.0,
        "mean_opportunity_benefit_bps": float(scoped["opportunity_benefit_bps"].mean()) if not scoped.empty else 0.0,
        "false_breaks": len(false_breaks),
    }


def viability_checks(development: dict[str, Any], validation: dict[str, Any]) -> dict[str, bool]:
    return {
        "development_events_ge_80": development["early_events"] >= 80,
        "development_precision_ge_70pct": development["precision"] >= 0.70,
        "development_coverage_ge_60pct": development["coverage"] >= 0.60,
        "development_lead_ge_4h": development["median_lead_hours"] >= 4.0,
        "development_confirmed_advantage_positive": development["mean_confirmed_advantage_bps"] > 0.0,
        "development_opportunity_positive": development["mean_opportunity_benefit_bps"] > 0.0,
        "validation_events_ge_25": validation["early_events"] >= 25,
        "validation_precision_ge_65pct": validation["precision"] >= 0.65,
        "validation_coverage_ge_55pct": validation["coverage"] >= 0.55,
        "validation_confirmed_advantage_positive": validation["mean_confirmed_advantage_bps"] > 0.0,
        "validation_opportunity_positive": validation["mean_opportunity_benefit_bps"] > 0.0,
    }


def main() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    official, hourly, _ = load_exact_inputs(DATA_ROOT)
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
    intraday_events = first_intraday_events(sources, four_hour, START, end)
    periods = {
        "DEVELOPMENT_2021_2023": (START, DEVELOPMENT_END),
        "VALIDATION_2024": (DEVELOPMENT_END, VALIDATION_END),
        "FROZEN_2025_PLUS": (VALIDATION_END, end),
        "FULL": (START, end),
    }
    results: list[dict[str, Any]] = []
    frames: dict[str, pd.DataFrame] = {}
    for configuration in CONFIGURATIONS:
        print(f"V24 {configuration.name}", flush=True)
        frame, _ = build_event_frame(
            configuration,
            core_daily,
            fast_daily,
            hourly,
            context,
            intraday_events,
            START,
            end,
        )
        frames[configuration.name] = frame
        period_metrics: dict[str, Any] = {}
        for name, (period_start, period_end) in periods.items():
            decisions = 0
            for day in pd.date_range(period_start, period_end - pd.Timedelta(days=1), freq="1D", tz="UTC"):
                daily = daily_decisions(core_daily, fast_daily, context, day)
                decisions += len(daily["CORE"])
                if configuration.include_fast:
                    decisions += len(daily["FAST"])
            period_metrics[name] = metrics(frame, decisions, period_start, period_end)
        checks = viability_checks(
            period_metrics["DEVELOPMENT_2021_2023"], period_metrics["VALIDATION_2024"]
        )
        results.append(
            {
                "configuration": configuration.name,
                "parameters": {
                    "include_fast": configuration.include_fast,
                    "core_confirmations": configuration.core_confirmations,
                },
                "metrics": period_metrics,
                "checks": checks,
                "viability_pass": all(checks.values()),
            }
        )
    eligible = [result for result in results if result["viability_pass"]]
    selected = max(
        eligible if eligible else results,
        key=lambda item: (
            item["viability_pass"],
            sum(item["checks"].values()),
            item["metrics"]["VALIDATION_2024"]["mean_opportunity_benefit_bps"],
            item["metrics"]["DEVELOPMENT_2021_2023"]["mean_opportunity_benefit_bps"],
        ),
    )
    selected_name = selected["configuration"]
    frames[selected_name].to_csv(REPORT_ROOT / "selected_events.csv", index=False)
    summary_rows = []
    for result in results:
        row = {
            "configuration": result["configuration"],
            "checks_passed": sum(result["checks"].values()),
            "checks_total": len(result["checks"]),
            "viability_pass": result["viability_pass"],
        }
        for period, values in result["metrics"].items():
            for key, value in values.items():
                row[f"{period}_{key}"] = value
        summary_rows.append(row)
    pd.DataFrame(summary_rows).to_csv(REPORT_ROOT / "configuration_summary.csv", index=False)
    verdict = {
        "experiment": "CORE4_V24_EARLY_EXECUTION_OVERLAY",
        "period": f"{START.date()}/{(end - pd.Timedelta(days=1)).date()}",
        "configuration_count": len(CONFIGURATIONS),
        "selected_configuration": selected_name,
        "selected_from_eligible_set": bool(eligible),
        "selected": selected,
        "stage_two_authorized": bool(eligible),
        "decision": "RUN_V24_TRANCHE_SIMULATION" if eligible else "REJECT_V24_EARLY_ENTRY",
        "runtime_changed": False,
        "real_money_authorized": False,
        "configurations": results,
    }
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8"
    )
    lines = [
        "# CORE4 V24 EARLY EXECUTION OVERLAY",
        "",
        f"**{verdict['decision']}**",
        "",
        f"Selected diagnostic configuration: `{selected_name}`.",
        f"Viability checks: {sum(selected['checks'].values())}/{len(selected['checks'])}.",
        "",
        "| Period | Events/wk | Precision | Coverage | Lead | Confirmed advantage | False-break net | Opportunity |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for period, values in selected["metrics"].items():
        lines.append(
            f"| {period} | {values['early_events_per_week']:.3f} | {values['precision']:.1%} | "
            f"{values['coverage']:.1%} | {values['median_lead_hours']:.1f}h | "
            f"{values['mean_confirmed_advantage_bps']:+.1f} bps | "
            f"{values['mean_false_break_net_bps']:+.1f} bps | "
            f"{values['mean_opportunity_benefit_bps']:+.1f} bps |"
        )
    lines.extend(["", "V20 production and real-money locks were not changed."])
    (REPORT_ROOT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_safe({key: value for key, value in verdict.items() if key != "configurations"}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
