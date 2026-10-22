"""Legacy spot-cycle diagnostic for the V18 channel family.

This extends regime coverage into 2018-2020, before a common CORE4 Futures
universe existed. It is diagnostic only and cannot select a replacement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from audit_beta_regime_v7 import audit_exit, audit_side, enrich
from audit_full_history_tournament_v18 import json_safe, summarize, trim_result
from backtest_allocator import DATA_ROOT as SPOT_ROOT, load_symbol
from backtest_breakout_v5 import (
    MAX_HOLD_DAYS,
    MAX_INITIAL_GROSS,
    MAX_SYMBOL_NOTIONAL,
    RISK_PER_TRADE,
    TP_FRACTIONS,
    TP_R_MULTIPLES,
)
from backtest_symmetric_v4 import STRESS_COST, simulate, true_range, truncate


ROOT = Path(__file__).resolve().parent
REPORT_ROOT = ROOT / "reports" / "legacy_spot_cycles_v18"
LEGACY_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")
START = pd.Timestamp("2018-01-01", tz="UTC")
END = pd.Timestamp("2021-01-01", tz="UTC")
CHANNELS = ((20, 10), (40, 15), (55, 20), (70, 25), (90, 30))


def load_legacy() -> dict[str, pd.DataFrame]:
    markets = {}
    for symbol in LEGACY_SYMBOLS:
        frame = load_symbol(SPOT_ROOT, symbol)[["open", "high", "low", "close"]].copy()
        frame["atr20"] = true_range(frame).rolling(20, min_periods=20).mean()
        markets[symbol] = frame
    return markets


def main() -> int:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    raw = load_legacy()
    funding = {
        symbol: pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
        for symbol in LEGACY_SYMBOLS
    }
    results = {}
    for entry, exit_ in CHANNELS:
        name = f"CHANNEL_{entry}_{exit_}"
        print(f"V18 LEGACY {name}", flush=True)
        markets = enrich(raw, entry_channel=entry, exit_channel=exit_, sma_days=200)
        bounded, bounded_funding = truncate(markets, funding, END)
        path, trades, diagnostics = simulate(
            bounded,
            bounded_funding,
            STRESS_COST,
            START,
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
            symbol_universe=LEGACY_SYMBOLS,
        )
        path, trades = trim_result(path, trades, START, END)
        years = {}
        for year in (2018, 2019, 2020):
            year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
            year_end = pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
            year_path, year_trades = trim_result(path, trades, year_start, year_end)
            years[str(year)] = summarize(year_path, year_trades, year_start, year_end)
        results[name] = {
            "full": summarize(path, trades, START, END),
            "years": years,
            "diagnostics": diagnostics,
        }

    verdict = {
        "experiment": "CORE4_V18_LEGACY_SPOT_CYCLE_DIAGNOSTIC",
        "period": "2018-01-01/2020-12-31",
        "symbols": list(LEGACY_SYMBOLS),
        "execution_limitations": [
            "spot daily bars rather than exact H1 Futures execution",
            "no Futures funding",
            "SOL unavailable, so this is not the production CORE4 universe",
        ],
        "selection_authorized": False,
        "results": results,
    }
    safe = json_safe(verdict)
    (REPORT_ROOT / "verdict.json").write_text(
        json.dumps(safe, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    lines = [
        "# V18 LEGACY SPOT-CYCLE DIAGNOSTIC",
        "",
        "Diagnostic only: this period predates a common CORE4 Futures universe.",
        "",
        "| Channel | Trades/wk | Win | PF | Return | Sharpe | Max DD | 2018 return | 2020 return |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, result in results.items():
        full = result["full"]
        lines.append(
            f"| {name} | {full['trades_per_week']:.3f} | {full['win_rate']:.1%} | "
            f"{full['profit_factor']:.3f} | {full['total_return']:.2%} | "
            f"{full['sharpe']:.3f} | {full['max_drawdown']:.2%} | "
            f"{result['years']['2018']['total_return']:.2%} | "
            f"{result['years']['2020']['total_return']:.2%} |"
        )
    lines.extend(
        [
            "",
            "These results can reject a fragile channel family but cannot promote one.",
            "",
        ]
    )
    (REPORT_ROOT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(safe, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
