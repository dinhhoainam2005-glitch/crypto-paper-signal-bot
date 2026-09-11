"""Build the concise evidence pack for the frozen V7 paper-forward candidate."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backtest_risk_defined import trade_metrics


ROOT = Path(__file__).resolve().parent
EXECUTION_ROOT = ROOT / "reports" / "beta_regime_v7_execution_audit"
SECOND_ROOT = ROOT / "reports" / "beta_regime_v7_second_audit"


def group_metrics(trades: pd.DataFrame, column: str) -> pd.DataFrame:
    rows = []
    for value, group in trades.groupby(column):
        metrics = trade_metrics(group)
        rows.append(
            {
                column: value,
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "mean_r": metrics["mean_r"],
                "net_pnl": group["net_pnl"].sum(),
                "tp1_rate": group["tp1_hit"].mean(),
                "tp2_rate": group["tp2_hit"].mean(),
                "tp3_rate": group["tp3_hit"].mean(),
                "median_holding_days": group["holding_days"].median(),
            }
        )
    return pd.DataFrame(rows)


def percent(value: float) -> str:
    return f"{value:.2%}"


def metric_table(frame: pd.DataFrame, label: str) -> list[str]:
    lines = [
        f"| {label} | Trades | Win | PF | Mean R | TP1 | TP2 | TP3 | Median hold |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row[label]} | {int(row['trades'])} | {percent(row['win_rate'])} | "
            f"{row['profit_factor']:.3f} | {row['mean_r']:.3f} | {percent(row['tp1_rate'])} | "
            f"{percent(row['tp2_rate'])} | {percent(row['tp3_rate'])} | "
            f"{row['median_holding_days']:.1f}d |"
        )
    return lines


def main() -> int:
    verdict = json.loads((EXECUTION_ROOT / "verdict.json").read_text(encoding="utf-8"))
    second = json.loads((SECOND_ROOT / "verdict.json").read_text(encoding="utf-8"))
    trades = pd.read_csv(EXECUTION_ROOT / "oos_trades.csv")
    folds = pd.read_csv(EXECUTION_ROOT / "oos_folds.csv")
    by_symbol = group_metrics(trades, "symbol")
    by_side = group_metrics(trades, "side")
    by_symbol.to_csv(EXECUTION_ROOT / "oos_by_symbol.csv", index=False)
    by_side.to_csv(EXECUTION_ROOT / "oos_by_side.csv", index=False)

    overall = verdict["overall"]
    recent = verdict["recent_2025_plus"]
    years = overall["days"] / 365.25
    trades_per_week = overall["trades"] / (overall["days"] / 7.0)
    recent_per_week = recent["trades"] / (recent["days"] / 7.0)
    cost_40 = second["variants"]["cost_40bps"]["overall"]
    delay = second["variants"]["entry_delay_1d"]["overall"]
    lines = [
        "# CORE4 V7 Evidence Report",
        "",
        "**PAPER_FORWARD_CANDIDATE - RESEARCH GATES PASSED**",
        "",
        "This is the first clean-room candidate to pass the primary audit, the",
        "pre-registered independent stress audit, and the exact 1h execution-timing",
        "audit. It is not authorization for real-money trading.",
        "",
        "## Frozen Signal",
        "",
        "- Universe: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT USD-M futures.",
        "- Timeframe: 1d; both LONG and SHORT.",
        "- Entry: next 00:00 UTC open after a 55-day Donchian breakout.",
        "- Regime: LONG only above BTC SMA200; SHORT only below BTC SMA200.",
        "- Stop: 2 ATR20. TP1/TP2/TP3: 1R/2R/4R, exiting 20%/30%/50%.",
        "- Stop moves to entry after TP1 and to TP1 after TP2.",
        "- Exit: opposite 20-day channel or 90-day maximum hold.",
        "- Risk: 0.35% equity per trade; 20% symbol cap; 60% portfolio gross cap.",
        "- Cost assumption: 20 bps each entry/exit; exact event-time funding.",
        "",
        "## Headline Evidence",
        "",
        "| Scope | Trades | Trades/week | Win | PF | Mean R | Return | CAGR | Sharpe | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| OOS 2021-Aug 2026 | {overall['trades']} | {trades_per_week:.3f} | {percent(overall['win_rate'])} | {overall['profit_factor']:.3f} | {overall['mean_r']:.3f} | {percent(overall['total_return'])} | {percent(overall['cagr'])} | {overall['sharpe']:.3f} | {percent(overall['max_drawdown'])} |",
        f"| 2025+ | {recent['trades']} | {recent_per_week:.3f} | {percent(recent['win_rate'])} | {recent['profit_factor']:.3f} | {recent['mean_r']:.3f} | {percent(recent['total_return'])} | {percent(recent['cagr'])} | {recent['sharpe']:.3f} | {percent(recent['max_drawdown'])} |",
        "",
        f"Observed OOS length: {years:.2f} years. Moving-block bootstrap 5% lower mean-R: "
        f"{verdict['moving_block_mean_r_lower_5pct']:.3f}. Bonferroni-adjusted HAC p-value: "
        f"{verdict['hac_bonferroni_adjusted_p']:.4f}.",
        "",
        "## Stress Evidence",
        "",
        f"- 40 bps each way: PF {cost_40['profit_factor']:.3f}, Sharpe {cost_40['sharpe']:.3f}, return {percent(cost_40['total_return'])}.",
        f"- Entry delayed one extra day: PF {delay['profit_factor']:.3f}, Sharpe {delay['sharpe']:.3f}, return {percent(delay['total_return'])}.",
        f"- Independent second audit: {second['checks_passed']}/{second['checks_total']} gates passed.",
        f"- Exact 1h execution audit: {verdict['checks_passed']}/{verdict['checks_total']} gates passed.",
        "",
        "## By Symbol",
        "",
        *metric_table(by_symbol, "symbol"),
        "",
        "## By Side",
        "",
        *metric_table(by_side, "side"),
        "",
        "## By Year",
        "",
        "| Year | Trades | Win | PF | Return | Sharpe | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in folds.iterrows():
        lines.append(
            f"| {int(row['year'])} | {int(row['trades'])} | {percent(row['win_rate'])} | "
            f"{row['profit_factor']:.3f} | {percent(row['total_return'])} | "
            f"{row['sharpe']:.3f} | {percent(row['max_drawdown'])} |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "V7 is retained unchanged as a research-grade paper-forward candidate.",
            "The low 0.35% trade risk explains the modest CAGR; leverage or risk was",
            "not increased after seeing results. Production, Telegram, Render, and",
            "real-money execution remain unchanged pending a separate paper-forward",
            "authorization and operational build.",
        ]
    )
    (EXECUTION_ROOT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report={EXECUTION_ROOT / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
