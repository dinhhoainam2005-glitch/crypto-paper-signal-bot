"""Causal cross-sectional LONG/SHORT relative-strength pair audit.

At each completed 1h or 4h candle, the four assets are ranked by
volatility-normalized momentum. A market-neutral pair is formed from the
strongest and weakest asset. Candidate selection uses 2020-2024 only; the 2025
through July 2026 period is evaluated after one candidate per timeframe locks.
"""

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

import r29c_regime_ensemble_search as base  # noqa: E402


OUTPUT_DIR = ROOT / "r33a_output" / "reports" / "R33A_cross_sectional_relative_strength"
TIMEFRAMES = ("1h", "4h")
LOOKBACKS = (3, 6, 12, 24, 48)
HOLD_GRID = {"1h": (3, 6, 12), "4h": (2, 3, 6)}
DISPERSION_GRID = (0.5, 1.0, 1.5, 2.0)
FLOW_GRID = (-2.0, 0.0)
VOLUME_GRID = (-1.0, 0.0)
MODES = ("MOMENTUM", "REVERSAL")
TRAIN_START = pd.Timestamp("2020-09-26", tz="UTC")
TRAIN_END = pd.Timestamp("2023-01-01", tz="UTC")
VALIDATION_END = pd.Timestamp("2025-01-01", tz="UTC")
FROZEN_END = pd.Timestamp("2026-08-01", tz="UTC")
BASE_COST_BPS = 12.0
STRESS_COST_BPS = 20.0
RISK_FRACTION = 0.25


@dataclass(frozen=True)
class Candidate:
    timeframe: str
    mode: str
    lookback: int
    hold: int
    dispersion_min: float
    flow_min: float
    volume_min: float

    @property
    def candidate_id(self) -> str:
        payload = json.dumps(self.__dict__, sort_keys=True)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
        return f"r33a_{self.timeframe}_{self.mode.lower()}_{digest}"


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


def build_panel(timeframe: str) -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame], pd.Series]:
    regimes = base.load_btc_regimes()
    frames: dict[str, pd.DataFrame] = {}
    common: pd.DatetimeIndex | None = None
    for asset in base.ASSETS:
        frame = base.load_cell(asset, timeframe, regimes).copy()
        frame = frame.set_index("signal_time").sort_index()
        frames[asset] = frame
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None:
        raise RuntimeError(f"No data for {timeframe}")
    common = common[(common >= TRAIN_START) & (common < FROZEN_END)]
    for asset in base.ASSETS:
        frames[asset] = frames[asset].reindex(common)
    regime = frames["BTC"]["regime"].fillna("TRANSITION")
    return common, frames, regime


def non_overlapping(mask: np.ndarray, hold: int) -> np.ndarray:
    raw = np.flatnonzero(mask)
    selected: list[int] = []
    next_allowed = -1
    for index in raw:
        if index < next_allowed:
            continue
        selected.append(int(index))
        next_allowed = int(index) + hold
    return np.asarray(selected, dtype=np.int64)


