"""R25B quality audit from available CSV report ledgers.

This script is intentionally offline-only. It does not read the parquet research
warehouse because the current runtime lacks a parquet engine. The output is a
source-backed audit of the ledgers already present in this workspace.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_signal_bot.strategy import CANDIDATES, PORTFOLIO_METRICS  # noqa: E402


ASSETS = ("BTC", "ETH", "SOL", "BNB")
TIMEFRAMES = ("15m", "1h", "4h", "1d")
DIRECTIONS = ("LONG", "SHORT")

SPLITS = {
    "train": (pd.Timestamp("2019-01-01T00:00:00Z"), pd.Timestamp("2024-01-01T00:00:00Z")),
    "validation": (pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2025-01-01T00:00:00Z")),
    "frozen": (pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2026-08-01T00:00:00Z")),
    "recent": (pd.Timestamp("2026-08-01T00:00:00Z"), pd.Timestamp("2026-09-01T00:00:00Z")),
}

FREQUENCY_TARGET_TPW = {
    "15m": 8.0,
    "1h": 7.0,
    "4h": 2.0,
    "1d": 0.9,
}
FREQUENCY_TOLERANCE = 0.1
QUALITY_GATES = {
    "profit_factor": 1.60,
    "sharpe": 2.00,
    "win_rate": 0.55,
    "max_drawdown_pct": -15.0,
}

SOURCE_FILES = {
    "R15C_STRICT_TAKER_FLOW": ROOT
    / "r15c_output"
    / "reports"
    / "R15C_strict_row_stress_selector"
    / "R15C_SELECTED_EDGE_TRADES.csv",
    "R22A_TREND_ROUTER": ROOT
    / "r22a_output"
    / "reports"
    / "R22A_trend_participation_router_audit"
    / "R22A_SELECTED_HISTORICAL_TRADES.csv",
    "R22B_REGIME_SLEEVE": ROOT
    / "r22b_output"
    / "reports"
    / "R22B_regime_sleeve_selector_audit"
    / "R22B_PASSING_SLEEVES_HISTORICAL_TRADES.csv",
    "R23A_SELECTED_EXPANDED": ROOT
    / "r23a_output"
    / "reports"
    / "R23A_quality_frequency_frontier_audit"
    / "R23A_SELECTED_HISTORICAL_TRADES.csv",
    "R23A_SELECTED_RECENT": ROOT
    / "r23a_output"
    / "reports"
    / "R23A_quality_frequency_frontier_audit"
    / "R23A_SELECTED_RECENT_TRADES.csv",
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def weeks_between(start: pd.Timestamp, end: pd.Timestamp) -> float:
    seconds = max((end - start).total_seconds(), 1.0)
    return seconds / (7.0 * 24.0 * 3600.0)


def max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns.astype("float64")).cumprod()
    equity = pd.concat([pd.Series([1.0]), equity], ignore_index=True)
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def max_loss_streak(returns: pd.Series) -> int:
    streak = 0
    worst = 0
    for value in returns:
        if value < 0.0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def metric_block(trades: pd.DataFrame, split: str) -> dict[str, Any]:
    start, end = SPLITS[split]
    if "entry_time" not in trades.columns:
        subset = trades.iloc[0:0].copy()
    else:
        subset = trades.loc[(trades["entry_time"] >= start) & (trades["entry_time"] < end)].copy()
    subset = subset.sort_values("entry_time") if "entry_time" in subset.columns else subset
    returns = pd.to_numeric(subset.get("net_return", pd.Series(dtype="float64")), errors="coerce").fillna(0.0)
    count = int(len(returns))
    wins = returns.loc[returns > 0.0]
    losses = returns.loc[returns < 0.0]
    gross_win = float(wins.sum())
    gross_loss = float(losses.sum())
    pf = gross_win / abs(gross_loss) if gross_loss < 0.0 else (999.0 if gross_win > 0.0 else 0.0)
    avg = float(returns.mean()) if count else 0.0
    std = float(returns.std(ddof=0)) if count else 0.0
    trades_per_week = count / weeks_between(start, end)
    scale = math.sqrt(max(trades_per_week * 52.0, 1.0))
    sharpe = avg / std * scale if std > 0.0 else (999.0 if avg > 0.0 and count else 0.0)
    downside = returns.loc[returns < 0.0]
    down_std = float(downside.std(ddof=0)) if len(downside) else 0.0
    sortino = avg / down_std * scale if down_std > 0.0 else (999.0 if avg > 0.0 and count else 0.0)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    return {
        "split": split,
        "trades": count,
        "trades_per_week": trades_per_week,
        "win_rate": float((returns > 0.0).mean()) if count else 0.0,
        "profit_factor": pf,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": max_drawdown(returns) * 100.0,
        "total_return_pct": ((1.0 + returns).prod() - 1.0) * 100.0 if count else 0.0,
        "avg_return_bps": avg * 10000.0,
        "median_return_bps": float(returns.median() * 10000.0) if count else 0.0,
        "avg_win_bps": avg_win * 10000.0,
        "avg_loss_bps": avg_loss * 10000.0,
        "max_win_bps": float(returns.max() * 10000.0) if count else 0.0,
        "max_loss_bps": float(returns.min() * 10000.0) if count else 0.0,
        "max_loss_streak": max_loss_streak(returns),
    }


def load_trades(label: str, path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["source_set"] = label
    if "entry_time" in frame.columns:
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True, errors="coerce")
    if "exit_time" in frame.columns:
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
    if "net_return" in frame.columns:
        frame["net_return"] = pd.to_numeric(frame["net_return"], errors="coerce").fillna(0.0)
    if "candidate_id" not in frame.columns:
        frame["candidate_id"] = ""
    if "composite_id" not in frame.columns:
        frame["composite_id"] = ""
    if "risk_fraction" not in frame.columns:
        frame["risk_fraction"] = pd.NA
    for column in ("asset", "timeframe", "direction", "family"):
        if column not in frame.columns:
            frame[column] = ""
    return frame


def taker_legacy_id(candidate: dict[str, Any]) -> str | None:
    if candidate["family"] != "taker_flow_breakout":
        return None
    params = candidate["params"]
    return (
        f"taker_flow_breakout_{candidate['direction']}_{candidate['timeframe']}"
        f"_lb{int(params['lb'])}_h{int(candidate['hold_bars'])}"
        f"_b{float(params['buffer'])}_vz{float(params['volz_min'])}"
        f"_f{float(params['flow_thr'])}"
    )


def current_candidates_frame() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in CANDIDATES:
        row = asdict(item)
        row["legacy_source_id"] = taker_legacy_id(row) or row["candidate_id"]
        rows.append(row)
    return pd.DataFrame(rows)


def current_config_matrix(current: pd.DataFrame, ledgers: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for asset in ASSETS:
        for timeframe in TIMEFRAMES:
            for direction in DIRECTIONS:
                group = current.loc[
                    (current["asset"] == asset)
                    & (current["timeframe"] == timeframe)
                    & (current["direction"] == direction)
                ]
                legacy_ids = sorted(group["legacy_source_id"].astype(str).unique()) if not group.empty else []
                evidence_sources: list[str] = []
                evidence_ids: list[str] = []
                for legacy_id in legacy_ids:
                    mask = ledgers["candidate_id"].astype(str).eq(legacy_id)
                    mask = mask | ledgers["composite_id"].astype(str).str.contains(legacy_id, regex=False, na=False)
                    matches = ledgers.loc[mask]
                    if not matches.empty:
                        evidence_sources.extend(sorted(matches["source_set"].dropna().astype(str).unique()))
                        evidence_ids.append(legacy_id)
                rows.append(
                    {
                        "asset": asset,
                        "timeframe": timeframe,
                        "direction": direction,
                        "configured": bool(len(group)),
                        "configured_candidate_count": int(len(group)),
                        "families": "|".join(sorted(group["family"].astype(str).unique())) if not group.empty else "",
                        "source_evidence": "YES" if evidence_ids else "NO",
                        "evidence_candidate_count": len(evidence_ids),
                        "evidence_sources": "|".join(sorted(set(evidence_sources))),
                        "candidate_ids": "||".join(group["candidate_id"].astype(str).tolist()) if not group.empty else "",
                        "legacy_source_ids": "||".join(legacy_ids),
                        "status": "TRADE_CONFIGURED" if len(group) else "NO_TRADE_STRATEGY",
                    }
                )
    return pd.DataFrame(rows)


def match_current_source_backed(current: pd.DataFrame, ledgers: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for _, row in current.iterrows():
        legacy_id = str(row["legacy_source_id"])
        candidate_id = str(row["candidate_id"])
        if row["family"] == "taker_flow_breakout":
            for source_name in ("R15C_STRICT_TAKER_FLOW", "R23A_SELECTED_RECENT"):
                source = ledgers.get(source_name, pd.DataFrame())
                if source.empty:
                    continue
                mask = source["candidate_id"].astype(str).eq(legacy_id)
                mask = mask | source["composite_id"].astype(str).str.contains(legacy_id, regex=False, na=False)
                subset = source.loc[mask].copy()
                if not subset.empty:
                    subset["matched_current_candidate_id"] = candidate_id
                    subset["matched_source_id"] = legacy_id
                    frames.append(subset)
        else:
            found_history = False
            for source_name in ("R23A_SELECTED_EXPANDED", "R22B_REGIME_SLEEVE", "R22A_TREND_ROUTER"):
                source = ledgers.get(source_name, pd.DataFrame())
                if source.empty:
                    continue
                subset = source.loc[source["candidate_id"].astype(str).eq(legacy_id)].copy()
                if not subset.empty:
                    subset["matched_current_candidate_id"] = candidate_id
                    subset["matched_source_id"] = legacy_id
                    frames.append(subset)
                    found_history = True
                    break
            recent = ledgers.get("R23A_SELECTED_RECENT", pd.DataFrame())
            if found_history and not recent.empty:
                subset = recent.loc[recent["candidate_id"].astype(str).eq(legacy_id)].copy()
                if not subset.empty:
                    subset["matched_current_candidate_id"] = candidate_id
                    subset["matched_source_id"] = legacy_id
                    frames.append(subset)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["source_set"] = "R24A_CURRENT_SOURCE_BACKED_PARTIAL"
    return out


def add_benchmark_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["target_trades_per_week"] = out["timeframe"].map(FREQUENCY_TARGET_TPW).fillna(0.0)
    out["target_with_tolerance"] = (out["target_trades_per_week"] - FREQUENCY_TOLERANCE).clip(lower=0.0)
    out["frequency_pass"] = out["trades_per_week"] >= out["target_with_tolerance"]
    out["pf_pass"] = out["profit_factor"] >= QUALITY_GATES["profit_factor"]
    out["sharpe_pass"] = out["sharpe"] >= QUALITY_GATES["sharpe"]
    out["win_pass"] = out["win_rate"] >= QUALITY_GATES["win_rate"]
    out["drawdown_pass"] = out["max_drawdown_pct"] >= QUALITY_GATES["max_drawdown_pct"]
    quality_cols = ["frequency_pass", "pf_pass", "sharpe_pass", "win_pass", "drawdown_pass"]
    out["gate_pass_count"] = out[quality_cols].sum(axis=1)
    out["quality_status"] = out["gate_pass_count"].map(lambda value: "PASS" if value == len(quality_cols) else "FAIL")
    return out


def summarize_trades(source_name: str, trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    trade_cols = ["source_set", "asset", "timeframe", "direction", "net_return", "entry_time", "exit_time"]
    trades = trades.loc[:, [col for col in trade_cols if col in trades.columns]].copy()
    trades = trades.dropna(subset=["entry_time"])
    group_cols = ["asset", "timeframe", "direction"]
    for (asset, timeframe, direction), group in trades.groupby(group_cols, dropna=False):
        for split in SPLITS:
            rows.append(
                {
                    "source_set": source_name,
                    "asset": asset,
                    "timeframe": timeframe,
                    "direction": direction,
                    **metric_block(group, split),
                }
            )
    for split in SPLITS:
        rows.append(
            {
                "source_set": source_name,
                "asset": "ALL",
                "timeframe": "ALL",
                "direction": "ALL",
                **metric_block(trades, split),
            }
        )
    return add_benchmark_columns(pd.DataFrame(rows))


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def render_table(frame: pd.DataFrame, columns: list[str], limit: int | None = None) -> list[str]:
    subset = frame.loc[:, columns].copy()
    if limit is not None:
        subset = subset.head(limit)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for _, row in subset.iterrows():
        values = []
        for column in columns:
            value = row[column]
            if isinstance(value, float):
                if "rate" in column:
                    values.append(f"{value:.1%}")
                elif column.endswith("_pct") or "drawdown" in column:
                    values.append(f"{value:.2f}")
                elif column.endswith("_bps"):
                    values.append(f"{value:.1f}")
                else:
                    values.append(f"{value:.3f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def write_report(
    report_path: Path,
    config: pd.DataFrame,
    metrics: pd.DataFrame,
    r23_verdict: dict[str, Any],
    source_info: list[dict[str, Any]],
) -> None:
    current_cells = config.loc[config["configured"]].copy()
    source_cells = current_cells.loc[current_cells["source_evidence"] == "YES"].copy()
    frozen = metrics.loc[metrics["split"] == "frozen"].copy()
    current_frozen = frozen.loc[frozen["source_set"] == "R24A_CURRENT_SOURCE_BACKED_PARTIAL"].copy()
    current_frozen = current_frozen.loc[current_frozen["asset"] != "ALL"].sort_values(
        ["timeframe", "asset", "direction"]
    )
    r23_frozen = frozen.loc[
        (frozen["source_set"] == "R23A_SELECTED_EXPANDED") & (frozen["asset"] != "ALL")
    ].sort_values(["timeframe", "asset", "direction"])
    lines: list[str] = [
        "# R25B Bot Quality Audit From Available Report Ledgers",
        "",
        "## Scope",
        "",
        "- Current bot code inspected: R24A_STRICT_QUALITY_R15C_BNB_PAPER_OBSERVATION.",
        "- Production forward ledger is not usable for PF/win audit because the Render status endpoint currently keeps only one scan and no stored signals.",
        "- Parquet full-history recomputation was not performed in this run because the available Python runtime has neither pyarrow nor fastparquet.",
        "- Metrics below are recomputed from existing CSV trade ledgers; rows marked source-backed are stronger evidence than hardcoded strategy constants.",
        "",
        "## Current Bot Configuration Coverage",
        "",
        f"- Configured trade cells: {len(current_cells)}/64.",
        f"- Source-backed configured cells: {len(source_cells)}/{len(current_cells)}.",
        "- No configured trade rules for 15m or 1d. SOL is context-only in the deployed R24A trade layer.",
        "",
    ]
    coverage_cols = [
        "asset",
        "timeframe",
        "direction",
        "configured_candidate_count",
        "families",
        "source_evidence",
        "evidence_candidate_count",
        "evidence_sources",
    ]
    lines.extend(render_table(current_cells.loc[:, coverage_cols], coverage_cols))
    lines.extend(
        [
            "",
            "## R24A Reported Portfolio Metrics",
            "",
            "| Sample | TPW | PF | Sharpe | Win | Max DD % |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
            "| Frozen | {tpw:.3f} | {pf:.3f} | {sh:.3f} | {win:.1%} | {dd:.2f} |".format(
                tpw=safe_float(PORTFOLIO_METRICS.get("frozen_trades_per_week")),
                pf=safe_float(PORTFOLIO_METRICS.get("frozen_profit_factor")),
                sh=safe_float(PORTFOLIO_METRICS.get("frozen_sharpe")),
                win=safe_float(PORTFOLIO_METRICS.get("frozen_win_rate")),
                dd=safe_float(PORTFOLIO_METRICS.get("frozen_max_drawdown_pct")),
            ),
            "| Validation | {tpw:.3f} | {pf:.3f} | {sh:.3f} | {win:.1%} | {dd:.2f} |".format(
                tpw=safe_float(PORTFOLIO_METRICS.get("validation_trades_per_week")),
                pf=safe_float(PORTFOLIO_METRICS.get("validation_profit_factor")),
                sh=safe_float(PORTFOLIO_METRICS.get("validation_sharpe")),
                win=safe_float(PORTFOLIO_METRICS.get("validation_win_rate")),
                dd=safe_float(PORTFOLIO_METRICS.get("validation_max_drawdown_pct")),
            ),
            "| Recent | {tpw:.3f} | {pf:.3f} | {sh:.3f} | {win:.1%} | {dd:.2f} |".format(
                tpw=safe_float(PORTFOLIO_METRICS.get("recent_trades_per_week")),
                pf=safe_float(PORTFOLIO_METRICS.get("recent_profit_factor")),
                sh=safe_float(PORTFOLIO_METRICS.get("recent_sharpe")),
                win=safe_float(PORTFOLIO_METRICS.get("recent_win_rate")),
                dd=safe_float(PORTFOLIO_METRICS.get("recent_max_drawdown_pct")),
            ),
            "",
            "These portfolio metrics are present in the bot code, but this audit did not find the matching R24A per-cell ledger.",
            "",
            "## Source-Backed R24A-Like Components, Frozen Split",
            "",
        ]
    )
    metric_cols = [
        "asset",
        "timeframe",
        "direction",
        "trades",
        "trades_per_week",
        "target_with_tolerance",
        "profit_factor",
        "sharpe",
        "win_rate",
        "max_drawdown_pct",
        "avg_return_bps",
        "quality_status",
    ]
    if current_frozen.empty:
        lines.append("No source-backed current-like rows found.")
    else:
        lines.extend(render_table(current_frozen, metric_cols))
    lines.extend(
        [
            "",
            "## R23A Expanded Portfolio, Frozen Split",
            "",
            "R23A is useful as a comparison because it increased frequency and diversified SHORT coverage, but its own selected portfolio failed the strict quality gate.",
            "",
        ]
    )
    if not r23_frozen.empty:
        lines.extend(render_table(r23_frozen, metric_cols))
    lines.extend(
        [
            "",
            "## R23A Portfolio Verdict",
            "",
            f"- Selected status: {r23_verdict.get('result', 'n/a')}",
            f"- Quality status: {r23_verdict.get('selected_metrics', {}).get('quality_status', 'n/a')}",
            f"- Quality fail reasons: {r23_verdict.get('selected_metrics', {}).get('quality_fail_reasons', 'n/a')}",
            "",
            "## Source Files",
            "",
        ]
    )
    for item in source_info:
        lines.append(f"- {item['source_set']}: rows={item['rows']} path={item['path']}")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "1. The current deployed trade layer is narrow: 5 configured cells out of 64 symbol/TF/side combinations.",
            "2. The 1h layer does not meet the user's requested frequency of about one trade per day.",
            "3. R23A proves that higher frequency is possible, but the quality gate deteriorated; it is not a clean upgrade candidate by itself.",
            "4. The next research step should rebuild a true full-history parquet audit when pyarrow/fastparquet is available, then optimize a multi-sleeve bot with explicit latency, freshness, and no-replay constraints.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    out_dir = ROOT / "data" / "quality_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    ledgers = {name: load_trades(name, path) for name, path in SOURCE_FILES.items()}
    all_ledgers = pd.concat([frame for frame in ledgers.values() if not frame.empty], ignore_index=True)
    current = current_candidates_frame()
    config = current_config_matrix(current, all_ledgers)

    current_backed = match_current_source_backed(current, ledgers)
    source_metrics: list[pd.DataFrame] = []
    if not current_backed.empty:
        source_metrics.append(summarize_trades("R24A_CURRENT_SOURCE_BACKED_PARTIAL", current_backed))
    for source_name in ("R15C_STRICT_TAKER_FLOW", "R22B_REGIME_SLEEVE", "R23A_SELECTED_EXPANDED", "R23A_SELECTED_RECENT"):
        frame = ledgers.get(source_name, pd.DataFrame())
        if not frame.empty:
            source_metrics.append(summarize_trades(source_name, frame))
    metrics = pd.concat(source_metrics, ignore_index=True) if source_metrics else pd.DataFrame()

    source_info = [
        {"source_set": name, "path": str(path), "rows": int(len(ledgers.get(name, pd.DataFrame())))}
        for name, path in SOURCE_FILES.items()
    ]
    r23_verdict = read_json(
        ROOT
        / "r23a_output"
        / "reports"
        / "R23A_quality_frequency_frontier_audit"
        / "R23A_FINAL_VERDICT.json"
    )
    config.to_csv(out_dir / "R25B_current_config_matrix.csv", index=False)
    if not metrics.empty:
        metrics.to_csv(out_dir / "R25B_cell_metrics.csv", index=False)
    summary = {
        "audit_id": "R25B_QUALITY_AUDIT_FROM_REPORT_LEDGERS",
        "current_strategy_id": "R24A_STRICT_QUALITY_R15C_BNB_PAPER_OBSERVATION",
        "configured_trade_cells": int(config["configured"].sum()),
        "configured_trade_candidates": int(current.shape[0]),
        "source_backed_configured_cells": int(
            config.loc[config["configured"] & config["source_evidence"].eq("YES")].shape[0]
        ),
        "frequency_target_tpw": FREQUENCY_TARGET_TPW,
        "frequency_tolerance": FREQUENCY_TOLERANCE,
        "quality_gates": QUALITY_GATES,
        "portfolio_metrics_in_code": PORTFOLIO_METRICS,
        "source_info": source_info,
        "limitations": [
            "No independent R24A per-cell ledger found in the workspace.",
            "No parquet recomputation because pyarrow/fastparquet is unavailable in the current runtime.",
            "Production Render state currently exposes no forward signal history to compute live PF/win.",
            "CSV ledger metrics are historical research metrics, not a live-money performance guarantee.",
        ],
    }
    (out_dir / "R25B_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_report(out_dir / "R25B_quality_audit_report.md", config, metrics, r23_verdict, source_info)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
