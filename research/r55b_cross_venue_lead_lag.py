"""Evaluate the fixed Kraken-to-Binance 1h lead-lag rule."""

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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import r31a_r26a_production_parity_backtest as r31a  # noqa: E402
import r34e_r26a_derivatives_overlay as r34e  # noqa: E402
import r41a_core4_symmetric_breakout_1h as r41a  # noqa: E402
import r55a_kraken_futures_audit as r55a  # noqa: E402


DEFAULT_KRAKEN = (
    ROOT / "r55a_output" / "reports" / "R55A_KRAKEN_FUTURES_AUDIT" / "R55A_KRAKEN_1H.csv"
)
DEFAULT_OUTPUT_DIR = ROOT / "r55b_output" / "reports" / "R55B_CROSS_VENUE_LEAD_LAG"
LOOKBACK = 168
Z_THRESHOLD = 2.0
HOLD_HOURS = 6
SPLITS = {
    "development": (
        pd.Timestamp("2020-02-26", tz="UTC"),
        pd.Timestamp("2024-01-01", tz="UTC"),
    ),
    "validation": (
        pd.Timestamp("2024-01-01", tz="UTC"),
        pd.Timestamp("2025-01-01", tz="UTC"),
    ),
    "locked_diagnostic": (
        pd.Timestamp("2025-01-01", tz="UTC"),
        pd.Timestamp("2026-08-01", tz="UTC"),
    ),
}
MINIMUM_TRADES = {"development": 100, "validation": 30, "locked_diagnostic": 45}
MINIMUM_PF20 = {"development": 1.05, "validation": 1.10, "locked_diagnostic": 1.10}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kraken", type=Path, default=DEFAULT_KRAKEN)
    parser.add_argument("--warehouse", type=Path, default=r31a.WAREHOUSE)
    parser.add_argument("--derivatives-root", type=Path, default=r34e.DERIVATIVES_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def load_kraken(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["bar_open"] = pd.to_datetime(frame["bar_open"], utc=True)
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def add_divergence_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["kraken_return_1h"] = output["kraken_close"].pct_change()
    output["binance_return_1h"] = output["binance_close"].pct_change()
    output["divergence"] = output["kraken_return_1h"] - output["binance_return_1h"]
    prior = output["divergence"].shift(1)
    prior_mean = prior.rolling(LOOKBACK, min_periods=LOOKBACK).mean()
    prior_std = prior.rolling(LOOKBACK, min_periods=LOOKBACK).std(ddof=0)
    output["divergence_z"] = (
        (output["divergence"] - prior_mean) / prior_std.replace(0.0, np.nan)
    )
    return output


def signal_mask(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    long_mask = frame["divergence_z"].ge(Z_THRESHOLD) & frame["kraken_return_1h"].gt(0.0)
    short_mask = frame["divergence_z"].le(-Z_THRESHOLD) & frame["kraken_return_1h"].lt(0.0)
    return long_mask.fillna(False), short_mask.fillna(False)


def build_symbol_trades(
    symbol: str, kraken: pd.DataFrame, binance: pd.DataFrame
) -> pd.DataFrame:
    kraken_symbol = (
        kraken.loc[kraken["symbol"].eq(symbol)]
        .set_index("bar_open")
        .sort_index()
        .rename(columns={"close": "kraken_close", "available_at": "kraken_available_at"})
    )
    aligned = pd.DataFrame(index=binance.index)
    aligned["binance_open"] = binance["px_open"]
    aligned["binance_close"] = binance["px_close"]
    aligned["binance_available_at"] = binance["feature_available"]
    aligned = aligned.join(
        kraken_symbol[["kraken_close", "kraken_available_at"]], how="inner"
    ).sort_index()
    if bool((aligned["kraken_available_at"] > aligned.index + pd.Timedelta(hours=1)).any()):
        raise ValueError(f"Kraken look-ahead detected for {symbol}")
    if bool((aligned["binance_available_at"] > aligned.index + pd.Timedelta(hours=1)).any()):
        raise ValueError(f"Binance look-ahead detected for {symbol}")
    aligned = add_divergence_features(aligned)
    binance_next_open = binance["px_open"].shift(-1)
    binance_exit_open = binance["px_open"].shift(-(HOLD_HOURS + 1))
    binance_exit_time = binance.index.to_series().shift(-(HOLD_HOURS + 1))
    aligned["entry_price"] = binance_next_open.reindex(aligned.index)
    aligned["exit_price"] = binance_exit_open.reindex(aligned.index)
    aligned["exit_bar_open"] = binance_exit_time.reindex(aligned.index)
    expected_exit = aligned.index + pd.Timedelta(hours=HOLD_HOURS + 1)
    aligned["contiguous"] = aligned["exit_bar_open"].eq(expected_exit)
    long_mask, short_mask = signal_mask(aligned)
    active = (long_mask | short_mask) & aligned["contiguous"]
    selected = aligned.loc[active].copy()
    if selected.empty:
        return pd.DataFrame()
    selected["side"] = np.where(long_mask.loc[selected.index], "LONG", "SHORT")
    selected["signal_time"] = selected.index + pd.Timedelta(hours=1)
    selected["entry_time"] = selected["signal_time"]
    selected["exit_time"] = selected["exit_bar_open"]
    selected = selected.reset_index(names="bar_open")
    chosen = r41a.non_overlapping_indices(selected["entry_time"], selected["exit_time"])
    selected = selected.loc[chosen].copy()
    sign = np.where(selected["side"].eq("LONG"), 1.0, -1.0)
    selected["gross_return"] = sign * (
        selected["exit_price"] / selected["entry_price"] - 1.0
    )
    selected["symbol"] = symbol
    selected["timeframe"] = "1h"
    selected["hold_bars"] = HOLD_HOURS
    return selected[
        [
            "symbol",
            "timeframe",
            "side",
            "signal_time",
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
            "gross_return",
            "kraken_return_1h",
            "binance_return_1h",
            "divergence",
            "divergence_z",
            "hold_bars",
        ]
    ]


def generate_trades(
    kraken: pd.DataFrame, warehouse: Path, derivatives_root: Path
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for symbol in r55a.CONTRACTS:
        asset = symbol.removesuffix("USDT")
        binance = r31a.load_cell(warehouse, asset, "1h")
        trades = build_symbol_trades(symbol, kraken, binance)
        if not trades.empty:
            parts.append(trades)
    if not parts:
        return pd.DataFrame()
    output = pd.concat(parts, ignore_index=True).sort_values(["exit_time", "symbol", "side"])
    output["group_order"] = output.groupby("exit_time").cumcount()
    output["net_return_12bps"] = output["gross_return"] - r31a.BASE_COST
    output["net_return_20bps"] = output["gross_return"] - r31a.STRESS_COST
    return r41a.add_funding(output.reset_index(drop=True), derivatives_root)


def evaluate(trades: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    all_split_pass = True
    for split, window in SPLITS.items():
        metrics = r41a.fast_metrics(trades, window)
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
        all_split_pass &= split_pass
        rows.append(
            {
                "scope": "portfolio",
                "split": split,
                **metrics,
                "minimum_trades": MINIMUM_TRADES[split],
                "minimum_pf20": MINIMUM_PF20[split],
                "split_pass": split_pass,
            }
        )
    locked_start, locked_end = SPLITS["locked_diagnostic"]
    locked = trades.loc[
        trades["entry_time"].ge(locked_start) & trades["entry_time"].lt(locked_end)
    ]
    cells_pass = True
    for symbol, group in locked.groupby("symbol", sort=True):
        metrics = r41a.fast_metrics(group, SPLITS["locked_diagnostic"])
        cell_pass = bool(metrics["trades"] < 15 or metrics["profit_factor_20bps"] >= 1.0)
        cells_pass &= cell_pass
        rows.append(
            {
                "scope": f"locked_symbol:{symbol}",
                "split": "locked_diagnostic",
                **metrics,
                "minimum_trades": 15,
                "minimum_pf20": 1.0,
                "split_pass": cell_pass,
            }
        )
    historical_pass = bool(all_split_pass and cells_pass)
    verdict = {
        "audit_id": "R55B_CROSS_VENUE_LEAD_LAG",
        "fixed_rule": {
            "lookback_hours": LOOKBACK,
            "z_threshold": Z_THRESHOLD,
            "hold_hours": HOLD_HOURS,
        },
        "all_split_pass": bool(all_split_pass),
        "all_populated_locked_symbols_pf20_ge_1": bool(cells_pass),
        "historical_screen_pass": historical_pass,
        "r31a_gate_changed": False,
        "production_change_allowed": False,
        "decision": "PORTFOLIO_RESEARCH_ALLOWED" if historical_pass else "REJECT_FIXED_LEAD_LAG_RULE",
    }
    return pd.DataFrame(rows), verdict


def write_report(output_dir: Path, metrics: pd.DataFrame, verdict: dict[str, Any]) -> None:
    lines = [
        "# R55B Fixed Cross-Venue Lead-Lag",
        "",
        f"- Decision: **{verdict['decision']}**.",
        "- Thresholds changed after result: **False**.",
        "- R31A gate changed: **False**.",
        "- Production change allowed: **False**.",
        "",
        "| Scope | Split | Trades | LONG | SHORT | PF12 | PF20 | Win | Pass |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in metrics.to_dict("records"):
        lines.append(
            f"| {row['scope']} | {row['split']} | {row['trades']} | "
            f"{row['long_trades']} | {row['short_trades']} | "
            f"{row['profit_factor_12bps']:.3f} | {row['profit_factor_20bps']:.3f} | "
            f"{row['win_rate_12bps']:.1%} | {row['split_pass']} |"
        )
    (output_dir / "R55B_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    kraken = load_kraken(args.kraken)
    trades = generate_trades(kraken, args.warehouse, args.derivatives_root)
    metrics, verdict = evaluate(trades)
    trades.to_csv(args.output_dir / "R55B_TRADES.csv", index=False)
    metrics.to_csv(args.output_dir / "R55B_METRICS.csv", index=False)
    (args.output_dir / "R55B_VERDICT.json").write_text(
        json.dumps(r31a.json_safe(verdict), indent=2, sort_keys=True), encoding="utf-8"
    )
    write_report(args.output_dir, metrics, verdict)
    print(json.dumps(r31a.json_safe(verdict), indent=2, sort_keys=True))
    print(metrics.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