def candidate_trades(
    index: pd.DatetimeIndex,
    frames: dict[str, pd.DataFrame],
    regimes: pd.Series,
    candidate: Candidate,
) -> pd.DataFrame:
    assets = list(base.ASSETS)
    close = np.column_stack([frames[asset]["px_close"].to_numpy(dtype=float) for asset in assets])
    open_price = np.column_stack([frames[asset]["px_open"].to_numpy(dtype=float) for asset in assets])
    flow = np.column_stack([frames[asset]["flow"].to_numpy(dtype=float) for asset in assets])
    volume = np.column_stack(
        [frames[asset]["quote_volume_prior_z_20"].to_numpy(dtype=float) for asset in assets]
    )
    log_return = np.log(close / np.roll(close, 1, axis=0))
    log_return[0, :] = np.nan
    rv = pd.DataFrame(log_return).rolling(24, min_periods=16).std(ddof=0).to_numpy()
    momentum = close / np.roll(close, candidate.lookback, axis=0) - 1.0
    momentum[: candidate.lookback, :] = np.nan
    score = momentum / (rv * math.sqrt(candidate.lookback))
    best = np.nanargmax(np.where(np.isfinite(score), score, -np.inf), axis=1)
    worst = np.nanargmin(np.where(np.isfinite(score), score, np.inf), axis=1)
    valid_score = np.isfinite(score).all(axis=1)
    top = score[np.arange(len(index)), best]
    bottom = score[np.arange(len(index)), worst]
    dispersion = top - bottom
    if candidate.mode == "MOMENTUM":
        long_index = best
        short_index = worst
    else:
        long_index = worst
        short_index = best

    rows = np.arange(len(index))
    directional_flow = np.minimum(
        flow[rows, long_index], -flow[rows, short_index]
    )
    pair_volume = np.minimum(
        volume[rows, long_index], volume[rows, short_index]
    )
    entry = np.roll(open_price, -1, axis=0)
    exit_price = np.roll(open_price, -(candidate.hold + 1), axis=0)
    entry[-1:, :] = np.nan
    exit_price[-(candidate.hold + 1) :, :] = np.nan
    long_return = exit_price[rows, long_index] / entry[rows, long_index] - 1.0
    short_return = 1.0 - exit_price[rows, short_index] / entry[rows, short_index]
    gross = 0.5 * (long_return + short_return)
    flow_ok = (
        np.ones(len(index), dtype=bool)
        if candidate.flow_min <= -1.5
        else directional_flow >= candidate.flow_min
    )
    mask = (
        valid_score
        & np.isfinite(gross)
        & (best != worst)
        & (dispersion >= candidate.dispersion_min)
        & flow_ok
        & (pair_volume >= candidate.volume_min)
    )
    selected = non_overlapping(mask, candidate.hold)
    delta = pd.to_timedelta(base.TF_MINUTES[candidate.timeframe], unit="m")
    return pd.DataFrame(
        {
            "candidate_id": candidate.candidate_id,
            "timeframe": candidate.timeframe,
            "mode": candidate.mode,
            "signal_time": index[selected],
            "entry_time": index[selected],
            "exit_time": index[selected] + delta * candidate.hold,
            "long_asset": np.asarray(assets, dtype=object)[long_index[selected]],
            "short_asset": np.asarray(assets, dtype=object)[short_index[selected]],
            "hold_bars": candidate.hold,
            "lookback": candidate.lookback,
            "dispersion": dispersion[selected],
            "directional_flow": directional_flow[selected],
            "pair_volume_z": pair_volume[selected],
            "gross_return": gross[selected],
            "regime": regimes.iloc[selected].to_numpy(),
        }
    )


def max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    equity = np.cumprod(1.0 + np.clip(returns, -0.99, 10.0))
    equity = np.concatenate(([1.0], equity))
    return float(np.min(equity / np.maximum.accumulate(equity) - 1.0))


def metrics(
    trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, cost_bps: float
) -> dict[str, Any]:
    subset = trades.loc[trades["entry_time"].ge(start) & trades["entry_time"].lt(end)]
    returns = RISK_FRACTION * (
        subset["gross_return"].to_numpy(dtype=float) - cost_bps / 10000.0
    )
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
    }


def selection_score(
    train: dict[str, Any], validation: dict[str, Any], stress: dict[str, Any], timeframe: str
) -> tuple[float, bool]:
    target = 7.0 if timeframe == "1h" else 2.0
    minimum = 100 if timeframe == "1h" else 40
    stable_pf = min(train["profit_factor"], validation["profit_factor"], stress["profit_factor"])
    passed = bool(
        train["trades"] >= minimum
        and validation["trades"] >= minimum
        and train["profit_factor"] >= 1.05
        and validation["profit_factor"] >= 1.20
        and stress["profit_factor"] >= 1.05
        and validation["max_drawdown_pct"] >= -15.0
    )
    score = (
        min(stable_pf, 4.0) * 45.0
        + min(train["win_rate"], validation["win_rate"]) * 25.0
        + min(validation["sharpe"], 5.0) * 5.0
        + min(validation["trades_per_week"] / target, 1.0) * 10.0
        + validation["max_drawdown_pct"] / 5.0
    )
    return float(score), passed


