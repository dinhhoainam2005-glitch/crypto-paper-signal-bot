"""Frozen audit of a robust-consensus router over deterministic rule sleeves.

The rule shortlist is fixed with 2020-2022 data. Consensus requirements are
selected on 2023-2024 only. The final 2025 through July 2026 period is opened
once, after one policy has been locked.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / ".codex_deps", ROOT, ROOT / "research"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import r32b_online_expert_router as r32b  # noqa: E402


OUTPUT_DIR = ROOT / "r32d_output" / "reports" / "R32D_consensus_sleeve_router"
VOTE_GRID = (2, 3, 4, 6, 8)
FAMILY_GRID = (1, 2, 3)
MARGIN_GRID = (0, 1, 2, 3)


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


def development_score(registry: pd.DataFrame) -> pd.Series:
    return (
        registry["development_profit_factor"].clip(upper=3.0) * 30.0
        + registry["development_win_rate"] * 20.0
        + registry["development_sharpe"].clip(lower=-2.0, upper=4.0) * 5.0
        + registry["development_trades_per_week"].clip(upper=4.0) * 2.0
    )


def build_consensus(events: pd.DataFrame, registry: pd.DataFrame) -> pd.DataFrame:
    registry = registry.copy()
    registry["development_score"] = development_score(registry)
    enriched = events.merge(
        registry[["candidate_id", "development_score"]],
        on="candidate_id",
        how="left",
        validate="many_to_one",
    )
    keys = ["entry_time", "asset", "timeframe", "side"]
    votes = enriched.groupby(keys, as_index=False).agg(
        votes=("candidate_id", "nunique"),
        families=("family", "nunique"),
        consensus_score=("development_score", "sum"),
    )
    representative = enriched.sort_values(
        [*keys, "development_score"], ascending=[True, True, True, True, False]
    ).drop_duplicates(keys, keep="first")
    consensus = representative.merge(votes, on=keys, how="inner", validate="one_to_one")
    pair_keys = ["entry_time", "asset", "timeframe"]
    totals = consensus.groupby(pair_keys)["votes"].transform("sum")
    consensus["opposite_votes"] = totals - consensus["votes"]
    consensus["vote_margin"] = consensus["votes"] - consensus["opposite_votes"]
    return consensus.sort_values("entry_time").reset_index(drop=True)


def select_policy(
    consensus: pd.DataFrame, min_votes: int, min_families: int, min_margin: int
) -> pd.DataFrame:
    eligible = consensus.loc[
        consensus["votes"].ge(min_votes)
        & consensus["families"].ge(min_families)
        & consensus["vote_margin"].ge(min_margin)
    ].copy()
    eligible["online_score"] = (
        eligible["votes"] * 20.0
        + eligible["families"] * 10.0
        + eligible["vote_margin"] * 5.0
        + eligible["consensus_score"] / eligible["votes"].clip(lower=1)
    )
    return r32b.portfolio_select(eligible)


def calibration_score(base: dict[str, Any], stress: dict[str, Any]) -> tuple[float, bool]:
    weeks = (r32b.CALIBRATION_END - r32b.DEVELOPMENT_END).days / 7.0
    one_hour_tpw = base["trades_1h"] / weeks
    four_hour_tpw = base["trades_4h"] / weeks
    direction_min = min(base["long_trades"], base["short_trades"])
    passed = bool(
        base["trades"] >= 200
        and base["profit_factor"] >= 1.30
        and stress["profit_factor"] >= 1.10
        and base["max_drawdown_pct"] >= -15.0
        and one_hour_tpw >= 1.0
        and four_hour_tpw >= 0.8
        and direction_min >= 20
    )
    frequency = min(one_hour_tpw / 7.0, 1.0) + min(four_hour_tpw / 2.0, 1.0)
    score = (
        min(base["profit_factor"], stress["profit_factor"], 4.0) * 45.0
        + base["win_rate"] * 25.0
        + min(base["sharpe"], 5.0) * 5.0
        + frequency * 8.0
        + min(direction_min / 40.0, 1.0) * 5.0
        + base["max_drawdown_pct"] / 5.0
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
    events, registry = r32b.build_library()
    consensus = build_consensus(events, registry)

    search_rows: list[dict[str, Any]] = []
    locked: tuple[int, int, int] | None = None
    locked_portfolio = pd.DataFrame()
    locked_rank = (False, -math.inf)
    for min_votes in VOTE_GRID:
        for min_families in FAMILY_GRID:
            for min_margin in MARGIN_GRID:
                portfolio = select_policy(consensus, min_votes, min_families, min_margin)
                base = r32b.metrics(
                    portfolio,
                    r32b.DEVELOPMENT_END,
                    r32b.CALIBRATION_END,
                    r32b.BASE_COST_BPS,
                )
                stress = r32b.metrics(
                    portfolio,
                    r32b.DEVELOPMENT_END,
                    r32b.CALIBRATION_END,
                    r32b.STRESS_COST_BPS,
                )
                score, passed = calibration_score(base, stress)
                search_rows.append(
                    {
                        "min_votes": min_votes,
                        "min_families": min_families,
                        "min_vote_margin": min_margin,
                        "selection_score": score,
                        "selection_pass": passed,
                        **{f"calibration_{key}": value for key, value in base.items()},
                        **{f"calibration_stress_{key}": value for key, value in stress.items()},
                    }
                )
                rank = (passed, score)
                if rank > locked_rank:
                    locked = (min_votes, min_families, min_margin)
                    locked_portfolio = portfolio
                    locked_rank = rank
    if locked is None:
        raise RuntimeError("No consensus policy was evaluated")

    periods = {
        "calibration_2023_2024": (r32b.DEVELOPMENT_END, r32b.CALIBRATION_END),
        "frozen_2025_2026m07": (r32b.CALIBRATION_END, r32b.FROZEN_END),
        "recent_2026m01_m07": (pd.Timestamp("2026-01-01", tz="UTC"), r32b.FROZEN_END),
    }
    summary_rows: list[dict[str, Any]] = []
    for period, (start, end) in periods.items():
        for cost in (r32b.BASE_COST_BPS, r32b.STRESS_COST_BPS):
            summary_rows.append(
                {"period": period, "cost_bps": cost, **r32b.metrics(locked_portfolio, start, end, cost)}
            )
    summary = pd.DataFrame(summary_rows)
    frozen = locked_portfolio.loc[
        locked_portfolio["entry_time"].ge(r32b.CALIBRATION_END)
        & locked_portfolio["entry_time"].lt(r32b.FROZEN_END)
    ].copy()
    route_rows: list[dict[str, Any]] = []
    for keys, group in frozen.groupby(["asset", "timeframe", "side"], sort=True):
        route_rows.append(
            {
                "asset": keys[0],
                "timeframe": keys[1],
                "side": keys[2],
                **r32b.metrics(group, r32b.CALIBRATION_END, r32b.FROZEN_END, r32b.BASE_COST_BPS),
            }
        )
    routes = pd.DataFrame(route_rows)

    frozen_base = summary.loc[
        summary["period"].eq("frozen_2025_2026m07")
        & summary["cost_bps"].eq(r32b.BASE_COST_BPS)
    ].iloc[0]
    frozen_stress = summary.loc[
        summary["period"].eq("frozen_2025_2026m07")
        & summary["cost_bps"].eq(r32b.STRESS_COST_BPS)
    ].iloc[0]
    weeks = (r32b.FROZEN_END - r32b.CALIBRATION_END).days / 7.0
    one_hour_tpw = float(frozen_base["trades_1h"] / weeks)
    four_hour_tpw = float(frozen_base["trades_4h"] / weeks)
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
        and one_hour_tpw >= 1.0
        and four_hour_tpw >= 0.8
    )
    verdict = {
        "audit_id": "R32D_ROBUST_CONSENSUS_SLEEVE_ROUTER",
        "candidate_library_size": int(len(registry)),
        "raw_candidate_events": int(len(events)),
        "consensus_events": int(len(consensus)),
        "policies_evaluated": int(len(search_rows)),
        "locked_policy": {
            "minimum_votes": locked[0],
            "minimum_families": locked[1],
            "minimum_direction_margin": locked[2],
        },
        "calibration_gate_pass": bool(locked_rank[0]),
        "frozen_1h_trades_per_week": one_hour_tpw,
        "frozen_4h_trades_per_week": four_hour_tpw,
        "frozen_metrics_12bps": frozen_base.to_dict(),
        "frozen_metrics_20bps": frozen_stress.to_dict(),
        "promotion_gate_pass": promotion,
        "production_change_allowed": promotion,
        "decision": "PROMOTE_R32D_CONSENSUS" if promotion else "REJECT_R32D_CONSENSUS",
    }

    pd.DataFrame(search_rows).sort_values(
        ["selection_pass", "selection_score"], ascending=[False, False]
    ).to_csv(args.output_dir / "R32D_POLICY_SEARCH.csv", index=False)
    summary.to_csv(args.output_dir / "R32D_SUMMARY.csv", index=False)
    routes.to_csv(args.output_dir / "R32D_FROZEN_ROUTES.csv", index=False)
    frozen.to_csv(args.output_dir / "R32D_FROZEN_TRADES.csv", index=False)
    (args.output_dir / "R32D_VERDICT.json").write_text(
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
        "# R32D Robust Consensus Sleeve Router",
        "",
        "## Decision",
        "",
        f"**{verdict['decision']}**",
        "",
        "Rules are shortlisted with 2020-2022 only. Consensus policy selection uses "
        "2023-2024 only. The 2025 through July 2026 result is frozen.",
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
    (args.output_dir / "R32D_REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
