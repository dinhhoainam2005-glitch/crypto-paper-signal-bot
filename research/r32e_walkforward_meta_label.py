"""Purged walk-forward meta-label audit for the R26A signal stream."""

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

import r32c_r26a_meta_label as meta  # noqa: E402


OUTPUT_DIR = ROOT / "r32e_output" / "reports" / "R32E_walkforward_meta_label"
FOLDS = (
    ("2024H2", "2024-07-01", "2025-01-01"),
    ("2025H1", "2025-01-01", "2025-07-01"),
    ("2025H2", "2025-07-01", "2026-01-01"),
    ("2026H1", "2026-01-01", "2026-07-01"),
    ("2026M07", "2026-07-01", "2026-08-01"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-trades", type=Path, default=meta.INPUT_TRADES)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def json_safe(value: Any) -> Any:
    return meta.json_safe(value)


def fit_candidates(
    train: pd.DataFrame,
    validation: pd.DataFrame,
) -> tuple[dict[str, Any], list[dict[str, Any]], meta.Transform, list[str]]:
    x_train, spec, feature_names = meta.fit_transform(train)
    x_validation = meta.transform(validation, spec)
    raw_return = train["gross_return"].to_numpy(dtype=float) - meta.STRESS_COST_BPS / 10000.0
    clipped_return = np.clip(raw_return, *np.quantile(raw_return, [0.01, 0.99]))
    binary = np.where(raw_return > 0.0, 1.0, -1.0)
    balance = np.ones(len(binary), dtype=float)
    for value in (-1.0, 1.0):
        mask = binary == value
        balance[mask] = len(binary) / (2.0 * mask.sum())

    rows: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    best_rank = (False, -math.inf)
    for target_name, target, weights in (
        ("RETURN", clipped_return, np.ones(len(clipped_return))),
        ("BALANCED_WIN", binary, balance),
    ):
        root_weight = np.sqrt(weights)[:, None]
        weighted_x = x_train * root_weight
        weighted_y = target * root_weight[:, 0]
        for alpha in meta.ALPHAS:
            coefficients = meta.ridge(weighted_x, weighted_y, alpha)
            scores = x_validation @ coefficients
            for quantile in meta.KEEP_QUANTILES:
                threshold = float(np.quantile(scores, quantile))
                chosen = validation.loc[scores >= threshold]
                base = meta.metrics(
                    chosen,
                    validation["entry_time"].min().floor("D"),
                    validation["entry_time"].max().ceil("D"),
                    meta.BASE_COST_BPS,
                )
                stress = meta.metrics(
                    chosen,
                    validation["entry_time"].min().floor("D"),
                    validation["entry_time"].max().ceil("D"),
                    meta.STRESS_COST_BPS,
                )
                score, passed = meta.candidate_score(base, stress)
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
                rows.append(row)
                rank = (passed, score)
                if rank > best_rank:
                    best_rank = rank
                    best = {
                        "target": target_name,
                        "alpha": alpha,
                        "keep_quantile": quantile,
                        "threshold": threshold,
                        "coefficients": coefficients,
                        "validation_pass": passed,
                    }
    if best is None:
        raise RuntimeError("No walk-forward model candidate")
    return best, rows, spec, feature_names


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame = meta.load_signals(args.input_trades)
    fold_rows: list[dict[str, Any]] = []
    search_rows: list[dict[str, Any]] = []
    test_parts: list[pd.DataFrame] = []

    for fold, start_text, end_text in FOLDS:
        test_start = pd.Timestamp(start_text, tz="UTC")
        test_end = pd.Timestamp(end_text, tz="UTC")
        validation_start = test_start - pd.Timedelta(days=365)
        train = frame.loc[frame["exit_time"].lt(validation_start)].copy()
        validation = frame.loc[
            frame["entry_time"].ge(validation_start)
            & frame["exit_time"].lt(test_start)
        ].copy()
        test = frame.loc[
            frame["entry_time"].ge(test_start)
            & frame["entry_time"].lt(test_end)
        ].copy()
        if len(train) < 500 or len(validation) < 150 or test.empty:
            raise RuntimeError(
                f"Insufficient fold data for {fold}: {len(train)}/{len(validation)}/{len(test)}"
            )
        best, rows, spec, feature_names = fit_candidates(train, validation)
        scores = meta.transform(test, spec) @ best["coefficients"]
        selected = test.loc[scores >= best["threshold"]].copy()
        selected["meta_score"] = scores[scores >= best["threshold"]]
        selected["fold"] = fold
        selected["model_target"] = best["target"]
        selected["model_alpha"] = best["alpha"]
        selected["keep_quantile"] = best["keep_quantile"]
        test_parts.append(selected)
        base = meta.metrics(selected, test_start, test_end, meta.BASE_COST_BPS)
        stress = meta.metrics(selected, test_start, test_end, meta.STRESS_COST_BPS)
        fold_rows.append(
            {
                "fold": fold,
                "test_start": test_start,
                "test_end": test_end,
                "train_signals": len(train),
                "validation_signals": len(validation),
                "test_signals_before_filter": len(test),
                "target": best["target"],
                "alpha": best["alpha"],
                "keep_quantile": best["keep_quantile"],
                "validation_gate_pass": best["validation_pass"],
                **{f"test_{key}": value for key, value in base.items()},
                **{f"test_stress_{key}": value for key, value in stress.items()},
            }
        )
        for row in rows:
            search_rows.append({"fold": fold, **row})
        print(
            f"fold={fold} train={len(train)} validation={len(validation)} "
            f"test={len(test)} selected={len(selected)} pf12={base['profit_factor']:.3f}",
            flush=True,
        )

    trades = pd.concat(test_parts, ignore_index=True).sort_values("exit_time")
    summary_rows: list[dict[str, Any]] = []
    periods = {
        "walkforward_all": (pd.Timestamp("2024-07-01", tz="UTC"), meta.FROZEN_END),
        "frozen_2025_2026m07": (meta.VALIDATION_END, meta.FROZEN_END),
        "recent_2026m01_m07": (pd.Timestamp("2026-01-01", tz="UTC"), meta.FROZEN_END),
    }
    for period, (start, end) in periods.items():
        for cost in (meta.BASE_COST_BPS, meta.STRESS_COST_BPS):
            summary_rows.append(
                {"period": period, "cost_bps": cost, **meta.metrics(trades, start, end, cost)}
            )
    summary = pd.DataFrame(summary_rows)
    frozen_base = summary.loc[
        summary["period"].eq("frozen_2025_2026m07")
        & summary["cost_bps"].eq(meta.BASE_COST_BPS)
    ].iloc[0]
    frozen_stress = summary.loc[
        summary["period"].eq("frozen_2025_2026m07")
        & summary["cost_bps"].eq(meta.STRESS_COST_BPS)
    ].iloc[0]
    folds = pd.DataFrame(fold_rows)
    positive_folds = int((folds["test_profit_factor"] > 1.0).sum())
    promotion = bool(
        folds["validation_gate_pass"].all()
        and positive_folds >= 4
        and frozen_base["trades"] >= 150
        and frozen_base["trades_per_week"] >= 2.5
        and frozen_base["win_rate"] >= 0.60
        and frozen_base["profit_factor"] >= 1.50
        and frozen_stress["profit_factor"] >= 1.20
        and frozen_base["sharpe"] >= 2.0
        and frozen_base["max_drawdown_pct"] >= -15.0
    )
    verdict = {
        "audit_id": "R32E_PURGED_WALKFORWARD_META_LABEL",
        "folds": len(FOLDS),
        "positive_pf_folds": positive_folds,
        "frozen_metrics_12bps": frozen_base.to_dict(),
        "frozen_metrics_20bps": frozen_stress.to_dict(),
        "promotion_gate_pass": promotion,
        "production_change_allowed": promotion,
        "decision": "PROMOTE_R32E_WALKFORWARD" if promotion else "REJECT_R32E_WALKFORWARD",
    }

    folds.to_csv(args.output_dir / "R32E_FOLD_RESULTS.csv", index=False)
    pd.DataFrame(search_rows).to_csv(args.output_dir / "R32E_MODEL_SEARCH.csv", index=False)
    summary.to_csv(args.output_dir / "R32E_SUMMARY.csv", index=False)
    trades.to_csv(args.output_dir / "R32E_WALKFORWARD_TRADES.csv", index=False)
    (args.output_dir / "R32E_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )

    fold_columns = [
        "fold",
        "test_signals_before_filter",
        "test_trades",
        "test_trades_per_week",
        "test_win_rate",
        "test_profit_factor",
        "test_stress_profit_factor",
        "test_sharpe",
        "test_max_drawdown_pct",
        "target",
        "alpha",
        "keep_quantile",
    ]
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
    report = [
        "# R32E Purged Walk-Forward Meta-Label",
        "",
        "## Decision",
        "",
        f"**{verdict['decision']}**",
        "",
        "Each fold trains before a one-year validation window, locks one model, and "
        "then leaves that model unchanged throughout the following test window.",
        "",
        "## Fold results",
        "",
        meta.markdown_table(folds, fold_columns),
        "",
        "## Combined results",
        "",
        meta.markdown_table(summary, summary_columns),
        "",
    ]
    (args.output_dir / "R32E_REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