def candidates(timeframe: str) -> list[Candidate]:
    return [
        Candidate(timeframe, mode, lookback, hold, dispersion, flow_min, volume_min)
        for mode in MODES
        for lookback in LOOKBACKS
        for hold in HOLD_GRID[timeframe]
        for dispersion in DISPERSION_GRID
        for flow_min in FLOW_GRID
        for volume_min in VOLUME_GRID
    ]


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
    search_rows: list[dict[str, Any]] = []
    locked_rows: list[dict[str, Any]] = []
    locked_trades: list[pd.DataFrame] = []
    for timeframe in TIMEFRAMES:
        index, frames, regimes = build_panel(timeframe)
        best: tuple[bool, float, Candidate, pd.DataFrame] | None = None
        for candidate in candidates(timeframe):
            trades = candidate_trades(index, frames, regimes, candidate)
            train = metrics(trades, TRAIN_START, TRAIN_END, BASE_COST_BPS)
            validation = metrics(trades, TRAIN_END, VALIDATION_END, BASE_COST_BPS)
            stress = metrics(trades, TRAIN_END, VALIDATION_END, STRESS_COST_BPS)
            score, passed = selection_score(train, validation, stress, timeframe)
            row = {
                **candidate.__dict__,
                "candidate_id": candidate.candidate_id,
                "selection_score": score,
                "selection_pass": passed,
                **{f"train_{key}": value for key, value in train.items()},
                **{f"validation_{key}": value for key, value in validation.items()},
                **{f"validation_stress_{key}": value for key, value in stress.items()},
            }
            search_rows.append(row)
            rank = (passed, score)
            if best is None or rank > best[:2]:
                best = (passed, score, candidate, trades)
        if best is None:
            raise RuntimeError(f"No candidate for {timeframe}")
        passed, score, candidate, trades = best
        frozen = metrics(trades, VALIDATION_END, FROZEN_END, BASE_COST_BPS)
        frozen_stress = metrics(trades, VALIDATION_END, FROZEN_END, STRESS_COST_BPS)
        locked_rows.append(
            {
                **candidate.__dict__,
                "candidate_id": candidate.candidate_id,
                "selection_score": score,
                "selection_pass": passed,
                **{f"frozen_{key}": value for key, value in frozen.items()},
                **{f"frozen_stress_{key}": value for key, value in frozen_stress.items()},
            }
        )
        locked_trades.append(trades)
        print(
            f"tf={timeframe} candidate={candidate.candidate_id} pass={passed} "
            f"frozen_trades={frozen['trades']} pf12={frozen['profit_factor']:.3f}",
            flush=True,
        )

    selected = pd.DataFrame(locked_rows)
    all_trades = pd.concat(locked_trades, ignore_index=True)
    frozen_trades = all_trades.loc[
        all_trades["entry_time"].ge(VALIDATION_END)
        & all_trades["entry_time"].lt(FROZEN_END)
    ].copy()
    summary_rows: list[dict[str, Any]] = []
    for timeframe, group in frozen_trades.groupby("timeframe", sort=True):
        for cost in (BASE_COST_BPS, STRESS_COST_BPS):
            summary_rows.append(
                {
                    "scope": timeframe,
                    "cost_bps": cost,
                    **metrics(group, VALIDATION_END, FROZEN_END, cost),
                }
            )
    for cost in (BASE_COST_BPS, STRESS_COST_BPS):
        summary_rows.append(
            {
                "scope": "combined_unconstrained",
                "cost_bps": cost,
                **metrics(frozen_trades, VALIDATION_END, FROZEN_END, cost),
            }
        )
    summary = pd.DataFrame(summary_rows)

    base_rows = summary.loc[summary["cost_bps"].eq(BASE_COST_BPS)]
    stress_rows = summary.loc[summary["cost_bps"].eq(STRESS_COST_BPS)]
    timeframe_pass = True
    for timeframe in TIMEFRAMES:
        base_row = base_rows.loc[base_rows["scope"].eq(timeframe)].iloc[0]
        stress_row = stress_rows.loc[stress_rows["scope"].eq(timeframe)].iloc[0]
        target = 7.0 if timeframe == "1h" else 2.0
        timeframe_pass &= bool(
            base_row["trades"] >= 50
            and base_row["trades_per_week"] >= target * 0.7
            and base_row["win_rate"] >= 0.55
            and base_row["profit_factor"] >= 1.50
            and stress_row["profit_factor"] >= 1.20
            and base_row["max_drawdown_pct"] >= -15.0
        )
    promotion = bool(selected["selection_pass"].all() and timeframe_pass)
    verdict = {
        "audit_id": "R33A_CROSS_SECTIONAL_RELATIVE_STRENGTH",
        "candidates_evaluated": len(search_rows),
        "locked_candidates": selected.to_dict(orient="records"),
        "market_neutral_long_short_by_construction": True,
        "promotion_gate_pass": promotion,
        "production_change_allowed": promotion,
        "decision": "PROMOTE_R33A_PAIR_SLEEVE" if promotion else "REJECT_R33A_PAIR_SLEEVE",
    }

    pd.DataFrame(search_rows).sort_values(
        ["selection_pass", "selection_score"], ascending=[False, False]
    ).to_csv(args.output_dir / "R33A_CANDIDATE_SEARCH.csv", index=False)
    selected.to_csv(args.output_dir / "R33A_LOCKED_CANDIDATES.csv", index=False)
    summary.to_csv(args.output_dir / "R33A_FROZEN_SUMMARY.csv", index=False)
    frozen_trades.to_csv(args.output_dir / "R33A_FROZEN_TRADES.csv", index=False)
    (args.output_dir / "R33A_VERDICT.json").write_text(
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
        "avg_net_bps",
    ]
    report = [
        "# R33A Cross-Sectional Relative Strength",
        "",
        "## Decision",
        "",
        f"**{verdict['decision']}**",
        "",
        "One market-neutral LONG/SHORT pair candidate per timeframe is locked with "
        "2020-2024 data. The 2025 through July 2026 period is frozen.",
        "",
        "## Frozen results",
        "",
        markdown_table(summary, columns),
        "",
    ]
    (args.output_dir / "R33A_REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
