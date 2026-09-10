"""Causal online expert router for balanced 1h and 4h crypto signals.

R32B freezes a deterministic rule library using 2020-2022 development data,
selects one online-performance policy on 2023-2024 calibration data, and opens
the 2025 through July 2026 window once. At every signal timestamp, an expert is
scored only with trades that had already exited at that timestamp.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / ".codex_deps", ROOT, ROOT / "research"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import r29c_regime_ensemble_search as library  # noqa: E402


OUTPUT_DIR = ROOT / "r32b_output" / "reports" / "R32B_online_expert_router"
TIMEFRAMES = ("1h", "4h")
DEVELOPMENT_START = pd.Timestamp("2020-01-01", tz="UTC")
DEVELOPMENT_END = pd.Timestamp("2023-01-01", tz="UTC")
CALIBRATION_END = pd.Timestamp("2025-01-01", tz="UTC")
FROZEN_END = pd.Timestamp("2026-08-01", tz="UTC")
BASE_COST_BPS = 12.0
STRESS_COST_BPS = 20.0
RISK_FRACTION = 0.25
MAX_POSITIONS = 4
LOOKBACK_DAYS = (180, 365, 730)
MIN_HISTORY = (8, 12, 20)
MIN_PF20 = (1.05, 1.15, 1.30)


@dataclass(frozen=True)
class Policy:
    lookback_days: int
    min_history: int
    min_pf20: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def candidate_key(result: library.CandidateResult) -> str:
    return result.candidate_id


def development_shortlist(
    results: list[library.CandidateResult], timeframe: str
) -> list[library.CandidateResult]:
    minimum = 35 if timeframe == "1h" else 20
    eligible = [
        result
        for result in results
        if result.train["trades"] >= minimum
        and result.train["profit_factor"] >= 0.90
        and result.train["avg_net_bps"] >= -2.0
    ]
    groups: dict[tuple[str, str], list[library.CandidateResult]] = {}
    for result in eligible:
        groups.setdefault((result.direction, result.family), []).append(result)
    selected: list[library.CandidateResult] = []
    for group in groups.values():
        ranked = sorted(
            group,
            key=lambda result: (
                min(float(result.train["profit_factor"]), 3.0) * 30.0
                + float(result.train["win_rate"]) * 20.0
                + min(float(result.train["sharpe"]), 4.0) * 5.0
                + min(float(result.train["trades_per_week"]), 4.0) * 2.0
            ),
            reverse=True,
        )
        selected.extend(ranked[:2])
    return selected


def trade_rows(
    frame: pd.DataFrame, result: library.CandidateResult
) -> pd.DataFrame:
    indices = result.selected_indices
    entry = frame["px_open"].shift(-1)
    exit_price = frame["px_open"].shift(-(result.hold_bars + 1))
    sign = 1.0 if result.direction == "LONG" else -1.0
    rows = pd.DataFrame(
        {
            "candidate_id": candidate_key(result),
            "asset": result.asset,
            "timeframe": result.timeframe,
            "side": result.direction,
            "family": result.family,
            "signal_time": frame.loc[indices, "signal_time"].to_numpy(),
            "entry_time": frame.loc[indices, "signal_time"].to_numpy(),
            "exit_time": (
                frame.loc[indices, "signal_time"]
                + pd.to_timedelta(
                    result.hold_bars * library.TF_MINUTES[result.timeframe], unit="m"
                )
            ).to_numpy(),
            "entry_price": entry.iloc[indices].to_numpy(dtype=float),
            "exit_price": exit_price.iloc[indices].to_numpy(dtype=float),
            "regime": frame.loc[indices, "regime"].to_numpy(),
            "hold_bars": result.hold_bars,
        }
    )
    rows["gross_return"] = sign * (
        rows["exit_price"] / rows["entry_price"] - 1.0
    )
    rows["net_return_12bps"] = rows["gross_return"] - BASE_COST_BPS / 10000.0
    rows["net_return_20bps"] = rows["gross_return"] - STRESS_COST_BPS / 10000.0
    return rows.dropna(subset=["gross_return"]).sort_values("entry_time")


def build_library() -> tuple[pd.DataFrame, pd.DataFrame]:
    regimes = library.load_btc_regimes()
    event_parts: list[pd.DataFrame] = []
    registry_rows: list[dict[str, Any]] = []
    for timeframe in TIMEFRAMES:
        breadth = library.load_breadth(timeframe)
        for asset in library.ASSETS:
            frame = library.load_cell(asset, timeframe, regimes).merge(
                breadth, on="bar_open", how="left", validate="one_to_one"
            )
            results = library.evaluate_cell(frame, asset, timeframe)
            shortlisted = development_shortlist(results, timeframe)
            for result in shortlisted:
                rows = trade_rows(frame, result)
                event_parts.append(rows)
                registry_rows.append(
                    {
                        "candidate_id": result.candidate_id,
                        "asset": result.asset,
                        "timeframe": result.timeframe,
                        "side": result.direction,
                        "family": result.family,
                        "hold_bars": result.hold_bars,
                        "params_json": json.dumps(result.params, sort_keys=True),
                        **{f"development_{key}": value for key, value in result.train.items()},
                    }
                )
            print(
                f"cell={asset} {timeframe} all={len(results)} shortlisted={len(shortlisted)}",
                flush=True,
            )
    events = pd.concat(event_parts, ignore_index=True)
    events = events.sort_values(["candidate_id", "entry_time"]).reset_index(drop=True)
    registry = pd.DataFrame(registry_rows).drop_duplicates("candidate_id")
    return events, registry


def rolling_expert_stats(events: pd.DataFrame) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for _, group in events.groupby("candidate_id", sort=False):
        group = group.sort_values("entry_time").copy()
        entry_ns = group["entry_time"].astype("int64").to_numpy()
        exit_ns = group["exit_time"].astype("int64").to_numpy()
        returns = group["net_return_20bps"].to_numpy(dtype=float)
        for lookback_days in LOOKBACK_DAYS:
            window_ns = int(pd.Timedelta(days=lookback_days).value)
            history: deque[int] = deque()
            pending = 0
            counts = np.zeros(len(group), dtype=np.int32)
            pfs = np.zeros(len(group), dtype=float)
            means = np.zeros(len(group), dtype=float)
            wins = np.zeros(len(group), dtype=float)
            for index, now in enumerate(entry_ns):
                while pending < index and exit_ns[pending] <= now:
                    history.append(pending)
                    pending += 1
                cutoff = now - window_ns
                while history and exit_ns[history[0]] < cutoff:
                    history.popleft()
                if not history:
                    continue
                values = returns[np.fromiter(history, dtype=np.int64)]
                positive = float(values[values > 0.0].sum())
                negative = float(values[values < 0.0].sum())
                counts[index] = len(values)
                pfs[index] = (
                    positive / abs(negative)
                    if negative < 0.0
                    else (10.0 if positive > 0.0 else 0.0)
                )
                means[index] = float(np.mean(values) * 10000.0)
                wins[index] = float(np.mean(values > 0.0))
            suffix = str(lookback_days)
            group[f"history_count_{suffix}"] = counts
            group[f"history_pf20_{suffix}"] = pfs
            group[f"history_mean_bps20_{suffix}"] = means
            group[f"history_win_{suffix}"] = wins
        parts.append(group)
    return pd.concat(parts, ignore_index=True).sort_values("entry_time").reset_index(drop=True)


def eligible_events(events: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    suffix = str(policy.lookback_days)
    count = events[f"history_count_{suffix}"]
    pf = events[f"history_pf20_{suffix}"]
    mean_bps = events[f"history_mean_bps20_{suffix}"]
    win = events[f"history_win_{suffix}"]
    selected = events.loc[
        count.ge(policy.min_history)
        & pf.ge(policy.min_pf20)
        & mean_bps.gt(0.0)
    ].copy()
    shrink = count.loc[selected.index] / (count.loc[selected.index] + 20.0)
    selected["online_score"] = (
        shrink * mean_bps.loc[selected.index]
        + np.log(pf.loc[selected.index].clip(lower=0.1, upper=10.0)) * 12.0
        + (win.loc[selected.index] - 0.5) * 20.0
    )
    return selected


def portfolio_select(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    work = events.sort_values(
        ["entry_time", "online_score"], ascending=[True, False]
    ).drop_duplicates(["entry_time", "asset"], keep="first")
    selected: list[int] = []
    active: list[tuple[pd.Timestamp, str]] = []
    for entry_time, group in work.groupby("entry_time", sort=True):
        active = [(exit_time, asset) for exit_time, asset in active if exit_time > entry_time]
        active_assets = {asset for _, asset in active}
        for index, row in group.sort_values("online_score", ascending=False).iterrows():
            if row["asset"] in active_assets or len(active) >= MAX_POSITIONS:
                continue
            selected.append(index)
            active.append((row["exit_time"], row["asset"]))
            active_assets.add(row["asset"])
    return work.loc[selected].sort_values("exit_time").reset_index(drop=True)


def max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    equity = np.cumprod(1.0 + np.clip(returns, -0.99, 10.0))
    equity = np.concatenate(([1.0], equity))
    return float(np.min(equity / np.maximum.accumulate(equity) - 1.0))


def metrics(
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_bps: float,
) -> dict[str, Any]:
    subset = trades.loc[trades["entry_time"].ge(start) & trades["entry_time"].lt(end)]
    gross = subset["gross_return"].to_numpy(dtype=float)
    returns = RISK_FRACTION * (gross - cost_bps / 10000.0)
    days = max((end - start).total_seconds() / 86400.0, 1.0)
    tpw = float(len(returns) / days * 7.0)
    wins = float(returns[returns > 0.0].sum())
    losses = float(returns[returns < 0.0].sum())
    pf = wins / abs(losses) if losses < 0.0 else (math.inf if wins > 0.0 else 0.0)
    std = float(np.std(returns)) if len(returns) else 0.0
    sharpe = (
        float(np.mean(returns)) / std * math.sqrt(max(tpw * 52.0, 1.0))
        if std > 0.0
        else 0.0
    )
    return {
        "trades": int(len(returns)),
        "trades_per_week": tpw,
        "win_rate": float(np.mean(returns > 0.0)) if len(returns) else 0.0,
        "profit_factor": float(pf),
        "sharpe": float(sharpe),
        "max_drawdown_pct": max_drawdown(returns) * 100.0,
        "avg_net_bps": float(np.mean(returns) * 10000.0) if len(returns) else 0.0,
        "long_trades": int(subset["side"].eq("LONG").sum()),
        "short_trades": int(subset["side"].eq("SHORT").sum()),
        "trades_1h": int(subset["timeframe"].eq("1h").sum()),
        "trades_4h": int(subset["timeframe"].eq("4h").sum()),
    }


def calibration_score(base: dict[str, Any], stress: dict[str, Any]) -> tuple[float, bool]:
    one_hour_tpw = base["trades_1h"] / ((CALIBRATION_END - DEVELOPMENT_END).days / 7.0)
    four_hour_tpw = base["trades_4h"] / ((CALIBRATION_END - DEVELOPMENT_END).days / 7.0)
    direction_min = min(base["long_trades"], base["short_trades"])
    passed = bool(
        base["trades"] >= 300
        and base["profit_factor"] >= 1.20
        and stress["profit_factor"] >= 1.05
        and base["max_drawdown_pct"] >= -20.0
        and direction_min >= 30
        and one_hour_tpw >= 2.0
        and four_hour_tpw >= 1.0
    )
    frequency = min(one_hour_tpw / 7.0, 1.0) + min(four_hour_tpw / 2.0, 1.0)
    score = (
        min(base["profit_factor"], stress["profit_factor"], 3.0) * 40.0
        + base["win_rate"] * 25.0
        + min(base["sharpe"], 5.0) * 5.0
        + frequency * 8.0
        + min(direction_min / 50.0, 1.0) * 6.0
        + base["max_drawdown_pct"] / 6.0
    )
    return float(score), passed


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_No rows._"
    display = frame.loc[:, columns].copy()
    for column in display.columns:
        if pd.api.types.is_float_dtype(display[column]):
            display[column] = display[column].map(
                lambda value: f"{value:.4f}" if pd.notna(value) else ""
            )
    header = "| " + " | ".join(display.columns) + " |"
    separator = "| " + " | ".join(["---"] * len(display.columns)) + " |"
    body = [
        "| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |"
        for row in display.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *body])


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_events, registry = build_library()
    events = rolling_expert_stats(raw_events)

    policy_rows: list[dict[str, Any]] = []
    locked: Policy | None = None
    locked_portfolio = pd.DataFrame()
    locked_rank = (False, -math.inf)
    for lookback in LOOKBACK_DAYS:
        for min_history in MIN_HISTORY:
            for min_pf in MIN_PF20:
                policy = Policy(lookback, min_history, min_pf)
                portfolio = portfolio_select(eligible_events(events, policy))
                base = metrics(portfolio, DEVELOPMENT_END, CALIBRATION_END, BASE_COST_BPS)
                stress = metrics(portfolio, DEVELOPMENT_END, CALIBRATION_END, STRESS_COST_BPS)
                score, passed = calibration_score(base, stress)
                row = {
                    "lookback_days": lookback,
                    "min_history": min_history,
                    "min_pf20": min_pf,
                    "selection_score": score,
                    "selection_pass": passed,
                }
                row.update({f"calibration_{key}": value for key, value in base.items()})
                row.update({f"calibration_stress_{key}": value for key, value in stress.items()})
                policy_rows.append(row)
                rank = (passed, score)
                if rank > locked_rank:
                    locked = policy
                    locked_portfolio = portfolio
                    locked_rank = rank

    if locked is None:
        raise RuntimeError("No R32B policy was evaluated")

    periods = {
        "calibration_2023_2024": (DEVELOPMENT_END, CALIBRATION_END),
        "frozen_2025_2026m07": (CALIBRATION_END, FROZEN_END),
        "recent_2026m01_m07": (pd.Timestamp("2026-01-01", tz="UTC"), FROZEN_END),
    }
    summary_rows: list[dict[str, Any]] = []
    for period, (start, end) in periods.items():
        for cost in (BASE_COST_BPS, STRESS_COST_BPS):
            summary_rows.append(
                {"period": period, "cost_bps": cost, **metrics(locked_portfolio, start, end, cost)}
            )
    summary = pd.DataFrame(summary_rows)
    frozen = locked_portfolio.loc[
        locked_portfolio["entry_time"].ge(CALIBRATION_END)
        & locked_portfolio["entry_time"].lt(FROZEN_END)
    ].copy()

    route_rows: list[dict[str, Any]] = []
    for keys, group in frozen.groupby(["asset", "timeframe", "side"], sort=True):
        route_rows.append(
            {
                "asset": keys[0],
                "timeframe": keys[1],
                "side": keys[2],
                **metrics(group, CALIBRATION_END, FROZEN_END, BASE_COST_BPS),
            }
        )
    routes = pd.DataFrame(route_rows)

    frozen_base = summary.loc[
        summary["period"].eq("frozen_2025_2026m07")
        & summary["cost_bps"].eq(BASE_COST_BPS)
    ].iloc[0]
    frozen_stress = summary.loc[
        summary["period"].eq("frozen_2025_2026m07")
        & summary["cost_bps"].eq(STRESS_COST_BPS)
    ].iloc[0]
    duration_weeks = (FROZEN_END - CALIBRATION_END).days / 7.0
    one_hour_tpw = float(frozen_base["trades_1h"] / duration_weeks)
    four_hour_tpw = float(frozen_base["trades_4h"] / duration_weeks)
    promotion = bool(
        locked_rank[0]
        and frozen_base["trades"] >= 150
        and frozen_base["win_rate"] >= 0.55
        and frozen_base["profit_factor"] >= 1.50
        and frozen_stress["profit_factor"] >= 1.20
        and frozen_base["sharpe"] >= 2.0
        and frozen_base["max_drawdown_pct"] >= -15.0
        and frozen_base["long_trades"] >= 30
        and frozen_base["short_trades"] >= 30
        and one_hour_tpw >= 2.0
        and four_hour_tpw >= 1.0
    )
    verdict = {
        "audit_id": "R32B_CAUSAL_ONLINE_EXPERT_ROUTER",
        "candidate_library_size": int(len(registry)),
        "candidate_event_count": int(len(events)),
        "policies_evaluated": int(len(policy_rows)),
        "locked_policy": {
            "lookback_days": locked.lookback_days,
            "min_completed_history": locked.min_history,
            "minimum_trailing_pf_20bps": locked.min_pf20,
        },
        "calibration_gate_pass": bool(locked_rank[0]),
        "frozen_1h_trades_per_week": one_hour_tpw,
        "frozen_4h_trades_per_week": four_hour_tpw,
        "frozen_metrics_12bps": frozen_base.to_dict(),
        "frozen_metrics_20bps": frozen_stress.to_dict(),
        "promotion_gate_pass": promotion,
        "production_change_allowed": promotion,
        "decision": "PROMOTE_R32B_CHALLENGER" if promotion else "REJECT_R32B_CHALLENGER",
    }

    registry.to_csv(args.output_dir / "R32B_CANDIDATE_REGISTRY.csv", index=False)
    pd.DataFrame(policy_rows).sort_values(
        ["selection_pass", "selection_score"], ascending=[False, False]
    ).to_csv(args.output_dir / "R32B_POLICY_SEARCH.csv", index=False)
    summary.to_csv(args.output_dir / "R32B_SUMMARY.csv", index=False)
    routes.to_csv(args.output_dir / "R32B_FROZEN_ROUTES.csv", index=False)
    frozen.to_csv(args.output_dir / "R32B_FROZEN_TRADES.csv", index=False)
    (args.output_dir / "R32B_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )

    summary_columns = [
        "period",
        "cost_bps",
        "trades",
        "trades_per_week",
        "win_rate",
        "profit_factor",
        "sharpe",
        "max_drawdown_pct",
        "long_trades",
        "short_trades",
        "trades_1h",
        "trades_4h",
    ]
    route_columns = [
        "asset",
        "timeframe",
        "side",
        "trades",
        "trades_per_week",
        "win_rate",
        "profit_factor",
        "sharpe",
        "max_drawdown_pct",
    ]
    report = [
        "# R32B Causal Online Expert Router",
        "",
        "## Decision",
        "",
        f"**{verdict['decision']}**",
        "",
        "Candidate definitions and shortlist use 2020-2022 only. The online policy "
        "uses 2023-2024 only. Frozen 2025 through July 2026 metrics are not used "
        "for selection.",
        "",
        "Each live decision sees only expert trades whose exits were already known. "
        "Positions are deconflicted by asset and capped at four portfolio positions.",
        "",
        "## Locked policy",
        "",
        f"Lookback `{locked.lookback_days}d`, minimum `{locked.min_history}` completed "
        f"expert trades, trailing PF20 >= `{locked.min_pf20:.2f}`.",
        "",
        "## Summary",
        "",
        markdown_table(summary, summary_columns),
        "",
        "## Frozen routes",
        "",
        markdown_table(routes, route_columns),
        "",
    ]
    (args.output_dir / "R32B_REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
