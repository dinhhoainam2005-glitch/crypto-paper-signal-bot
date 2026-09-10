"""Causal meta-label audit for exact R26A production-parity signals.

The R26A entries are unchanged. A regularized meta-model estimates whether each
already-triggered setup is worth sending. Model fitting uses 2020-2023,
threshold selection uses 2024, and 2025 through July 2026 is opened once.
"""

from __future__ import annotations

import argparse
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

import r29c_regime_ensemble_search as features  # noqa: E402
import r31a_r26a_production_parity_backtest as r31a  # noqa: E402


INPUT_TRADES = (
    ROOT
    / "r31a_output"
    / "reports"
    / "R31A_R26A_production_parity"
    / "R31A_ACCEPTED_TRADES.csv"
)
OUTPUT_DIR = ROOT / "r32c_output" / "reports" / "R32C_R26A_meta_label"
TRAIN_END = pd.Timestamp("2024-01-01", tz="UTC")
VALIDATION_END = pd.Timestamp("2025-01-01", tz="UTC")
FROZEN_END = pd.Timestamp("2026-08-01", tz="UTC")
BASE_COST_BPS = 12.0
STRESS_COST_BPS = 20.0
RISK_FRACTION = 0.25
ALPHAS = (0.01, 0.1, 1.0, 10.0, 100.0)
KEEP_QUANTILES = (0.0, 0.20, 0.35, 0.50, 0.60, 0.70, 0.80)

NUMERIC_FEATURES = (
    "ret_1",
    "ret_4",
    "ret_6",
    "ret_12",
    "ret_24",
    "ret_48",
    "ret_72",
    "rv_24",
    "rv_72",
    "close_z_20",
    "atr_pct_14",
    "quote_volume_prior_z_20",
    "flow",
    "large_flow",
    "premium_close_prior_z_24",
    "drv_open_interest_log_change_1",
    "drv_open_interest_pct_change_24h",
    "drv_basis_close",
    "drv_basis_change_1",
    "drv_funding_last_rate",
    "drv_funding_sum_trailing_24h",
    "drv_funding_sum_trailing_72h",
    "breadth_up_24",
    "breadth_down_24",
    "breadth_total",
    "market_mean_24",
    "full_derivatives_state_available",
    "micro_source_available",
    "market_flow_source_available",
    "selection_score",
    "hold_bars",
)
CATEGORICAL_FEATURES = ("asset", "timeframe", "side", "family", "regime")


