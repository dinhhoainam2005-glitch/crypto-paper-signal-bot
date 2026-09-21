"""Confirm sparse R53 liquidation events with causal OI and book state.

This is the pre-registered interaction test that R53C could not perform by
itself. It uses verified 1h Binance metrics and book-depth rows from 2023 onward
and never changes the underlying event outcomes or costs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS = ROOT / ".codex_deps"
if LOCAL_DEPS.exists() and str(LOCAL_DEPS) not in sys.path:
    sys.path.insert(0, str(LOCAL_DEPS))

import numpy as np
import pandas as pd

from r53c_liquidation_event_study import json_safe, metrics


EVENTS = ROOT / "r53c_output" / "reports" / "R53C_LIQUIDATION_EVENT_STUDY" / "R53C_EVENTS.csv"
METRICS_ROOT = Path(r"D:\@Nam\btc_eth_signal_research\features\R50H_core4_metrics")
BOOK_ROOT = Path(r"D:\@Nam\btc_eth_signal_research\features\R29I_bookdepth_quality")
OUTPUT_DIR = ROOT / "r53d_output" / "reports" / "R53D_LIQUIDATION_BOOK_CONFIRMATION"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
SPLITS = {
    "development_2023": (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "validation_2024": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "locked_diagnostic": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-08-01", tz="UTC")),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=EVENTS)
    parser.add_argument("--metrics-root", type=Path, default=METRICS_ROOT)
    parser.add_argument("--book-root", type=Path, default=BOOK_ROOT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def load_confirmation(metrics_root: Path, book_root: Path, symbol: str) -> pd.DataFrame:
    metrics_frame = pd.read_parquet(
        metrics_root / symbol / f"{symbol}_metrics_1h.parquet",
        columns=[
            "available_at",
            "oi_value_change_1",
            "metrics_window_complete",
        ],
    )
    asset = symbol.removesuffix("USDT")
    book = pd.read_parquet(
        book_root / "1h" / f"{asset}_bookdepth_quality_1h.parquet",
        columns=[
            "available_at",
            "book_source_available",
            "book_data_quality_ok",
            "book_bid_notional_p1_change",
            "book_ask_notional_p1_change",
            "book_imbalance_p1_last",
            "book_imbalance_p1_change",
        ],
    )
    metrics_frame["signal_time"] = pd.to_datetime(
        metrics_frame.pop("available_at"), utc=True, errors="coerce"
    )
    book["signal_time"] = pd.to_datetime(book.pop("available_at"), utc=True, errors="coerce")
    frame = metrics_frame.merge(book, on="signal_time", how="inner", validate="one_to_one")
    frame["symbol"] = symbol
    frame["confirmation_quality_ok"] = (
        frame["metrics_window_complete"].fillna(False).astype(bool)
        & frame["book_source_available"].fillna(False).astype(bool)
        & frame["book_data_quality_ok"].fillna(False).astype(bool)
        & frame[
            [
                "oi_value_change_1",
                "book_bid_notional_p1_change",
                "book_ask_notional_p1_change",
                "book_imbalance_p1_last",
                "book_imbalance_p1_change",
            ]
        ]
        .notna()
        .all(axis=1)
    )
    return frame


def confirmation_mask(frame: pd.DataFrame) -> pd.Series:
    hypothesis = frame["hypothesis"]
    oi_falling = frame["oi_value_change_1"].lt(0.0)
    bid_depletion = frame["book_bid_notional_p1_change"].lt(0.0) & frame[
        "book_imbalance_p1_change"
    ].le(0.0)
    ask_depletion = frame["book_ask_notional_p1_change"].lt(0.0) & frame[
        "book_imbalance_p1_change"
    ].ge(0.0)
    bid_absorption = frame["book_bid_notional_p1_change"].ge(0.0) & frame[
        "book_imbalance_p1_last"
    ].gt(0.0)
    ask_absorption = frame["book_ask_notional_p1_change"].ge(0.0) & frame[
        "book_imbalance_p1_last"
    ].lt(0.0)
    selected = (
        (hypothesis.eq("SHORT_DELEVERAGING_CONTINUATION") & oi_falling & bid_depletion)
        | (hypothesis.eq("LONG_SQUEEZE_CONTINUATION") & oi_falling & ask_depletion)
        | (hypothesis.eq("LONG_LIQUIDATION_REVERSAL") & oi_falling & bid_absorption)
        | (hypothesis.eq("SHORT_SQUEEZE_REVERSAL") & oi_falling & ask_absorption)
    )
    return selected & frame["confirmation_quality_ok"]


def enrich(events: pd.DataFrame, confirmations: pd.DataFrame) -> pd.DataFrame:
    working = events.copy()
    for column in ("bar_open", "signal_time", "entry_time", "exit_time"):
        working[column] = pd.to_datetime(working[column], utc=True, errors="coerce")
    result = working.merge(
        confirmations,
        on=["symbol", "signal_time"],
        how="left",
        validate="many_to_one",
    )
    result["confirmed"] = confirmation_mask(result).fillna(False)
    return result


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    hypotheses = sorted(events["hypothesis"].unique())
    rows: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        candidate = events.loc[events["hypothesis"].eq(hypothesis)]
        for split, (start, end) in SPLITS.items():
            period = candidate.loc[candidate["entry_time"].ge(start) & candidate["entry_time"].lt(end)]
            confirmed = period.loc[period["confirmed"]]
            rows.append(
                {
                    "hypothesis": hypothesis,
                    "split": split,
                    "candidate_events": int(len(period)),
                    "quality_ready": int(period["confirmation_quality_ok"].fillna(False).sum()),
                    **metrics(confirmed),
                }
            )
    return pd.DataFrame(rows)


def gate(summary: pd.DataFrame) -> dict[str, Any]:
    decisions: dict[str, Any] = {}
    for hypothesis in sorted(summary["hypothesis"].unique()):
        indexed = summary.loc[summary["hypothesis"].eq(hypothesis)].set_index("split")
        checks = {
            "development_trades_ge_8": int(indexed.loc["development_2023", "trades"]) >= 8,
            "development_pf20_ge_1p10": float(
                indexed.loc["development_2023", "profit_factor_20bps"]
            )
            >= 1.10,
            "validation_trades_ge_5": int(indexed.loc["validation_2024", "trades"]) >= 5,
            "validation_pf20_ge_1p10": float(
                indexed.loc["validation_2024", "profit_factor_20bps"]
            )
            >= 1.10,
            "locked_trades_ge_8": int(indexed.loc["locked_diagnostic", "trades"]) >= 8,
            "locked_pf20_ge_1p10": float(
                indexed.loc["locked_diagnostic", "profit_factor_20bps"]
            )
            >= 1.10,
        }
        decisions[hypothesis] = {
            "checks": checks,
            "passed_checks": int(sum(checks.values())),
            "total_checks": len(checks),
            "pass": bool(all(checks.values())),
        }
    passing = [name for name, item in decisions.items() if item["pass"]]
    return {
        "audit_id": "R53D_LIQUIDATION_BOOK_CONFIRMATION",
        "rules": "FIXED_OI_FALL_PLUS_TOP1_DEPLETION_OR_ABSORPTION",
        "hypotheses": decisions,
        "passing_hypotheses": passing,
        "decision": "ADVANCE_TO_CONTINUOUS_HISTORY" if passing else "REJECT_BOOK_CONFIRMED_RULES",
        "promotion_eligible": False,
        "r31a_gate_changed": False,
    }


def write_report(
    output_dir: Path,
    events: pd.DataFrame,
    summary: pd.DataFrame,
    verdict: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(output_dir / "R53D_ENRICHED_EVENTS.csv", index=False)
    summary.to_csv(output_dir / "R53D_METRICS.csv", index=False)
    (output_dir / "R53D_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, sort_keys=True), encoding="utf-8"
    )
    lines = [
        "# R53D Liquidation + OI + Book Confirmation",
        "",
        f"- Decision: **{verdict['decision']}**.",
        f"- Passing hypotheses: `{len(verdict['passing_hypotheses'])}`.",
        "- Promotion eligible: **False**.",
        "",
        "| Hypothesis | Split | Candidates | Quality | Trades | Win12 | PF20 | Mean bps |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.hypothesis} | {row.split} | {row.candidate_events} | "
            f"{row.quality_ready} | {row.trades} | {row.win_rate_12bps:.1%} | "
            f"{row.profit_factor_20bps:.3f} | {row.mean_net_bps_12bps:.2f} |"
        )
    lines.extend(
        [
            "",
            "This fixed interaction test is allowed to reject the hypothesis. Sparse monthly samples and 2023+ book history cannot by themselves promote a trading sleeve.",
            "",
        ]
    )
    (output_dir / "R53D_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    events = pd.read_csv(args.events)
    confirmation_parts = [
        load_confirmation(args.metrics_root, args.book_root, symbol) for symbol in SYMBOLS
    ]
    confirmations = pd.concat(confirmation_parts, ignore_index=True)
    enriched = enrich(events, confirmations)
    summary = summarize(enriched)
    verdict = gate(summary)
    write_report(args.output_dir, enriched, summary, verdict)
    print(json.dumps(json_safe(verdict), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
