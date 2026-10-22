"""Post-selection audit: V7 core plus confirmed BNB/SOL daily fast sleeve.

Symbol selection uses the already revealed V16 periods, so a pass can only
authorize prospective paper-shadow observation.
"""

from __future__ import annotations

import json

import pandas as pd

from audit_beta_regime_v7 import enrich
from audit_execution_timing_v7 import exact_simulate, load_exact_inputs
from audit_fast_confirmed_v16 import (
    CORE_WEIGHT,
    FAST_WEIGHT,
    PERIODS,
    REPORT_ROOT as V16_REPORT_ROOT,
    improvement_checks,
    json_safe,
    public,
    scale_trades,
    summarize,
    combine_paths,
)
from backtest_symmetric_v4 import DATA_ROOT, STRESS_COST


REPORT_ROOT = V16_REPORT_ROOT.parent / "alt_fast_confirmed_v17"
FAST_SYMBOLS = ("BNBUSDT", "SOLUSDT")


def run_period(core_daily, fast_daily, hourly, funding, start, end):
    core_path, core_trades, _ = exact_simulate(
        core_daily, hourly, funding, start, end, cost_rate=STRESS_COST
    )
    fast_path, fast_trades, _ = exact_simulate(
        fast_daily,
        hourly,
        funding,
        start,
        end,
        cost_rate=STRESS_COST,
        min_concurrent_signals=2,
        tradable_symbols=FAST_SYMBOLS,
    )
    core_path = core_path.loc[(core_path.index >= start) & (core_path.index < end)]
    fast_path = fast_path.loc[(fast_path.index >= start) & (fast_path.index < end)]
    core_entries = pd.to_datetime(core_trades["entry_time"], utc=True)
    fast_entries = pd.to_datetime(fast_trades["entry_time"], utc=True)
    core_trades = core_trades.loc[(core_entries >= start) & (core_entries < end)].copy()
    fast_trades = fast_trades.loc[(fast_entries >= start) & (fast_entries < end)].copy()
    combined_path = combine_paths(core_path, fast_path, CORE_WEIGHT)
    combined_trades = pd.concat(
        [
            scale_trades(core_trades, "V7_CORE_55_20", CORE_WEIGHT),
            scale_trades(fast_trades, "ALT_FAST_CONFIRMED_20_10", FAST_WEIGHT),
        ],
        ignore_index=True,
    )
    return {
        "baseline_v7": summarize(core_path, core_trades, start, end),
        "fast_confirmed": summarize(fast_path, fast_trades, start, end),
        "candidate_v17": summarize(combined_path, combined_trades, start, end),
        "candidate_path": combined_path,
        "candidate_trades": combined_trades,
        "fast_trades": fast_trades,
    }


def main() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    core_daily, hourly, funding = load_exact_inputs(DATA_ROOT)
    raw_daily = {
        symbol: frame[["open", "high", "low", "close", "atr20"]].copy()
        for symbol, frame in core_daily.items()
    }
    fast_daily = enrich(raw_daily, entry_channel=20, exit_channel=10, sma_days=200)
    verdict = {
        "experiment": "CORE4_V17_ALT_FAST_CONFIRMED_DAILY",
        "post_selected_from_v16": True,
        "fast_tradable_symbols": list(FAST_SYMBOLS),
        "confirmation_universe": list(core_daily),
        "weights": {"core": CORE_WEIGHT, "fast": FAST_WEIGHT},
        "periods": {},
        "production_changed": False,
    }
    all_pass = True
    for name, (start, end) in PERIODS.items():
        print(f"V17 {name}", flush=True)
        result = run_period(core_daily, fast_daily, hourly, funding, start, end)
        checks = improvement_checks(
            {**result, "candidate_v16": result["candidate_v17"]}
        )
        verdict["periods"][name] = {**public(result), "checks": checks}
        all_pass = all_pass and all(checks.values())
        result["candidate_path"].to_csv(REPORT_ROOT / f"{name.lower()}_path.csv")
        result["candidate_trades"].to_csv(
            REPORT_ROOT / f"{name.lower()}_trades.csv", index=False
        )
    verdict["strict_replacement_gate_pass"] = all_pass
    verdict["replacement_decision"] = (
        "REPLACEMENT_AUDIT_REQUIRED" if all_pass else "REJECT_AS_V7_REPLACEMENT"
    )
    verdict["observation_decision"] = "PROSPECTIVE_PAPER_SHADOW_ONLY"
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
