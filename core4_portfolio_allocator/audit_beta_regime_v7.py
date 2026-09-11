"""Independent stress, stability, and statistical audit of frozen V7."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable

import numpy as np
import pandas as pd

from backtest_allocator import SYMBOLS
from backtest_beta_regime_v7 import REPORT_ROOT as V7_REPORT_ROOT
from backtest_breakout_v5 import (
    MAX_HOLD_DAYS,
    MAX_INITIAL_GROSS,
    MAX_SYMBOL_NOTIONAL,
    RISK_PER_TRADE,
    TP_FRACTIONS,
    TP_R_MULTIPLES,
)
from backtest_risk_defined import path_metrics, trade_metrics
from backtest_symmetric_v4 import (
    DATA_ROOT,
    FIRST_OOS_YEAR,
    load_inputs,
    simulate,
    truncate,
)


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "beta_regime_v7_second_audit"
RECENT_START = pd.Timestamp("2025-01-01", tz="UTC")
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 7702
RESEARCH_TRIALS = 7


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def enrich(
    raw_markets: dict[str, pd.DataFrame],
    entry_channel: int = 55,
    exit_channel: int = 20,
    sma_days: int = 200,
) -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    for symbol, source in raw_markets.items():
        frame = source.copy()
        frame["audit_sma"] = frame["close"].rolling(sma_days, min_periods=sma_days).mean()
        frame["audit_entry_high"] = frame["high"].shift(1).rolling(entry_channel).max()
        frame["audit_entry_low"] = frame["low"].shift(1).rolling(entry_channel).min()
        frame["audit_exit_high"] = frame["high"].shift(1).rolling(exit_channel).max()
        frame["audit_exit_low"] = frame["low"].shift(1).rolling(exit_channel).min()
        output[symbol] = frame
    return output


def audit_side(
    markets: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp
) -> str | None:
    frame = markets[symbol]
    btc = markets["BTCUSDT"]
    if date not in frame.index or date not in btc.index:
        return None
    row = frame.loc[date]
    beta = btc.loc[date]
    required = row[["close", "audit_entry_high", "audit_entry_low"]]
    if required.isna().any() or pd.isna(beta["audit_sma"]):
        return None
    side = None
    if row["close"] > row["audit_entry_high"]:
        side = "LONG"
    elif row["close"] < row["audit_entry_low"]:
        side = "SHORT"
    if side == "LONG" and beta["close"] > beta["audit_sma"]:
        return side
    if side == "SHORT" and beta["close"] < beta["audit_sma"]:
        return side
    return None


def audit_exit(
    markets: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp, side: str
) -> bool:
    frame = markets[symbol]
    if date not in frame.index:
        return False
    row = frame.loc[date]
    if side == "LONG":
        return bool(pd.notna(row["audit_exit_low"]) and row["close"] < row["audit_exit_low"])
    return bool(pd.notna(row["audit_exit_high"]) and row["close"] > row["audit_exit_high"])


def run_variant(
    raw_markets: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    *,
    cost_rate: float = 0.0020,
    entry_channel: int = 55,
    sma_days: int = 200,
    entry_signal_lag_days: int = 0,
    funding_credit_fraction: float = 1.0,
    funding_cost_multiplier: float = 1.0,
    symbol_universe: tuple[str, ...] = SYMBOLS,
) -> dict[str, Any]:
    markets = enrich(raw_markets, entry_channel=entry_channel, sma_days=sma_days)
    last_date = max(frame.index.max() for frame in markets.values())
    paths: list[pd.DataFrame] = []
    ledgers: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    for year in range(FIRST_OOS_YEAR, int(last_date.year) + 1):
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = min(pd.Timestamp(f"{year + 1}-01-01", tz="UTC"), last_date + pd.Timedelta(days=1))
        bounded_markets, bounded_funding = truncate(markets, funding, end)
        path, ledger, diagnostics = simulate(
            bounded_markets,
            bounded_funding,
            cost_rate,
            start,
            entry_side_function=audit_side,
            exit_function=audit_exit,
            entry_weekday=None,
            review_weekday=None,
            tp_r_multiples=TP_R_MULTIPLES,
            tp_fractions=TP_FRACTIONS,
            max_hold_days=MAX_HOLD_DAYS,
            risk_per_trade=RISK_PER_TRADE,
            max_symbol_notional=MAX_SYMBOL_NOTIONAL,
            max_initial_gross=MAX_INITIAL_GROSS,
            entry_signal_lag_days=entry_signal_lag_days,
            funding_credit_fraction=funding_credit_fraction,
            funding_cost_multiplier=funding_cost_multiplier,
            symbol_universe=symbol_universe,
        )
        path = path.loc[(path.index >= start) & (path.index < end)].copy()
        if not ledger.empty:
            entry_times = pd.to_datetime(ledger["entry_time"], utc=True)
            ledger = ledger.loc[(entry_times >= start) & (entry_times < end)].copy()
            ledger["fold_year"] = year
            ledgers.append(ledger)
        paths.append(path)
        folds.append({"year": year, **path_metrics(path), **trade_metrics(ledger), **diagnostics})
    combined_path = pd.concat(paths).sort_index()
    combined_trades = pd.concat(ledgers, ignore_index=True)
    fold_frame = pd.DataFrame(folds)
    entry_times = pd.to_datetime(combined_trades["entry_time"], utc=True)
    return {
        "overall": {**path_metrics(combined_path), **trade_metrics(combined_trades)},
        "recent": {
            **path_metrics(combined_path.loc[combined_path.index >= RECENT_START]),
            **trade_metrics(combined_trades.loc[entry_times >= RECENT_START]),
        },
        "path": combined_path,
        "trades": combined_trades,
        "folds": fold_frame,
    }


def moving_block_lower(values: pd.Series, block_length: int = 5) -> float:
    clean = values.dropna().to_numpy(dtype=float)
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    means = np.empty(BOOTSTRAP_SAMPLES)
    offsets = np.arange(block_length)
    blocks_needed = math.ceil(len(clean) / block_length)
    for index in range(BOOTSTRAP_SAMPLES):
        starts = generator.integers(0, len(clean), size=blocks_needed)
        sample_indices = (starts[:, None] + offsets[None, :]) % len(clean)
        means[index] = clean[sample_indices.ravel()[: len(clean)]].mean()
    return float(np.quantile(means, 0.05))


def hac_one_sided_p(values: pd.Series, max_lag: int = 5) -> tuple[float, float]:
    clean = values.dropna().to_numpy(dtype=float)
    centered = clean - clean.mean()
    n = len(clean)
    long_run_variance = float(centered @ centered / n)
    for lag in range(1, min(max_lag, n - 1) + 1):
        covariance = float(centered[lag:] @ centered[:-lag] / n)
        long_run_variance += 2.0 * (1.0 - lag / (max_lag + 1.0)) * covariance
    standard_error = math.sqrt(max(long_run_variance, 0.0) / n)
    z_score = float(clean.mean() / standard_error) if standard_error > 0.0 else math.inf
    p_value = 1.0 - NormalDist().cdf(z_score)
    return p_value, min(1.0, p_value * RESEARCH_TRIALS)


def primary_gates(result: dict[str, Any]) -> dict[str, bool]:
    overall = result["overall"]
    recent = result["recent"]
    folds = result["folds"]
    completed = folds.loc[folds["days"] >= 360]
    return {
        "trades": overall["trades"] >= 100,
        "pf": overall["profit_factor"] >= 1.30,
        "sharpe": overall["sharpe"] >= 1.0,
        "drawdown": overall["max_drawdown"] >= -0.15,
        "positive_years": (completed["total_return"] > 0.0).mean() >= 0.70,
        "recent_pf": recent["profit_factor"] >= 1.20,
        "recent_sharpe": recent["sharpe"] >= 1.0,
        "recent_return": recent["total_return"] > 0.0,
        "funding": result["trades"]["funding_pnl"].abs().sum() > 0.0,
        "reconciled": folds["reconciliation_error"].max() < 1e-10,
        "gross": folds["maximum_initial_gross"].max() <= MAX_INITIAL_GROSS + 1e-9,
    }


def ledger_is_valid(trades: pd.DataFrame) -> bool:
    common = (
        (trades["entry"] > 0.0)
        & (trades["max_holding_hours"] == MAX_HOLD_DAYS * 24)
        & (trades["holding_days"] <= MAX_HOLD_DAYS)
        & trades["paper_only"].astype(bool)
    )
    long_rows = trades["side"] == "LONG"
    short_rows = trades["side"] == "SHORT"
    long_ok = (
        (trades.loc[long_rows, "initial_stop"] < trades.loc[long_rows, "entry"])
        & (trades.loc[long_rows, "entry"] < trades.loc[long_rows, "tp1"])
        & (trades.loc[long_rows, "tp1"] < trades.loc[long_rows, "tp2"])
        & (trades.loc[long_rows, "tp2"] < trades.loc[long_rows, "tp3"])
    ).all()
    short_ok = (
        (trades.loc[short_rows, "initial_stop"] > trades.loc[short_rows, "entry"])
        & (trades.loc[short_rows, "entry"] > trades.loc[short_rows, "tp1"])
        & (trades.loc[short_rows, "tp1"] > trades.loc[short_rows, "tp2"])
        & (trades.loc[short_rows, "tp2"] > trades.loc[short_rows, "tp3"])
        & (trades.loc[short_rows, "tp3"] > 0.0)
    ).all()
    return bool(common.all() and long_ok and short_ok)


def compact(result: dict[str, Any]) -> dict[str, Any]:
    return {"overall": result["overall"], "recent_2025_plus": result["recent"]}


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
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def main() -> int:
    args = parse_args()
    args.report_root.mkdir(parents=True, exist_ok=True)
    raw_markets, funding = load_inputs(args.data_root)
    variants: dict[str, dict[str, Any]] = {}
    specifications = {
        "frozen_20bps": {},
        "cost_30bps": {"cost_rate": 0.0030},
        "cost_40bps": {"cost_rate": 0.0040},
        "entry_delay_1d": {"entry_signal_lag_days": 1},
        "adverse_funding": {"funding_credit_fraction": 0.0, "funding_cost_multiplier": 1.5},
        "sma_180": {"sma_days": 180},
        "sma_220": {"sma_days": 220},
        "entry_channel_50": {"entry_channel": 50},
        "entry_channel_60": {"entry_channel": 60},
    }
    for name, specification in specifications.items():
        print(f"AUDIT {name}", flush=True)
        variants[name] = run_variant(raw_markets, funding, **specification)
    for omitted in SYMBOLS:
        name = f"without_{omitted}"
        print(f"AUDIT {name}", flush=True)
        universe = tuple(symbol for symbol in SYMBOLS if symbol != omitted)
        variants[name] = run_variant(raw_markets, funding, symbol_universe=universe)

    frozen = variants["frozen_20bps"]
    prior = json.loads((V7_REPORT_ROOT / "verdict.json").read_text(encoding="utf-8"))
    replay_keys = ("total_return", "sharpe", "max_drawdown", "trades", "profit_factor", "mean_r")
    replay_exact = all(
        math.isclose(float(frozen["overall"][key]), float(prior["overall"][key]), rel_tol=0.0, abs_tol=1e-12)
        for key in replay_keys
    )
    block_lower = moving_block_lower(frozen["trades"]["realized_r"])
    hac_p, adjusted_p = hac_one_sided_p(frozen["trades"]["realized_r"])

    pnl_by_symbol = frozen["trades"].groupby("symbol")["net_pnl"].sum()
    positive_total = float(pnl_by_symbol.clip(lower=0.0).sum())
    largest_symbol_share = float(pnl_by_symbol.clip(lower=0.0).max() / positive_total)
    parameter_names = ("sma_180", "sma_220", "entry_channel_50", "entry_channel_60")
    leave_one_out_names = tuple(f"without_{symbol}" for symbol in SYMBOLS)

    def passes(result: dict[str, Any], pf: float, sharpe: float, recent_pf: float | None = None) -> bool:
        return bool(
            result["overall"]["profit_factor"] >= pf
            and result["overall"]["sharpe"] >= sharpe
            and result["recent"]["total_return"] > 0.0
            and (recent_pf is None or result["recent"]["profit_factor"] >= recent_pf)
        )

    all_reconciled = all(
        result["folds"]["reconciliation_error"].max() < 1e-10 for result in variants.values()
    )
    all_gross = all(
        result["folds"]["maximum_initial_gross"].max() <= MAX_INITIAL_GROSS + 1e-9
        for result in variants.values()
    )
    checks = {
        "frozen_replay_exact": replay_exact,
        "frozen_primary_gates": all(primary_gates(frozen).values()),
        "cost_30bps": passes(variants["cost_30bps"], 1.40, 0.85, 1.20),
        "cost_40bps": passes(variants["cost_40bps"], 1.25, 0.65, 1.20),
        "entry_delay_1d": passes(variants["entry_delay_1d"], 1.20, 0.60),
        "adverse_funding": passes(variants["adverse_funding"], 1.50, 0.90, 1.20),
        "parameter_neighborhood": all(passes(variants[name], 1.30, 0.75) for name in parameter_names),
        "leave_one_symbol_out": all(passes(variants[name], 1.30, 0.75) for name in leave_one_out_names),
        "moving_block_lower_positive": block_lower > 0.0,
        "hac_bonferroni_p_lt_0p05": adjusted_p < 0.05,
        "signal_contract_complete": ledger_is_valid(frozen["trades"]),
        "symbol_concentration_le_60pct": largest_symbol_share <= 0.60,
        "all_variants_reconciled": all_reconciled,
        "all_variants_gross_cap": all_gross,
    }
    verdict = {
        "experiment": "CORE4_V7_INDEPENDENT_SECOND_AUDIT",
        "protocol_frozen_before_execution": True,
        "research_trials_adjusted": RESEARCH_TRIALS,
        "moving_block_mean_r_lower_5pct": block_lower,
        "hac_one_sided_p": hac_p,
        "hac_bonferroni_adjusted_p": adjusted_p,
        "largest_positive_symbol_pnl_share": largest_symbol_share,
        "checks_passed": sum(checks.values()),
        "checks_total": len(checks),
        "checks": checks,
        "decision": "EXECUTION_TIMING_AUDIT_REQUIRED" if all(checks.values()) else "REJECT_V7_SECOND_AUDIT",
        "production_changed": False,
        "variants": {name: compact(result) for name, result in variants.items()},
    }
    frozen["trades"].to_csv(args.report_root / "frozen_trades.csv", index=False)
    frozen["folds"].to_csv(args.report_root / "frozen_folds.csv", index=False)
    pd.DataFrame(
        [
            {
                "variant": name,
                **{f"oos_{key}": value for key, value in result["overall"].items()},
                **{f"recent_{key}": value for key, value in result["recent"].items()},
            }
            for name, result in variants.items()
        ]
    ).to_csv(args.report_root / "variant_summary.csv", index=False)
    (args.report_root / "verdict.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), encoding="utf-8"
    )
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
