"""Test the pre-registered DVOL interaction on fixed R41 1h sleeves."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / ".codex_deps", ROOT, ROOT / "research"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pandas as pd  # noqa: E402

import r31a_r26a_production_parity_backtest as r31a  # noqa: E402
import r41a_core4_symmetric_breakout_1h as r41a  # noqa: E402


DEFAULT_DVOL = (
    ROOT / "r54a_output" / "reports" / "R54A_DERIBIT_DVOL_AUDIT" / "R54A_DVOL_DAILY.csv"
)
DEFAULT_BREAKOUT = (
    ROOT
    / "r41a_output"
    / "reports"
    / "R41A_core4_symmetric_breakout_1h"
    / "R41A_SELECTED_TRADES.csv"
)
DEFAULT_MEAN_REVERSION = (
    ROOT
    / "r41b_output"
    / "reports"
    / "R41B_core4_symmetric_mean_reversion_1h"
    / "R41B_SELECTED_TRADES.csv"
)
DEFAULT_OUTPUT_DIR = ROOT / "r54b_output" / "reports" / "R54B_DVOL_REGIME_INTERACTION"

SPLITS = {
    "development": (pd.Timestamp("2021-04-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "validation": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "locked_diagnostic": (
        pd.Timestamp("2025-01-01", tz="UTC"),
        pd.Timestamp("2026-08-01", tz="UTC"),
    ),
}
MINIMUM_TRADES = {"development": 100, "validation": 30, "locked_diagnostic": 45}
MINIMUM_PF20 = {"development": 1.05, "validation": 1.10, "locked_diagnostic": 1.10}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dvol", type=Path, default=DEFAULT_DVOL)
    parser.add_argument("--breakout", type=Path, default=DEFAULT_BREAKOUT)
    parser.add_argument("--mean-reversion", type=Path, default=DEFAULT_MEAN_REVERSION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def load_dvol(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["bar_open"] = pd.to_datetime(frame["bar_open"], utc=True)
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    parts: list[pd.DataFrame] = []
    for currency, group in frame.groupby("currency", sort=True):
        group = group.sort_values("available_at").copy()
        group["dvol_median_60d"] = group["close"].rolling(60, min_periods=60).median()
        group["dvol_change_5d"] = group["close"] / group["close"].shift(5) - 1.0
        group["dvol_currency"] = currency
        parts.append(group)
    return pd.concat(parts, ignore_index=True).sort_values(["currency", "available_at"])


def load_trades(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in ("signal_time", "entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


def proxy_currency(symbol: str) -> str:
    return "BTC" if symbol == "BTCUSDT" else "ETH"


def attach_causal_dvol(trades: pd.DataFrame, dvol: pd.DataFrame) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for currency in ("BTC", "ETH"):
        symbols = [symbol for symbol in trades["symbol"].unique() if proxy_currency(symbol) == currency]
        left = trades.loc[trades["symbol"].isin(symbols)].sort_values("signal_time").copy()
        if left.empty:
            continue
        right = dvol.loc[dvol["currency"].eq(currency)].sort_values("available_at").copy()
        right = right[
            ["available_at", "bar_open", "close", "dvol_median_60d", "dvol_change_5d", "dvol_currency"]
        ].rename(columns={"close": "dvol_close", "bar_open": "dvol_bar_open"})
        joined = pd.merge_asof(
            left,
            right,
            left_on="signal_time",
            right_on="available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        parts.append(joined)
    if not parts:
        return trades.iloc[0:0].copy()
    output = pd.concat(parts, ignore_index=True).sort_values(["entry_time", "symbol", "side"])
    available = output["available_at"].notna()
    if bool((output.loc[available, "available_at"] > output.loc[available, "signal_time"]).any()):
        raise ValueError("DVOL look-ahead detected")
    return output.reset_index(drop=True)


def apply_fixed_mask(frame: pd.DataFrame, sleeve: str) -> pd.DataFrame:
    ready = frame[["dvol_close", "dvol_median_60d", "dvol_change_5d"]].notna().all(axis=1)
    if sleeve == "BREAKOUT_CONTINUATION":
        mask = ready & frame["dvol_close"].gt(frame["dvol_median_60d"]) & frame[
            "dvol_change_5d"
        ].gt(0.0)
    elif sleeve == "MEAN_REVERSION":
        mask = ready & frame["dvol_close"].lt(frame["dvol_median_60d"]) & frame[
            "dvol_change_5d"
        ].lt(0.0)
    else:
        raise ValueError(f"Unknown sleeve: {sleeve}")
    output = frame.loc[mask].copy()
    output["r54_sleeve"] = sleeve
    return output


def evaluate_sleeve(frame: pd.DataFrame, sleeve: str) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    passed = True
    for split, window in SPLITS.items():
        metrics = r41a.fast_metrics(frame, window)
        split_frame = frame.loc[
            frame["entry_time"].ge(window[0]) & frame["entry_time"].lt(window[1])
        ]
        direction_ok = True
        if split == "locked_diagnostic":
            direction_ok = bool(
                metrics["long_trades"] >= 15 and metrics["short_trades"] >= 15
            )
        split_pass = bool(
            metrics["trades"] >= MINIMUM_TRADES[split]
            and metrics["profit_factor_20bps"] >= MINIMUM_PF20[split]
            and direction_ok
        )
        passed &= split_pass
        rows.append(
            {
                "sleeve": sleeve,
                "split": split,
                **metrics,
                "minimum_trades": MINIMUM_TRADES[split],
                "minimum_pf20": MINIMUM_PF20[split],
                "direction_balance_required": split == "locked_diagnostic",
                "split_pass": split_pass,
                "symbols": int(split_frame["symbol"].nunique()),
            }
        )
    return rows, bool(passed)


def write_report(output_dir: Path, metrics: pd.DataFrame, verdict: dict[str, Any]) -> None:
    lines = [
        "# R54B Fixed DVOL Regime Interaction",
        "",
        f"- Decision: **{verdict['decision']}**.",
        f"- Passing sleeves: `{verdict['passing_sleeves']}/2`.",
        "- Thresholds changed after result: **False**.",
        "- R31A gate changed: **False**.",
        "- Production change allowed: **False**.",
        "",
        "| Sleeve | Split | Trades | LONG | SHORT | PF12 | PF20 | Win | Pass |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in metrics.to_dict("records"):
        lines.append(
            f"| {row['sleeve']} | {row['split']} | {row['trades']} | "
            f"{row['long_trades']} | {row['short_trades']} | "
            f"{row['profit_factor_12bps']:.3f} | {row['profit_factor_20bps']:.3f} | "
            f"{row['win_rate_12bps']:.1%} | {row['split_pass']} |"
        )
    (output_dir / "R54B_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dvol = load_dvol(args.dvol)
    source = {
        "BREAKOUT_CONTINUATION": load_trades(args.breakout),
        "MEAN_REVERSION": load_trades(args.mean_reversion),
    }
    accepted_parts: list[pd.DataFrame] = []
    metric_rows: list[dict[str, Any]] = []
    sleeve_passes: dict[str, bool] = {}
    for sleeve, trades in source.items():
        decorated = attach_causal_dvol(trades, dvol)
        accepted = apply_fixed_mask(decorated, sleeve)
        accepted_parts.append(accepted)
        rows, passed = evaluate_sleeve(accepted, sleeve)
        metric_rows.extend(rows)
        sleeve_passes[sleeve] = passed
    accepted_all = pd.concat(accepted_parts, ignore_index=True)
    metrics = pd.DataFrame(metric_rows)
    passing = int(sum(sleeve_passes.values()))
    historical_pass = passing == len(sleeve_passes)
    verdict = {
        "audit_id": "R54B_DVOL_REGIME_INTERACTION",
        "fixed_rule": {
            "median_days": 60,
            "change_days": 5,
            "breakout": "DVOL_ABOVE_MEDIAN_AND_RISING",
            "mean_reversion": "DVOL_BELOW_MEDIAN_AND_FALLING",
        },
        "sleeve_passes": sleeve_passes,
        "passing_sleeves": passing,
        "historical_screen_pass": historical_pass,
        "r31a_gate_changed": False,
        "production_change_allowed": False,
        "decision": "PORTFOLIO_RESEARCH_ALLOWED" if historical_pass else "REJECT_FIXED_DVOL_RULES",
    }
    accepted_all.to_csv(args.output_dir / "R54B_ACCEPTED_TRADES.csv", index=False)
    metrics.to_csv(args.output_dir / "R54B_METRICS.csv", index=False)
    (args.output_dir / "R54B_VERDICT.json").write_text(
        json.dumps(r31a.json_safe(verdict), indent=2, sort_keys=True), encoding="utf-8"
    )
    write_report(args.output_dir, metrics, verdict)
    print(json.dumps(r31a.json_safe(verdict), indent=2, sort_keys=True))
    print(metrics.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
