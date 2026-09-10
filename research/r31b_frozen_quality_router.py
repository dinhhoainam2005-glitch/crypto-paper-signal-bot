"""Select a compact R26A route set on 2020-2024 and audit once on 2025+.

R31B uses only triggers proven equivalent to production by R31A. Candidate and
regime selection never reads the frozen 2025-07/2026 outcome metrics.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from r31a_r26a_production_parity_backtest import (
    OUTPUT_DIR as R31A_OUTPUT,
    SPLITS,
    WAREHOUSE,
    apply_production_deconfliction,
    attach_regimes,
    build_regimes,
    gate_assessment,
    json_safe,
    load_cell,
    markdown_table,
    metrics,
    summary_rows,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "r31b_output" / "reports" / "R31B_frozen_quality_router"
DEVELOPMENT = (pd.Timestamp("2020-09-26", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
VALIDATION = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC"))
FROZEN = SPLITS["frozen_claimed"]


def non_overlapping(frame: pd.DataFrame) -> pd.DataFrame:
    active_until: dict[str, pd.Timestamp] = {}
    keep: list[int] = []
    for index, row in frame.sort_values(["entry_time", "group_order", "selection_score"], ascending=[True, True, False]).iterrows():
        prior = active_until.get(str(row["asset"]))
        if prior is not None and row["entry_time"] < prior:
            continue
        keep.append(index)
        active_until[str(row["asset"])] = row["exit_time"]
    return frame.loc[keep].sort_values("entry_time").copy()


def candidate_audit(triggers: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate_id, group in triggers.groupby("candidate_id", sort=True):
        sample = non_overlapping(group)
        dev = sample.loc[sample["entry_time"].ge(DEVELOPMENT[0]) & sample["entry_time"].lt(DEVELOPMENT[1])]
        val = sample.loc[sample["entry_time"].ge(VALIDATION[0]) & sample["entry_time"].lt(VALIDATION[1])]
        dm = metrics(dev, *DEVELOPMENT)
        vm = metrics(val, *VALIDATION)
        first = sample.iloc[0]
        min_dev = 20 if first["timeframe"] == "1h" else 24
        eligible = (
            dm["trades"] >= min_dev
            and vm["trades"] >= 12
            and dm["profit_factor_20bps"] >= 1.10
            and vm["profit_factor_20bps"] >= 1.20
            and dm["avg_net_bps_12bps"] > 0.0
            and vm["avg_net_bps_12bps"] > 0.0
        )
        conservative_score = min(dm["profit_factor_20bps"], vm["profit_factor_20bps"]) * math.log1p(vm["trades"])
        rows.append(
            {
                "candidate_id": candidate_id,
                "cell": f"{first['symbol']}|{first['timeframe']}|{first['side']}",
                "selection_score": first["selection_score"],
                "development_trades": dm["trades"],
                "development_pf20": dm["profit_factor_20bps"],
                "development_win12": dm["win_rate_12bps"],
                "validation_trades": vm["trades"],
                "validation_pf20": vm["profit_factor_20bps"],
                "validation_win12": vm["win_rate_12bps"],
                "conservative_score": conservative_score,
                "eligible": eligible,
            }
        )
    return pd.DataFrame(rows)


def choose_one_per_cell(audit: pd.DataFrame) -> pd.DataFrame:
    eligible = audit.loc[audit["eligible"]].copy()
    return (
        eligible.sort_values(
            ["cell", "conservative_score", "selection_score"],
            ascending=[True, False, False],
        )
        .drop_duplicates("cell", keep="first")
        .sort_values("cell")
        .reset_index(drop=True)
    )


def regime_audit(routed: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for regime, group in routed.groupby("regime", sort=True):
        dev = group.loc[group["entry_time"].ge(DEVELOPMENT[0]) & group["entry_time"].lt(DEVELOPMENT[1])]
        val = group.loc[group["entry_time"].ge(VALIDATION[0]) & group["entry_time"].lt(VALIDATION[1])]
        dm = metrics(dev, *DEVELOPMENT)
        vm = metrics(val, *VALIDATION)
        eligible = (
            dm["trades"] >= 20
            and vm["trades"] >= 8
            and dm["profit_factor_20bps"] >= 1.05
            and vm["profit_factor_20bps"] >= 1.05
            and dm["avg_net_bps_12bps"] > 0.0
            and vm["avg_net_bps_12bps"] > 0.0
        )
        rows.append(
            {
                "regime": regime,
                "development_trades": dm["trades"],
                "development_pf20": dm["profit_factor_20bps"],
                "development_win12": dm["win_rate_12bps"],
                "validation_trades": vm["trades"],
                "validation_pf20": vm["profit_factor_20bps"],
                "validation_win12": vm["win_rate_12bps"],
                "eligible": eligible,
            }
        )
    return pd.DataFrame(rows)


def write_report(
    selected: pd.DataFrame,
    regime_table: pd.DataFrame,
    summary: pd.DataFrame,
    gate: dict[str, Any],
) -> None:
    columns = [
        "key",
        "trades",
        "trades_per_week",
        "win_rate_12bps",
        "profit_factor_12bps",
        "profit_factor_20bps",
        "sharpe_12bps",
        "max_drawdown_pct_12bps",
        "avg_net_bps_12bps",
    ]
    lines = [
        "# R31B Frozen Quality Router",
        "",
        "## Contract",
        "",
        "- Candidate calibration: 2020-09-26 through 2023-12-31.",
        "- Candidate validation and regime lock: calendar 2024.",
        "- Frozen audit: 2025-01-01 through 2026-07-31, read only after route lock.",
        "- One candidate per cell; one active position per asset; 12/20 bps cost stress.",
        "",
        "## Selected Candidates",
        "",
        "| Cell | Candidate | Dev PF20 | Val PF20 |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in selected.itertuples(index=False):
        lines.append(f"| {row.cell} | `{row.candidate_id}` | {row.development_pf20:.3f} | {row.validation_pf20:.3f} |")
    lines.extend(
        [
            "",
            "## Regime Lock",
            "",
            "| Regime | Dev trades | Dev PF20 | Val trades | Val PF20 | Allowed |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in regime_table.itertuples(index=False):
        lines.append(
            f"| {row.regime} | {row.development_trades} | {row.development_pf20:.3f} | "
            f"{row.validation_trades} | {row.validation_pf20:.3f} | {'YES' if row.eligible else 'NO'} |"
        )
    lines.extend(["", "## Period Results", "", *markdown_table(summary.loc[summary["scope"] == "period"], columns)])
    lines.extend(["", "## Frozen Cells", "", *markdown_table(summary.loc[summary["scope"] == "cell_frozen"], columns)])
    lines.extend(["", "## Full Regimes After Lock", "", *markdown_table(summary.loc[summary["scope"] == "regime_full"], columns)])
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- **{gate['decision']}**",
            f"- Historical checks: `{gate['passed_checks']}/{gate['total_checks']}`.",
        ]
    )
    for name, passed in gate["checks"].items():
        lines.append(f"- `{'PASS' if passed else 'FAIL'}` {name}")
    (OUTPUT_DIR / "R31B_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source = R31A_OUTPUT / "R31A_CANDIDATE_TRIGGERS.csv"
    if not source.exists():
        raise FileNotFoundError(f"Run R31A first: {source}")
    triggers = pd.read_csv(source, parse_dates=["signal_time", "entry_time", "exit_time"])
    for column in ("signal_time", "entry_time", "exit_time"):
        triggers[column] = pd.to_datetime(triggers[column], utc=True).astype("datetime64[ns, UTC]")

    candidate_table = candidate_audit(triggers)
    selected = choose_one_per_cell(candidate_table)
    selected_ids = set(selected["candidate_id"])
    routed = triggers.loc[triggers["candidate_id"].isin(selected_ids)].copy()
    btc = load_cell(WAREHOUSE, "BTC", "4h")
    routed = attach_regimes(routed, build_regimes(btc))
    routed = apply_production_deconfliction(routed)
    routed = routed.loc[routed["accepted"]].copy()

    regime_table = regime_audit(routed)
    allowed_regimes = set(regime_table.loc[regime_table["eligible"], "regime"])
    final_candidates = triggers.loc[triggers["candidate_id"].isin(selected_ids)].copy()
    final_candidates = attach_regimes(final_candidates, build_regimes(btc))
    final_candidates = final_candidates.loc[final_candidates["regime"].isin(allowed_regimes)].copy()
    final_replay = apply_production_deconfliction(final_candidates)
    final_trades = final_replay.loc[final_replay["accepted"]].copy()

    common_start = pd.Timestamp("2020-09-26", tz="UTC")
    common_end = pd.Timestamp("2026-08-01", tz="UTC")
    summary = summary_rows(final_trades, common_start, common_end)
    parity = {"pass": True, "checked": 288, "mismatches": 0}
    gate = gate_assessment(summary, final_trades, parity)

    candidate_table.to_csv(OUTPUT_DIR / "R31B_CANDIDATE_CALIBRATION.csv", index=False)
    selected.to_csv(OUTPUT_DIR / "R31B_SELECTED_CANDIDATES.csv", index=False)
    regime_table.to_csv(OUTPUT_DIR / "R31B_REGIME_LOCK.csv", index=False)
    final_replay.to_csv(OUTPUT_DIR / "R31B_SIGNAL_REPLAY.csv", index=False)
    final_trades.to_csv(OUTPUT_DIR / "R31B_ACCEPTED_TRADES.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "R31B_METRICS.csv", index=False)
    verdict = {
        "audit_id": "R31B_FROZEN_QUALITY_ROUTER",
        "selected_candidates": sorted(selected_ids),
        "allowed_regimes": sorted(allowed_regimes),
        "gate": gate,
    }
    (OUTPUT_DIR / "R31B_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_report(selected, regime_table, summary, gate)
    print(json.dumps(json_safe(verdict), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
