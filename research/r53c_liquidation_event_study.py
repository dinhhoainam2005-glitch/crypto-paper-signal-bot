"""Run a fixed, sparse-panel CORE4 liquidation event study.

Rules and thresholds are intentionally fixed before evaluation. Inputs are the
free first-day-of-month Tardis liquidation samples plus the existing causal 1h
CORE4 warehouse. Sparse samples can reject weak hypotheses but cannot authorize
promotion or replace a continuous-history replay.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS = ROOT / ".codex_deps"
if LOCAL_DEPS.exists() and str(LOCAL_DEPS) not in sys.path:
    sys.path.insert(0, str(LOCAL_DEPS))

import numpy as np
import pandas as pd

from r53a_tardis_microstructure_probe import CORE_SYMBOLS, aggregate_liquidations
from r53b_tardis_liquidation_samples import DEFAULT_DATA_DIR


WAREHOUSE = Path(r"D:\@Nam\btc_eth_signal_research\research_base\R14E_multi_asset_mtf\1h")
DEFAULT_OUTPUT_DIR = ROOT / "r53c_output" / "reports" / "R53C_LIQUIDATION_EVENT_STUDY"
BASE_COST = 0.0012
STRESS_COST = 0.0020
HOLD_HOURS = 12
MIN_LIQUIDATION_SHARE = 0.002
MIN_DIRECTION_SHARE = 0.75
MIN_ABS_RETURN = 0.005
MIN_VOLUME_Z = 0.0
MIN_FLOW = 0.05
CONTINUATION_CLOSE_POSITION = 0.40
REVERSAL_CLOSE_POSITION = 0.65

SPLITS = {
    "development": (pd.Timestamp("2020-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "validation": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "locked_diagnostic": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-08-01", tz="UTC")),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def parquet_path(root: Path, symbol: str) -> Path:
    asset = symbol.removesuffix("USDT")
    matches = list(root.glob(f"{asset}_common_research_base_1h_*.parquet"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one 1h warehouse file for {symbol}, got {matches}")
    return matches[0]


def load_market(root: Path, symbol: str) -> pd.DataFrame:
    columns = [
        "bar_open_utc",
        "feature_available_utc",
        "px_open",
        "px_high",
        "px_low",
        "px_close",
        "px_quote_volume",
        "mkt_taker_buy_quote_asset_volume",
    ]
    frame = pd.read_parquet(parquet_path(root, symbol), columns=columns)
    frame["bar_open"] = pd.to_datetime(frame.pop("bar_open_utc"), utc=True, errors="coerce")
    frame["feature_available"] = pd.to_datetime(
        frame.pop("feature_available_utc"), utc=True, errors="coerce"
    )
    for column in columns[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["bar_open", "px_open", "px_high", "px_low", "px_close", "px_quote_volume"])
        .drop_duplicates("bar_open", keep="last")
        .sort_values("bar_open")
        .set_index("bar_open")
    )
    expected_available = frame.index.to_series(index=frame.index) + pd.Timedelta(hours=1)
    if bool(frame["feature_available"].ne(expected_available).any()):
        raise ValueError(f"Warehouse availability mismatch for {symbol}")
    prior_mean = frame["px_quote_volume"].shift(1).rolling(20, min_periods=20).mean()
    prior_std = frame["px_quote_volume"].shift(1).rolling(20, min_periods=20).std(ddof=0)
    frame["volume_z20"] = (
        (frame["px_quote_volume"] - prior_mean) / prior_std.replace(0.0, np.nan)
    )
    frame["return_1h"] = frame["px_close"] / frame["px_open"] - 1.0
    frame["taker_imbalance"] = (
        frame["mkt_taker_buy_quote_asset_volume"] / frame["px_quote_volume"] * 2.0 - 1.0
    )
    candle_range = (frame["px_high"] - frame["px_low"]).replace(0.0, np.nan)
    frame["close_position"] = (frame["px_close"] - frame["px_low"]) / candle_range
    frame["entry_price"] = frame["px_open"].shift(-1)
    frame["exit_price"] = frame["px_open"].shift(-(1 + HOLD_HOURS))
    return frame


def load_liquidations(root: Path, symbol: str) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for path in sorted((root / symbol).glob("*.csv.gz")):
        frame = aggregate_liquidations(path)
        if not frame.empty:
            parts.append(frame)
    if not parts:
        return pd.DataFrame()
    result = pd.concat(parts).sort_index()
    if bool(result.index.duplicated().any()):
        result = result.groupby(level=0, sort=True).sum(min_count=1)
        denominator = result["liquidation_notional"].replace(0.0, np.nan)
        result["liquidation_pressure"] = (
            (result["short_liquidated_notional"] - result["long_liquidated_notional"])
            / denominator
        )
    return result


def candidate_events(market: pd.DataFrame, liquidations: pd.DataFrame, symbol: str) -> pd.DataFrame:
    joined = market.join(liquidations, how="inner")
    joined = joined.dropna(subset=["entry_price", "exit_price", "liquidation_notional"])
    joined["liquidation_share"] = joined["liquidation_notional"] / joined["px_quote_volume"]
    joined["long_liquidation_share"] = (
        joined["long_liquidated_notional"] / joined["liquidation_notional"]
    )
    joined["short_liquidation_share"] = (
        joined["short_liquidated_notional"] / joined["liquidation_notional"]
    )

    common = joined["liquidation_share"].ge(MIN_LIQUIDATION_SHARE) & joined["volume_z20"].ge(
        MIN_VOLUME_Z
    )
    definitions = {
        "SHORT_DELEVERAGING_CONTINUATION": (
            "SHORT",
            common
            & joined["long_liquidation_share"].ge(MIN_DIRECTION_SHARE)
            & joined["return_1h"].le(-MIN_ABS_RETURN)
            & joined["taker_imbalance"].le(-MIN_FLOW)
            & joined["close_position"].le(CONTINUATION_CLOSE_POSITION),
        ),
        "LONG_SQUEEZE_CONTINUATION": (
            "LONG",
            common
            & joined["short_liquidation_share"].ge(MIN_DIRECTION_SHARE)
            & joined["return_1h"].ge(MIN_ABS_RETURN)
            & joined["taker_imbalance"].ge(MIN_FLOW)
            & joined["close_position"].ge(1.0 - CONTINUATION_CLOSE_POSITION),
        ),
        "LONG_LIQUIDATION_REVERSAL": (
            "LONG",
            common
            & joined["long_liquidation_share"].ge(MIN_DIRECTION_SHARE)
            & joined["return_1h"].le(-MIN_ABS_RETURN)
            & joined["close_position"].ge(REVERSAL_CLOSE_POSITION),
        ),
        "SHORT_SQUEEZE_REVERSAL": (
            "SHORT",
            common
            & joined["short_liquidation_share"].ge(MIN_DIRECTION_SHARE)
            & joined["return_1h"].ge(MIN_ABS_RETURN)
            & joined["close_position"].le(1.0 - REVERSAL_CLOSE_POSITION),
        ),
    }
    rows: list[pd.DataFrame] = []
    for hypothesis, (side, mask) in definitions.items():
        selected = joined.loc[mask.fillna(False)].copy()
        if selected.empty:
            continue
        sign = 1.0 if side == "LONG" else -1.0
        selected["symbol"] = symbol
        selected["hypothesis"] = hypothesis
        selected["side"] = side
        selected["signal_time"] = selected.index + pd.Timedelta(hours=1)
        selected["entry_time"] = selected["signal_time"]
        selected["exit_time"] = selected["entry_time"] + pd.Timedelta(hours=HOLD_HOURS)
        selected["gross_return"] = (selected["exit_price"] / selected["entry_price"] - 1.0) * sign
        selected["net_return_12bps"] = selected["gross_return"] - BASE_COST
        selected["net_return_20bps"] = selected["gross_return"] - STRESS_COST
        rows.append(selected.reset_index(names="bar_open"))
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True).sort_values(["entry_time", "symbol", "hypothesis"])


def remove_overlaps(frame: pd.DataFrame) -> pd.DataFrame:
    kept: list[int] = []
    for _, group in frame.groupby(["symbol", "hypothesis"], sort=False):
        last_exit: pd.Timestamp | None = None
        for row in group.sort_values("entry_time").itertuples():
            if last_exit is None or row.entry_time >= last_exit:
                kept.append(row.Index)
                last_exit = row.exit_time
    return frame.loc[sorted(kept)].sort_values("entry_time").reset_index(drop=True)


def profit_factor(values: pd.Series) -> float:
    gains = float(values.loc[values > 0.0].sum())
    losses = float(-values.loc[values < 0.0].sum())
    if losses == 0.0:
        return math.inf if gains > 0.0 else 0.0
    return gains / losses


def metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "win_rate_12bps": 0.0,
            "profit_factor_12bps": 0.0,
            "profit_factor_20bps": 0.0,
            "mean_net_bps_12bps": 0.0,
            "event_sharpe_12bps": 0.0,
        }
    returns = frame["net_return_12bps"].astype(float)
    std = float(returns.std(ddof=0))
    return {
        "trades": int(len(frame)),
        "win_rate_12bps": float((returns > 0.0).mean()),
        "profit_factor_12bps": float(profit_factor(returns)),
        "profit_factor_20bps": float(profit_factor(frame["net_return_20bps"].astype(float))),
        "mean_net_bps_12bps": float(returns.mean() * 10_000.0),
        "event_sharpe_12bps": float(returns.mean() / std * math.sqrt(len(returns))) if std > 0.0 else 0.0,
    }


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for hypothesis, hypothesis_frame in events.groupby("hypothesis", sort=True):
        for split, (start, end) in SPLITS.items():
            subset = hypothesis_frame.loc[
                hypothesis_frame["entry_time"].ge(start) & hypothesis_frame["entry_time"].lt(end)
            ]
            rows.append({"hypothesis": hypothesis, "split": split, **metrics(subset)})
    return pd.DataFrame(rows)


def gate(summary: pd.DataFrame) -> dict[str, Any]:
    decisions: dict[str, Any] = {}
    for hypothesis in sorted(summary["hypothesis"].unique()):
        indexed = summary.loc[summary["hypothesis"].eq(hypothesis)].set_index("split")
        checks = {
            "development_trades_ge_30": int(indexed.loc["development", "trades"]) >= 30,
            "development_pf20_ge_1p10": float(indexed.loc["development", "profit_factor_20bps"]) >= 1.10,
            "validation_trades_ge_10": int(indexed.loc["validation", "trades"]) >= 10,
            "validation_pf20_ge_1p10": float(indexed.loc["validation", "profit_factor_20bps"]) >= 1.10,
            "locked_trades_ge_15": int(indexed.loc["locked_diagnostic", "trades"]) >= 15,
            "locked_pf20_ge_1p10": float(indexed.loc["locked_diagnostic", "profit_factor_20bps"]) >= 1.10,
        }
        decisions[hypothesis] = {
            "checks": checks,
            "passed_checks": int(sum(checks.values())),
            "total_checks": len(checks),
            "pass": bool(all(checks.values())),
        }
    passing = [name for name, item in decisions.items() if item["pass"]]
    return {
        "audit_id": "R53C_LIQUIDATION_EVENT_STUDY",
        "sample_design": "FIRST_DAY_OF_EACH_MONTH_SPARSE_PANEL",
        "fixed_horizon_hours": HOLD_HOURS,
        "base_cost_bps": int(round(BASE_COST * 10_000)),
        "stress_cost_bps": int(round(STRESS_COST * 10_000)),
        "hypotheses": decisions,
        "passing_hypotheses": passing,
        "decision": "ADVANCE_TO_CONTINUOUS_HISTORY" if passing else "REJECT_FIXED_LIQUIDATION_RULES",
        "promotion_eligible": False,
        "r31a_gate_changed": False,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
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


def write_report(output_dir: Path, events: pd.DataFrame, summary: pd.DataFrame, verdict: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(output_dir / "R53C_EVENTS.csv", index=False)
    summary.to_csv(output_dir / "R53C_METRICS.csv", index=False)
    (output_dir / "R53C_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, sort_keys=True), encoding="utf-8"
    )
    lines = [
        "# R53C Fixed Liquidation Event Study",
        "",
        f"- Decision: **{verdict['decision']}**.",
        f"- Passing hypotheses: `{len(verdict['passing_hypotheses'])}`.",
        "- R31A historical gate changed: **False**.",
        "- Promotion eligible: **False**. This is a sparse monthly sample panel.",
        "",
        "| Hypothesis | Split | Trades | Win12 | PF12 | PF20 | Mean bps |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.hypothesis} | {row.split} | {row.trades} | "
            f"{row.win_rate_12bps:.1%} | {row.profit_factor_12bps:.3f} | "
            f"{row.profit_factor_20bps:.3f} | {row.mean_net_bps_12bps:.2f} |"
        )
    lines.extend(
        [
            "",
            "A rule advances only if all six development, validation and locked diagnostic checks pass. Even then, continuous event history and true paper-forward evidence remain mandatory.",
            "",
        ]
    )
    (output_dir / "R53C_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    event_parts: list[pd.DataFrame] = []
    for symbol in CORE_SYMBOLS:
        market = load_market(args.warehouse, symbol)
        liquidations = load_liquidations(args.sample_root, symbol)
        events = candidate_events(market, liquidations, symbol)
        if not events.empty:
            event_parts.append(events)
    if not event_parts:
        raise RuntimeError("R53C produced no candidate events")
    events = remove_overlaps(pd.concat(event_parts, ignore_index=True))
    summary = summarize(events)
    verdict = gate(summary)
    write_report(args.output_dir, events, summary, verdict)
    print(json.dumps(json_safe(verdict), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