@dataclass
class Transform:
    median: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    mean: np.ndarray
    scale: np.ndarray
    categories: dict[str, list[str]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-trades", type=Path, default=INPUT_TRADES)
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


def load_signals(path: Path) -> pd.DataFrame:
    trades = pd.read_csv(path)
    for column in ("feature_available", "signal_time", "entry_time", "exit_time"):
        trades[column] = pd.to_datetime(trades[column], utc=True, errors="coerce")
    trades = trades.loc[trades["accepted"].astype(str).str.lower().eq("true")].copy()

    regimes = features.load_btc_regimes()
    enriched: list[pd.DataFrame] = []
    for (asset, timeframe), cell_trades in trades.groupby(["asset", "timeframe"], sort=True):
        breadth = features.load_breadth(str(timeframe))
        panel = features.load_cell(str(asset), str(timeframe), regimes).merge(
            breadth, on="bar_open", how="left", validate="one_to_one"
        )
        panel = panel.rename(columns={"signal_time": "feature_available"})
        columns = ["feature_available", *[name for name in NUMERIC_FEATURES if name in panel.columns]]
        merged = cell_trades.drop(
            columns=[name for name in ("regime",) if name in cell_trades.columns]
        ).merge(
            panel[columns + ["regime"]],
            on="feature_available",
            how="left",
            validate="many_to_one",
            suffixes=("", "_panel"),
        )
        enriched.append(merged)
    frame = pd.concat(enriched, ignore_index=True)
    frame["side_sign"] = np.where(frame["side"].eq("LONG"), 1.0, -1.0)
    for column in (
        "ret_1",
        "ret_4",
        "ret_6",
        "ret_12",
        "ret_24",
        "ret_48",
        "ret_72",
        "close_z_20",
        "flow",
        "large_flow",
        "premium_close_prior_z_24",
        "drv_open_interest_log_change_1",
        "drv_open_interest_pct_change_24h",
        "drv_basis_close",
        "drv_basis_change_1",
        "drv_funding_last_rate",
        "drv_funding_sum_trailing_24h",
        "drv_funding_sum_trailing_72h",
        "market_mean_24",
    ):
        frame[f"directional_{column}"] = frame[column] * frame["side_sign"]
    frame["directional_breadth"] = np.where(
        frame["side"].eq("LONG"), frame["breadth_up_24"], frame["breadth_down_24"]
    )
    return frame.sort_values(["entry_time", "group_order"]).reset_index(drop=True)


def fit_transform(train: pd.DataFrame) -> tuple[np.ndarray, Transform, list[str]]:
    numeric_columns = list(NUMERIC_FEATURES) + [
        column for column in train.columns if column.startswith("directional_")
    ]
    numeric = train[numeric_columns].apply(pd.to_numeric, errors="coerce")
    median = numeric.median().fillna(0.0).to_numpy(dtype=float)
    values = numeric.to_numpy(dtype=float)
    values = np.where(np.isfinite(values), values, median)
    lower = np.quantile(values, 0.005, axis=0)
    upper = np.quantile(values, 0.995, axis=0)
    values = np.clip(values, lower, upper)
    mean = values.mean(axis=0)
    scale = values.std(axis=0)
    scale = np.where(scale > 1e-10, scale, 1.0)
    matrix = [(values - mean) / scale]
    names = list(numeric_columns)
    categories: dict[str, list[str]] = {}
    for column in CATEGORICAL_FEATURES:
        levels = sorted(train[column].fillna("UNKNOWN").astype(str).unique().tolist())
        categories[column] = levels
        raw = train[column].fillna("UNKNOWN").astype(str)
        for level in levels:
            matrix.append(raw.eq(level).to_numpy(dtype=float)[:, None])
            names.append(f"{column}={level}")
    matrix.append(np.ones((len(train), 1), dtype=float))
    names.append("intercept")
    return np.concatenate(matrix, axis=1), Transform(
        median=median,
        lower=lower,
        upper=upper,
        mean=mean,
        scale=scale,
        categories=categories,
    ), names


def transform(frame: pd.DataFrame, spec: Transform) -> np.ndarray:
    numeric_columns = list(NUMERIC_FEATURES) + [
        column for column in frame.columns if column.startswith("directional_")
    ]
    values = frame[numeric_columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    values = np.where(np.isfinite(values), values, spec.median)
    values = np.clip(values, spec.lower, spec.upper)
    matrix = [(values - spec.mean) / spec.scale]
    for column in CATEGORICAL_FEATURES:
        raw = frame[column].fillna("UNKNOWN").astype(str)
        for level in spec.categories[column]:
            matrix.append(raw.eq(level).to_numpy(dtype=float)[:, None])
    matrix.append(np.ones((len(frame), 1), dtype=float))
    return np.concatenate(matrix, axis=1)


def ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    penalty = np.eye(x.shape[1], dtype=float) * alpha
    penalty[-1, -1] = 0.0
    return np.linalg.solve(x.T @ x / len(x) + penalty, x.T @ y / len(x))


def max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    equity = np.cumprod(1.0 + np.clip(returns, -0.99, 10.0))
    equity = np.concatenate(([1.0], equity))
    return float(np.min(equity / np.maximum.accumulate(equity) - 1.0))


def metrics(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, cost_bps: float) -> dict[str, Any]:
    subset = frame.loc[frame["entry_time"].ge(start) & frame["entry_time"].lt(end)]
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
        "long_trades": int(subset["side"].eq("LONG").sum()),
        "short_trades": int(subset["side"].eq("SHORT").sum()),
        "trades_1h": int(subset["timeframe"].eq("1h").sum()),
        "trades_4h": int(subset["timeframe"].eq("4h").sum()),
    }


def candidate_score(base: dict[str, Any], stress: dict[str, Any]) -> tuple[float, bool]:
    passed = bool(
        base["trades"] >= 150
        and base["trades_per_week"] >= 2.5
        and base["profit_factor"] >= 1.35
        and stress["profit_factor"] >= 1.15
        and base["max_drawdown_pct"] >= -15.0
    )
    score = (
        min(base["profit_factor"], stress["profit_factor"], 4.0) * 45.0
        + base["win_rate"] * 30.0
        + min(base["sharpe"], 5.0) * 5.0
        + min(base["trades_per_week"] / 6.0, 1.0) * 8.0
        + base["max_drawdown_pct"] / 5.0
    )
    return float(score), passed


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
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
    frame = load_signals(args.input_trades)
    train = frame.loc[frame["entry_time"].lt(TRAIN_END)].copy()
    validation = frame.loc[
        frame["entry_time"].ge(TRAIN_END) & frame["entry_time"].lt(VALIDATION_END)
    ].copy()
    frozen = frame.loc[
        frame["entry_time"].ge(VALIDATION_END) & frame["entry_time"].lt(FROZEN_END)
    ].copy()
    x_train, spec, feature_names = fit_transform(train)
    x_validation = transform(validation, spec)
    x_frozen = transform(frozen, spec)
    target_return = train["gross_return"].to_numpy(dtype=float) - STRESS_COST_BPS / 10000.0
    clipped_return = np.clip(target_return, *np.quantile(target_return, [0.01, 0.99]))
    target_binary = np.where(target_return > 0.0, 1.0, -1.0)
    balance = np.ones(len(target_binary), dtype=float)
    for value in (-1.0, 1.0):
        mask = target_binary == value
        balance[mask] = len(target_binary) / (2.0 * mask.sum())

    search_rows: list[dict[str, Any]] = []
    locked: dict[str, Any] | None = None
    locked_rank = (False, -math.inf)
    for target_name, y, weights in (
        ("RETURN", clipped_return, np.ones(len(clipped_return))),
        ("BALANCED_WIN", target_binary, balance),
    ):
        root_weights = np.sqrt(weights)[:, None]
        weighted_x = x_train * root_weights
        weighted_y = y * root_weights[:, 0]
        for alpha in ALPHAS:
            coefficients = ridge(weighted_x, weighted_y, alpha)
            validation_score = x_validation @ coefficients
            frozen_score = x_frozen @ coefficients
            for quantile in KEEP_QUANTILES:
                threshold = float(np.quantile(validation_score, quantile))
                selected_validation = validation.loc[validation_score >= threshold].copy()
                base = metrics(selected_validation, TRAIN_END, VALIDATION_END, BASE_COST_BPS)
                stress = metrics(selected_validation, TRAIN_END, VALIDATION_END, STRESS_COST_BPS)
                score, passed = candidate_score(base, stress)
                row = {
                    "target": target_name,
                    "alpha": alpha,
                    "keep_quantile": quantile,
                    "threshold": threshold,
                    "selection_score": score,
                    "selection_pass": passed,
                    **{f"validation_{key}": value for key, value in base.items()},
                    **{f"validation_stress_{key}": value for key, value in stress.items()},
                }
                search_rows.append(row)
                rank = (passed, score)
                if rank > locked_rank:
                    locked_rank = rank
                    locked = {
                        "target": target_name,
                        "alpha": alpha,
                        "quantile": quantile,
                        "threshold": threshold,
                        "coefficients": coefficients,
                        "frozen_score": frozen_score,
                    }
    if locked is None:
        raise RuntimeError("No meta-label candidate was evaluated")

    selected_frozen = frozen.loc[
        locked["frozen_score"] >= locked["threshold"]
    ].copy()
    selected_frozen["meta_score"] = locked["frozen_score"][
        locked["frozen_score"] >= locked["threshold"]
    ]
    baseline_frozen = frozen.copy()
    periods = [
        ("R31A_BASELINE", baseline_frozen),
        ("R32C_META_LABEL", selected_frozen),
    ]
    comparison_rows: list[dict[str, Any]] = []
    for model, data in periods:
        for cost in (BASE_COST_BPS, STRESS_COST_BPS):
            comparison_rows.append(
                {
                    "model": model,
                    "cost_bps": cost,
                    **metrics(data, VALIDATION_END, FROZEN_END, cost),
                }
            )
    comparison = pd.DataFrame(comparison_rows)

    route_rows: list[dict[str, Any]] = []
    for keys, group in selected_frozen.groupby(["asset", "timeframe", "side"], sort=True):
        route_rows.append(
            {
                "asset": keys[0],
                "timeframe": keys[1],
                "side": keys[2],
                **metrics(group, VALIDATION_END, FROZEN_END, BASE_COST_BPS),
            }
        )
    routes = pd.DataFrame(route_rows)

    frozen_base = comparison.loc[
        comparison["model"].eq("R32C_META_LABEL")
        & comparison["cost_bps"].eq(BASE_COST_BPS)
    ].iloc[0]
    frozen_stress = comparison.loc[
        comparison["model"].eq("R32C_META_LABEL")
        & comparison["cost_bps"].eq(STRESS_COST_BPS)
    ].iloc[0]
    promotion = bool(
        locked_rank[0]
        and frozen_base["trades"] >= 150
        and frozen_base["trades_per_week"] >= 2.5
        and frozen_base["win_rate"] >= 0.60
        and frozen_base["profit_factor"] >= 1.50
        and frozen_stress["profit_factor"] >= 1.20
        and frozen_base["sharpe"] >= 2.0
        and frozen_base["max_drawdown_pct"] >= -15.0
    )
    coefficient_table = pd.DataFrame(
        {"feature": feature_names, "coefficient": locked["coefficients"]}
    ).sort_values("coefficient", key=lambda values: values.abs(), ascending=False)
    verdict = {
        "audit_id": "R32C_R26A_CAUSAL_META_LABEL",
        "train_signals": len(train),
        "validation_signals": len(validation),
        "frozen_signals_before_filter": len(frozen),
        "models_evaluated": len(search_rows),
        "locked_model": {
            "target": locked["target"],
            "alpha": locked["alpha"],
            "keep_quantile": locked["quantile"],
            "threshold": locked["threshold"],
        },
        "validation_gate_pass": bool(locked_rank[0]),
        "frozen_metrics_12bps": frozen_base.to_dict(),
        "frozen_metrics_20bps": frozen_stress.to_dict(),
        "promotion_gate_pass": promotion,
        "production_change_allowed": promotion,
        "decision": "PROMOTE_R32C_META_LABEL" if promotion else "REJECT_R32C_META_LABEL",
    }

    pd.DataFrame(search_rows).sort_values(
        ["selection_pass", "selection_score"], ascending=[False, False]
    ).to_csv(args.output_dir / "R32C_MODEL_SEARCH.csv", index=False)
    comparison.to_csv(args.output_dir / "R32C_COMPARISON.csv", index=False)
    routes.to_csv(args.output_dir / "R32C_FROZEN_ROUTES.csv", index=False)
    selected_frozen.to_csv(args.output_dir / "R32C_FROZEN_TRADES.csv", index=False)
    coefficient_table.to_csv(args.output_dir / "R32C_COEFFICIENTS.csv", index=False)
    (args.output_dir / "R32C_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )

    metric_columns = [
        "model",
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
        "# R32C R26A Meta-Label Audit",
        "",
        "## Decision",
        "",
        f"**{verdict['decision']}**",
        "",
        "R26A entries are unchanged. Model coefficients use signals before 2024; "
        "the keep threshold uses 2024 only; 2025 through July 2026 is frozen.",
        "",
        "## Frozen comparison",
        "",
        markdown_table(comparison, metric_columns),
        "",
        "## Frozen routes",
        "",
        markdown_table(routes, route_columns),
        "",
    ]
    (args.output_dir / "R32C_REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
