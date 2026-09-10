"""Causal BTC-to-altcoin lead-lag diffusion audit for 1h and 4h signals."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / ".codex_deps", ROOT, ROOT / "research"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import r33a_cross_sectional_relative_strength as pair  # noqa: E402


OUTPUT_DIR = ROOT / "r33b_output" / "reports" / "R33B_BTC_lead_lag_diffusion"
ALT_ASSETS = ("ETH", "SOL", "BNB")
LOOKBACKS = (1, 3, 6, 12)
HOLD_GRID = {"1h": (1, 3, 6), "4h": (1, 2, 3)}
SIGMA_GRID = (1.0, 1.75, 2.5)
LAG_RATIO_GRID = (0.5, 0.8)
BREADTH_MAX_GRID = (2, 4)
FLOW_GRID = (-2.0, -0.05, 0.0)
VOLUME_GRID = (-99.0, -1.0, 0.0)


@dataclass(frozen=True)
class Candidate:
    timeframe: str
    lookback: int
    hold: int
    sigma_min: float
    lag_ratio_max: float
    breadth_max: int
    alt_flow_min: float
    btc_volume_min: float

    @property
    def candidate_id(self) -> str:
        payload = json.dumps(self.__dict__, sort_keys=True)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
        return f"r33b_{self.timeframe}_btc_diffusion_{digest}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def json_safe(value: Any) -> Any:
    return pair.json_safe(value)


def candidates(timeframe: str) -> list[Candidate]:
    return [
        Candidate(timeframe, lookback, hold, sigma, lag_ratio, breadth, flow_min, volume_min)
        for lookback in LOOKBACKS
        for hold in HOLD_GRID[timeframe]
        for sigma in SIGMA_GRID
        for lag_ratio in LAG_RATIO_GRID
        for breadth in BREADTH_MAX_GRID
        for flow_min in FLOW_GRID
        for volume_min in VOLUME_GRID
    ]


def candidate_trades(
    index: pd.DatetimeIndex,
    frames: dict[str, pd.DataFrame],
    regimes: pd.Series,
    candidate: Candidate,
) -> pd.DataFrame:
    close = {
        asset: frames[asset]["px_close"].to_numpy(dtype=float)
        for asset in ("BTC", *ALT_ASSETS)
    }
    open_price = {
        asset: frames[asset]["px_open"].to_numpy(dtype=float)
        for asset in ALT_ASSETS
    }
    btc_log_return = np.log(close["BTC"] / np.roll(close["BTC"], 1))
    btc_log_return[0] = np.nan
    btc_rv = pd.Series(btc_log_return).rolling(24, min_periods=16).std(ddof=0).to_numpy()
    btc_momentum = close["BTC"] / np.roll(close["BTC"], candidate.lookback) - 1.0
    btc_momentum[: candidate.lookback] = np.nan
    direction = np.sign(btc_momentum)
    leader_move = np.abs(btc_momentum)
    leader_sigma = leader_move / (btc_rv * math.sqrt(candidate.lookback))

    alt_momentum = np.column_stack(
        [
            close[asset] / np.roll(close[asset], candidate.lookback) - 1.0
            for asset in ALT_ASSETS
        ]
    )
    alt_momentum[: candidate.lookback, :] = np.nan
    directional_alt = alt_momentum * direction[:, None]
    lag_gap = leader_move[:, None] - directional_alt
    chosen_index = np.nanargmax(
        np.where(np.isfinite(lag_gap), lag_gap, -np.inf), axis=1
    )
    rows = np.arange(len(index))
    chosen_directional_momentum = directional_alt[rows, chosen_index]
    chosen_gap = lag_gap[rows, chosen_index]
    breadth = 1 + np.sum(directional_alt > 0.0, axis=1)

    alt_flow = np.column_stack(
        [frames[asset]["flow"].to_numpy(dtype=float) for asset in ALT_ASSETS]
    )
    chosen_flow = alt_flow[rows, chosen_index] * direction
    btc_volume = frames["BTC"]["quote_volume_prior_z_20"].to_numpy(dtype=float)

    entry_matrix = np.column_stack(
        [np.roll(open_price[asset], -1) for asset in ALT_ASSETS]
    )
    exit_matrix = np.column_stack(
        [np.roll(open_price[asset], -(candidate.hold + 1)) for asset in ALT_ASSETS]
    )
    entry_matrix[-1:, :] = np.nan
    exit_matrix[-(candidate.hold + 1) :, :] = np.nan
    entry = entry_matrix[rows, chosen_index]
    exit_price = exit_matrix[rows, chosen_index]
    gross = direction * (exit_price / entry - 1.0)
    flow_ok = (
        np.ones(len(index), dtype=bool)
        if candidate.alt_flow_min <= -1.5
        else chosen_flow >= candidate.alt_flow_min
    )
    volume_ok = (
        np.ones(len(index), dtype=bool)
        if candidate.btc_volume_min <= -90.0
        else btc_volume >= candidate.btc_volume_min
    )
    valid = (
        np.isfinite(leader_sigma)
        & np.isfinite(chosen_gap)
        & np.isfinite(gross)
        & (direction != 0.0)
        & (leader_sigma >= candidate.sigma_min)
        & (chosen_gap > 0.0)
        & (chosen_directional_momentum <= leader_move * candidate.lag_ratio_max)
        & (breadth <= candidate.breadth_max)
        & flow_ok
        & volume_ok
    )
    selected = pair.non_overlapping(valid, candidate.hold)
    assets = np.asarray(ALT_ASSETS, dtype=object)[chosen_index[selected]]
    delta = pd.to_timedelta(pair.base.TF_MINUTES[candidate.timeframe], unit="m")
    return pd.DataFrame(
        {
            "candidate_id": candidate.candidate_id,
            "timeframe": candidate.timeframe,
            "signal_time": index[selected],
            "entry_time": index[selected],
            "exit_time": index[selected] + delta * candidate.hold,
            "asset": assets,
            "side": np.where(direction[selected] > 0.0, "LONG", "SHORT"),
            "hold_bars": candidate.hold,
            "lookback": candidate.lookback,
            "leader_sigma": leader_sigma[selected],
            "lag_gap": chosen_gap[selected],
            "breadth": breadth[selected],
            "alt_flow": chosen_flow[selected],
            "btc_volume_z": btc_volume[selected],
            "entry_price": entry[selected],
            "exit_price": exit_price[selected],
            "gross_return": gross[selected],
            "regime": regimes.iloc[selected].to_numpy(),
        }
    )


def metrics(
    trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, cost_bps: float
) -> dict[str, Any]:
    result = pair.metrics(trades, start, end, cost_bps)
    subset = trades.loc[trades["entry_time"].ge(start) & trades["entry_time"].lt(end)]
    result.update(
        {
            "long_trades": int(subset["side"].eq("LONG").sum()),
            "short_trades": int(subset["side"].eq("SHORT").sum()),
        }
    )
    return result


def selection_score(
    train: dict[str, Any], validation: dict[str, Any], stress: dict[str, Any], timeframe: str
) -> tuple[float, bool]:
    target = 7.0 if timeframe == "1h" else 2.0
    minimum = 100 if timeframe == "1h" else 40
    direction_min = min(validation["long_trades"], validation["short_trades"])
    stable_pf = min(train["profit_factor"], validation["profit_factor"], stress["profit_factor"])
    passed = bool(
        train["trades"] >= minimum
        and validation["trades"] >= minimum
        and train["profit_factor"] >= 1.05
        and validation["profit_factor"] >= 1.20
        and stress["profit_factor"] >= 1.05
        and validation["max_drawdown_pct"] >= -15.0
        and direction_min >= minimum // 5
    )
    score = (
        min(stable_pf, 4.0) * 45.0
        + min(train["win_rate"], validation["win_rate"]) * 25.0
        + min(validation["sharpe"], 5.0) * 5.0
        + min(validation["trades_per_week"] / target, 1.0) * 10.0
        + min(direction_min / max(minimum / 2, 1), 1.0) * 5.0
        + validation["max_drawdown_pct"] / 5.0
    )
    return float(score), passed


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    search_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    frozen_parts: list[pd.DataFrame] = []
    for timeframe in pair.TIMEFRAMES:
        index, frames, regimes = pair.build_panel(timeframe)
        best: tuple[bool, float, Candidate, pd.DataFrame] | None = None
        for candidate in candidates(timeframe):
            trades = candidate_trades(index, frames, regimes, candidate)
            train = metrics(trades, pair.TRAIN_START, pair.TRAIN_END, pair.BASE_COST_BPS)
            validation = metrics(
                trades, pair.TRAIN_END, pair.VALIDATION_END, pair.BASE_COST_BPS
            )
            stress = metrics(
                trades, pair.TRAIN_END, pair.VALIDATION_END, pair.STRESS_COST_BPS
            )
            score, passed = selection_score(train, validation, stress, timeframe)
            search_rows.append(
                {
                    **candidate.__dict__,
                    "candidate_id": candidate.candidate_id,
                    "selection_score": score,
                    "selection_pass": passed,
                    **{f"train_{key}": value for key, value in train.items()},
                    **{f"validation_{key}": value for key, value in validation.items()},
                    **{f"validation_stress_{key}": value for key, value in stress.items()},
                }
            )
            rank = (passed, score)
            if best is None or rank > best[:2]:
                best = (passed, score, candidate, trades)
        if best is None:
            raise RuntimeError(f"No candidate for {timeframe}")
        passed, score, candidate, trades = best
        frozen = trades.loc[
            trades["entry_time"].ge(pair.VALIDATION_END)
            & trades["entry_time"].lt(pair.FROZEN_END)
        ].copy()
        base_result = metrics(
            frozen, pair.VALIDATION_END, pair.FROZEN_END, pair.BASE_COST_BPS
        )
        stress_result = metrics(
            frozen, pair.VALIDATION_END, pair.FROZEN_END, pair.STRESS_COST_BPS
        )
        selected_rows.append(
            {
                **candidate.__dict__,
                "candidate_id": candidate.candidate_id,
                "selection_score": score,
                "selection_pass": passed,
                **{f"frozen_{key}": value for key, value in base_result.items()},
                **{f"frozen_stress_{key}": value for key, value in stress_result.items()},
            }
        )
        frozen_parts.append(frozen)
        print(
            f"tf={timeframe} candidate={candidate.candidate_id} pass={passed} "
            f"frozen={len(frozen)} pf12={base_result['profit_factor']:.3f}",
            flush=True,
        )

    selected = pd.DataFrame(selected_rows)
    frozen_trades = pd.concat(frozen_parts, ignore_index=True)
    summary_rows: list[dict[str, Any]] = []
    for timeframe, group in zip(pair.TIMEFRAMES, frozen_parts):
        for cost in (pair.BASE_COST_BPS, pair.STRESS_COST_BPS):
            summary_rows.append(
                {
                    "scope": timeframe,
                    "cost_bps": cost,
                    **metrics(group, pair.VALIDATION_END, pair.FROZEN_END, cost),
                }
            )
    summary = pd.DataFrame(summary_rows)
    promotion = True
    for timeframe in pair.TIMEFRAMES:
        base_row = summary.loc[
            summary["scope"].eq(timeframe) & summary["cost_bps"].eq(pair.BASE_COST_BPS)
        ].iloc[0]
        stress_row = summary.loc[
            summary["scope"].eq(timeframe) & summary["cost_bps"].eq(pair.STRESS_COST_BPS)
        ].iloc[0]
        target = 7.0 if timeframe == "1h" else 2.0
        promotion &= bool(
            base_row["trades"] >= 50
            and base_row["trades_per_week"] >= target * 0.7
            and base_row["win_rate"] >= 0.55
            and base_row["profit_factor"] >= 1.50
            and stress_row["profit_factor"] >= 1.20
            and min(base_row["long_trades"], base_row["short_trades"]) >= 20
            and base_row["max_drawdown_pct"] >= -15.0
        )
    promotion = bool(promotion and selected["selection_pass"].all())
    verdict = {
        "audit_id": "R33B_BTC_LEAD_LAG_DIFFUSION",
        "candidates_evaluated": len(search_rows),
        "locked_candidates": selected.to_dict(orient="records"),
        "promotion_gate_pass": promotion,
        "production_change_allowed": promotion,
        "decision": "PROMOTE_R33B_DIFFUSION" if promotion else "REJECT_R33B_DIFFUSION",
    }

    pd.DataFrame(search_rows).sort_values(
        ["selection_pass", "selection_score"], ascending=[False, False]
    ).to_csv(args.output_dir / "R33B_CANDIDATE_SEARCH.csv", index=False)
    selected.to_csv(args.output_dir / "R33B_LOCKED_CANDIDATES.csv", index=False)
    summary.to_csv(args.output_dir / "R33B_FROZEN_SUMMARY.csv", index=False)
    frozen_trades.to_csv(args.output_dir / "R33B_FROZEN_TRADES.csv", index=False)
    (args.output_dir / "R33B_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )
    columns = [
        "scope",
        "cost_bps",
        "trades",
        "trades_per_week",
        "win_rate",
        "profit_factor",
        "sharpe",
        "max_drawdown_pct",
        "long_trades",
        "short_trades",
    ]
    report = [
        "# R33B BTC Lead-Lag Diffusion",
        "",
        "## Decision",
        "",
        f"**{verdict['decision']}**",
        "",
        "The candidate reacts to a closed BTC impulse and enters the most lagging "
        "altcoin at the immediately following open. Selection uses 2020-2024 and "
        "the 2025 through July 2026 period is frozen.",
        "",
        "## Frozen results",
        "",
        pair.markdown_table(summary, columns),
        "",
    ]
    (args.output_dir / "R33B_REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
