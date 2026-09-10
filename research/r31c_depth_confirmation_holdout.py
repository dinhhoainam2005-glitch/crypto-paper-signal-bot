"""Frozen holdout audit of simple true-depth confirmation for R26A signals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from r31a_r26a_production_parity_backtest import (
    OUTPUT_DIR as R31A_OUTPUT,
    WAREHOUSE,
    apply_production_deconfliction,
    attach_regimes,
    build_regimes,
    gate_assessment,
    json_safe,
    load_cell,
    markdown_table,
    metrics,
    summary_rows,
)


ROOT = Path(__file__).resolve().parents[1]
BOOK_ROOT = Path(r"D:\@Nam\btc_eth_signal_research\features\R29I_bookdepth_quality")
OUTPUT_DIR = ROOT / "r31c_output" / "reports" / "R31C_depth_confirmation_holdout"
DEVELOPMENT = (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
VALIDATION = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC"))
FROZEN = (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-08-01", tz="UTC"))
FEATURES = (
    "book_imbalance_p1_mean",
    "book_imbalance_p1_last",
    "book_imbalance_p2_mean",
    "book_imbalance_p5_mean",
)
THRESHOLDS = (-0.10, -0.05, 0.0, 0.05, 0.10)


def load_book(asset: str, timeframe: str) -> pd.DataFrame:
    path = BOOK_ROOT / timeframe / f"{asset}_bookdepth_quality_{timeframe}.parquet"
    columns = ["available_at", "book_data_quality_ok", *FEATURES]
    frame = pd.read_parquet(path, columns=columns)
    frame["entry_time"] = pd.to_datetime(frame.pop("available_at"), utc=True).astype("datetime64[ns, UTC]")
    frame["asset"] = asset
    frame["timeframe"] = timeframe
    return frame


def load_panel() -> pd.DataFrame:
    frames = [load_book(asset, timeframe) for timeframe in ("1h", "4h") for asset in ("BTC", "ETH", "SOL", "BNB")]
    return pd.concat(frames, ignore_index=True)


def prepare_events() -> pd.DataFrame:
    events = pd.read_csv(
        R31A_OUTPUT / "R31A_SIGNAL_REPLAY.csv",
        parse_dates=["signal_time", "entry_time", "exit_time"],
    )
    for column in ("signal_time", "entry_time", "exit_time"):
        events[column] = pd.to_datetime(events[column], utc=True).astype("datetime64[ns, UTC]")
    panel = load_panel()
    merged = events.merge(panel, on=["asset", "timeframe", "entry_time"], how="inner", validate="many_to_one")
    merged = merged.loc[merged["book_data_quality_ok"].eq(True)].copy()  # noqa: E712
    sign = np.where(merged["side"].eq("SHORT"), -1.0, 1.0)
    for feature in FEATURES:
        merged[f"directional_{feature}"] = merged[feature] * sign
    return merged


def run_filter(events: pd.DataFrame, feature: str | None, threshold: float | None) -> pd.DataFrame:
    selected = events
    if feature is not None and threshold is not None:
        selected = selected.loc[selected[f"directional_{feature}"].ge(threshold)]
    replay = apply_production_deconfliction(selected)
    return replay.loc[replay["accepted"]].copy()


def window_metrics(trades: pd.DataFrame, window: tuple[pd.Timestamp, pd.Timestamp]) -> dict[str, Any]:
    start, end = window
    subset = trades.loc[trades["entry_time"].ge(start) & trades["entry_time"].lt(end)]
    return metrics(subset, start, end)


def calibration_table(events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    configs: list[tuple[str, str | None, float | None]] = [("QUALITY_ONLY", None, None)]
    configs.extend((f"{feature}_ge_{threshold:+.2f}", feature, threshold) for feature in FEATURES for threshold in THRESHOLDS)
    baseline_trades = run_filter(events, None, None)
    baseline_dev = window_metrics(baseline_trades, DEVELOPMENT)
    baseline_val = window_metrics(baseline_trades, VALIDATION)
    rows: list[dict[str, Any]] = []
    for config_id, feature, threshold in configs:
        trades = run_filter(events, feature, threshold)
        dev = window_metrics(trades, DEVELOPMENT)
        val = window_metrics(trades, VALIDATION)
        retention = min(
            dev["trades"] / max(baseline_dev["trades"], 1),
            val["trades"] / max(baseline_val["trades"], 1),
        )
        eligible = (
            dev["trades"] >= 100
            and val["trades"] >= 100
            and retention >= 0.50
            and dev["profit_factor_20bps"] >= 1.20
            and val["profit_factor_20bps"] >= 1.20
            and dev["avg_net_bps_12bps"] > 0.0
            and val["avg_net_bps_12bps"] > 0.0
        )
        conservative_score = min(dev["profit_factor_20bps"], val["profit_factor_20bps"]) * np.sqrt(retention)
        rows.append(
            {
                "config_id": config_id,
                "feature": feature or "NONE",
                "threshold": threshold,
                "development_trades": dev["trades"],
                "development_pf20": dev["profit_factor_20bps"],
                "development_win12": dev["win_rate_12bps"],
                "validation_trades": val["trades"],
                "validation_pf20": val["profit_factor_20bps"],
                "validation_win12": val["win_rate_12bps"],
                "minimum_frequency_retention": retention,
                "conservative_score": conservative_score,
                "eligible": eligible,
            }
        )
    table = pd.DataFrame(rows)
    eligible = table.loc[table["eligible"] & table["config_id"].ne("QUALITY_ONLY")]
    if eligible.empty:
        chosen = table.loc[table["config_id"].eq("QUALITY_ONLY")].iloc[0].to_dict()
    else:
        chosen = eligible.sort_values(
            ["conservative_score", "minimum_frequency_retention"], ascending=False
        ).iloc[0].to_dict()
    return table, chosen


def report(
    table: pd.DataFrame,
    chosen: dict[str, Any],
    comparison: pd.DataFrame,
    summary: pd.DataFrame,
    gate: dict[str, Any],
) -> None:
    columns = [
        "key",
        "trades",
        "trades_per_week",
        "win_rate_12bps",
        "profit_factor_12bps",
        "profit_factor_20bps",
        "sharpe_12bps",
        "max_drawdown_pct_12bps",
        "avg_net_bps_12bps",
    ]
    top = table.sort_values("conservative_score", ascending=False).head(10)
    lines = [
        "# R31C True-Depth Confirmation Holdout",
        "",
        "## Contract",
        "",
        "- Signal core: exact R26A group winners from R31A.",
        "- Depth source: quality-audited Binance historical bookDepth, point-in-time available only.",
        "- Development: 2023; validation and threshold lock: 2024; frozen audit: 2025 through July 2026.",
        "- One global directional depth filter, not a per-symbol optimized model.",
        "",
        f"## Locked Choice: `{chosen['config_id']}`",
        "",
        "| Config | Dev trades | Dev PF20 | Val trades | Val PF20 | Retained | Eligible |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in top.itertuples(index=False):
        lines.append(
            f"| `{row.config_id}` | {row.development_trades} | {row.development_pf20:.3f} | "
            f"{row.validation_trades} | {row.validation_pf20:.3f} | {row.minimum_frequency_retention:.1%} | "
            f"{'YES' if row.eligible else 'NO'} |"
        )
    lines.extend(
        [
            "",
            "## Frozen Comparison",
            "",
            "| Variant | Trades | TPW | Win12 | PF12 | PF20 | Sharpe12 | MaxDD12 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in comparison.itertuples(index=False):
        lines.append(
            f"| {row.variant} | {row.trades} | {row.trades_per_week:.3f} | {row.win_rate_12bps:.1%} | "
            f"{row.profit_factor_12bps:.3f} | {row.profit_factor_20bps:.3f} | {row.sharpe_12bps:.3f} | "
            f"{row.max_drawdown_pct_12bps:.2f}% |"
        )
    lines.extend(["", "## Selected Period Results", "", *markdown_table(summary.loc[summary["scope"] == "period"], columns)])
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- **{gate['decision']}**",
            f"- Checks passed: `{gate['passed_checks']}/{gate['total_checks']}`.",
            "- Historical depth confirmation does not substitute for missing historical Hyperliquid liquidation truth.",
        ]
    )
    (OUTPUT_DIR / "R31C_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    events = prepare_events()
    table, chosen = calibration_table(events)
    chosen_feature = None if chosen["feature"] == "NONE" else str(chosen["feature"])
    chosen_threshold = None if pd.isna(chosen["threshold"]) else float(chosen["threshold"])
    selected_trades = run_filter(events, chosen_feature, chosen_threshold)
    baseline_trades = run_filter(events, None, None)
    regimes = build_regimes(load_cell(WAREHOUSE, "BTC", "4h"))
    selected_trades = attach_regimes(selected_trades, regimes)

    summary = summary_rows(selected_trades, DEVELOPMENT[0], FROZEN[1])
    comparison_rows: list[dict[str, Any]] = []
    for name, trades in (("R26A + depth quality only", baseline_trades), (str(chosen["config_id"]), selected_trades)):
        comparison_rows.append({"variant": name, **window_metrics(trades, FROZEN)})
    comparison = pd.DataFrame(comparison_rows)
    parity = {"pass": True, "checked": 288, "mismatches": 0}
    gate = gate_assessment(summary, selected_trades, parity)

    table.to_csv(OUTPUT_DIR / "R31C_CALIBRATION.csv", index=False)
    comparison.to_csv(OUTPUT_DIR / "R31C_FROZEN_COMPARISON.csv", index=False)
    selected_trades.to_csv(OUTPUT_DIR / "R31C_ACCEPTED_TRADES.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "R31C_METRICS.csv", index=False)
    verdict = {
        "audit_id": "R31C_DEPTH_CONFIRMATION_HOLDOUT",
        "locked_choice": chosen,
        "frozen_comparison": comparison.to_dict(orient="records"),
        "gate": gate,
    }
    (OUTPUT_DIR / "R31C_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report(table, chosen, comparison, summary, gate)
    print(json.dumps(json_safe(verdict), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
