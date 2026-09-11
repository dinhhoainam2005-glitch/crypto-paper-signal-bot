"""Clean-room weekly CORE4 plus cash portfolio allocation backtest."""

from __future__ import annotations

import argparse
import json
import math
import zipfile
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "data" / "binance_spot_1d"
REPORT_ROOT = ROOT / "reports" / "first_experiment"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
SMA_DAYS = 200
MOMENTUM_DAYS = (90, 180)
VOL_DAYS = 60
TARGET_VOL = 0.15
MAX_ASSET_WEIGHT = 0.40
BASE_COST = 0.0010
STRESS_COST = 0.0020
SECONDS_PER_DAY = 86400.0
CSV_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    return parser.parse_args()


def parse_exchange_time(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    microseconds = numeric.ge(100_000_000_000_000)
    result.loc[microseconds] = pd.to_datetime(
        numeric.loc[microseconds], unit="us", utc=True, errors="coerce"
    )
    result.loc[~microseconds] = pd.to_datetime(
        numeric.loc[~microseconds], unit="ms", utc=True, errors="coerce"
    )
    return result


def load_symbol(data_root: Path, symbol: str) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for path in sorted((data_root / symbol).glob(f"{symbol}-1d-*.zip")):
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1:
                raise ValueError(f"Unexpected archive members in {path}")
            parts.append(pd.read_csv(archive.open(members[0]), names=CSV_COLUMNS))
    if not parts:
        raise FileNotFoundError(f"No verified daily archives found for {symbol}")
    frame = pd.concat(parts, ignore_index=True)
    frame["date"] = parse_exchange_time(frame["open_time"]).dt.floor("D")
    for column in ("open", "high", "low", "close", "quote_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "close"])
    return (
        frame.sort_values("date")
        .drop_duplicates("date", keep="last")
        .set_index("date")
    )


def capped_inverse_vol(volatility: pd.Series) -> pd.Series:
    clean = volatility.replace([np.inf, -np.inf], np.nan).dropna()
    clean = clean.loc[clean > 0.0]
    weights = pd.Series(0.0, index=volatility.index)
    if clean.empty:
        return weights
    remaining = list(clean.index)
    remaining_weight = 1.0
    while remaining and remaining_weight > 0.0:
        inverse = 1.0 / clean.loc[remaining]
        proposal = inverse / inverse.sum() * remaining_weight
        over = proposal.loc[proposal > MAX_ASSET_WEIGHT].index.tolist()
        if not over:
            weights.loc[remaining] = proposal
            break
        for symbol in over:
            weights.loc[symbol] = MAX_ASSET_WEIGHT
            remaining_weight -= MAX_ASSET_WEIGHT
            remaining.remove(symbol)
    return weights.clip(lower=0.0, upper=MAX_ASSET_WEIGHT)


def build_inputs(data_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frames = {symbol: load_symbol(data_root, symbol) for symbol in SYMBOLS}
    calendar = pd.date_range(
        min(frame.index.min() for frame in frames.values()),
        max(frame.index.max() for frame in frames.values()),
        freq="1D",
        tz="UTC",
    )
    opens = pd.DataFrame(
        {symbol: frame["open"].reindex(calendar).ffill(limit=2) for symbol, frame in frames.items()},
        index=calendar,
    )
    closes = pd.DataFrame(
        {symbol: frame["close"].reindex(calendar).ffill(limit=2) for symbol, frame in frames.items()},
        index=calendar,
    )
    close_returns = closes.pct_change(fill_method=None)
    return opens, closes, close_returns


def allocator_target(
    decision_date: pd.Timestamp,
    closes: pd.DataFrame,
    close_returns: pd.DataFrame,
) -> pd.Series:
    history = closes.loc[:decision_date]
    latest = history.iloc[-1]
    sma = history.rolling(SMA_DAYS, min_periods=SMA_DAYS).mean().iloc[-1]
    momentum_90 = latest / history.shift(MOMENTUM_DAYS[0]).iloc[-1] - 1.0
    momentum_180 = latest / history.shift(MOMENTUM_DAYS[1]).iloc[-1] - 1.0
    vol = close_returns.loc[:decision_date].tail(VOL_DAYS).std(ddof=0) * math.sqrt(365.0)
    eligible = latest.gt(sma) & momentum_90.gt(0.0) & momentum_180.gt(0.0)
    eligible &= latest.notna() & sma.notna() & vol.gt(0.0)
    weights = capped_inverse_vol(vol.where(eligible))
    selected = weights.loc[weights > 0.0].index.tolist()
    if not selected:
        return weights
    sample = close_returns.loc[:decision_date, selected].tail(VOL_DAYS).dropna(how="any")
    if len(sample) >= 30:
        covariance = sample.cov(ddof=0).to_numpy(dtype=float) * 365.0
        vector = weights.loc[selected].to_numpy(dtype=float)
        estimated_vol = float(np.sqrt(max(vector @ covariance @ vector, 0.0)))
    else:
        estimated_vol = float(np.sqrt(np.square(weights.loc[selected] * vol.loc[selected]).sum()))
    scale = min(1.0, TARGET_VOL / estimated_vol) if estimated_vol > 0.0 else 0.0
    return weights * scale


def equal_weight_target(
    decision_date: pd.Timestamp,
    closes: pd.DataFrame,
    close_returns: pd.DataFrame,
) -> pd.Series:
    del close_returns
    history = closes.loc[:decision_date]
    available = history.count().ge(SMA_DAYS) & history.iloc[-1].notna()
    weights = pd.Series(0.0, index=closes.columns)
    if available.any():
        weights.loc[available] = 1.0 / int(available.sum())
    return weights


def simulate(
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    close_returns: pd.DataFrame,
    target_function: Callable[[pd.Timestamp, pd.DataFrame, pd.DataFrame], pd.Series],
    cost_rate: float,
) -> pd.DataFrame:
    forward_returns = opens.shift(-1).div(opens).sub(1.0)
    asset_weights = pd.Series(0.0, index=opens.columns)
    cash_weight = 1.0
    rows: list[dict[str, object]] = []
    for position in range(1, len(opens.index) - 1):
        date = opens.index[position]
        decision_date = opens.index[position - 1]
        turnover = 0.0
        if date.weekday() == 0:
            target = target_function(decision_date, closes, close_returns).fillna(0.0)
            tradable = opens.loc[date].notna() & forward_returns.loc[date].notna()
            target = target.where(tradable, 0.0)
            if target.sum() > 1.0:
                target /= target.sum()
            turnover = float((target - asset_weights).abs().sum())
            asset_weights = target
            cash_weight = 1.0 - float(asset_weights.sum())
        day_asset_returns = forward_returns.loc[date].fillna(0.0)
        gross_return = float((asset_weights * day_asset_returns).sum())
        fee = turnover * cost_rate
        net_return = (1.0 - fee) * (1.0 + gross_return) - 1.0
        denominator = 1.0 + gross_return
        if denominator > 0.0:
            asset_weights = asset_weights * (1.0 + day_asset_returns) / denominator
            cash_weight /= denominator
        rows.append(
            {
                "date": date,
                "return": net_return,
                "gross_return": gross_return,
                "fee": fee,
                "turnover": turnover,
                "gross_exposure": float(asset_weights.sum()),
                "cash_weight": cash_weight,
                **{f"weight_{symbol}": asset_weights[symbol] for symbol in SYMBOLS},
            }
        )
    result = pd.DataFrame(rows).set_index("date")
    result["equity"] = (1.0 + result["return"]).cumprod()
    return result


def max_drawdown(returns: pd.Series) -> float:
    equity = (1.0 + returns.fillna(0.0)).cumprod()
    return float((equity / equity.cummax() - 1.0).min()) if len(equity) else 0.0


def metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    returns = frame["return"].dropna()
    if returns.empty:
        return {
            "days": 0,
            "total_return": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "positive_month_rate": 0.0,
            "annual_turnover": 0.0,
            "average_exposure": 0.0,
        }
    years = max((returns.index[-1] - returns.index[0]).total_seconds() / (365.0 * SECONDS_PER_DAY), 1.0 / 365.0)
    total = float((1.0 + returns).prod() - 1.0)
    standard_deviation = float(returns.std(ddof=0))
    monthly = (1.0 + returns).resample("ME").prod() - 1.0
    return {
        "days": len(returns),
        "total_return": total,
        "cagr": float((1.0 + total) ** (1.0 / years) - 1.0),
        "sharpe": float(returns.mean() / standard_deviation * math.sqrt(365.0)) if standard_deviation > 0.0 else 0.0,
        "max_drawdown": max_drawdown(returns),
        "positive_month_rate": float((monthly > 0.0).mean()),
        "annual_turnover": float(frame["turnover"].sum() / years),
        "average_exposure": float(frame["gross_exposure"].mean()),
    }


def period_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    periods = {
        "FULL": (frame.index.min(), frame.index.max() + pd.Timedelta(days=1)),
        "DEVELOPMENT": (frame.index.min(), pd.Timestamp("2022-01-01", tz="UTC")),
        "BEAR_2022": (pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2023-01-01", tz="UTC")),
        "VALIDATION_2022_2023": (pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
        "HOLDOUT_2024_PLUS": (pd.Timestamp("2024-01-01", tz="UTC"), frame.index.max() + pd.Timedelta(days=1)),
        "RECENT_2025_PLUS": (pd.Timestamp("2025-01-01", tz="UTC"), frame.index.max() + pd.Timedelta(days=1)),
    }
    rows = []
    for name, (start, end) in periods.items():
        selected = frame.loc[(frame.index >= start) & (frame.index < end)]
        rows.append({"period": name, **metrics(selected)})
    return pd.DataFrame(rows)


def yearly_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year, group in frame.groupby(frame.index.year):
        rows.append({"year": int(year), **metrics(group)})
    return pd.DataFrame(rows)


def evaluate_gates(periods: pd.DataFrame, years: pd.DataFrame) -> dict[str, bool]:
    table = periods.set_index("period")
    holdout = table.loc["HOLDOUT_2024_PLUS"]
    full = table.loc["FULL"]
    bear = table.loc["BEAR_2022"]
    return {
        "holdout_at_least_two_years": bool(holdout["days"] >= 730),
        "holdout_cagr_ge_8pct": bool(holdout["cagr"] >= 0.08),
        "holdout_sharpe_ge_1": bool(holdout["sharpe"] >= 1.0),
        "holdout_drawdown_le_20pct": bool(holdout["max_drawdown"] >= -0.20),
        "full_sharpe_ge_1": bool(full["sharpe"] >= 1.0),
        "full_drawdown_le_25pct": bool(full["max_drawdown"] >= -0.25),
        "worst_calendar_year_ge_minus_15pct": bool(years["total_return"].min() >= -0.15),
        "bear_2022_positive": bool(bear["total_return"] > 0.0),
    }


def main() -> int:
    args = parse_args()
    args.report_root.mkdir(parents=True, exist_ok=True)
    opens, closes, close_returns = build_inputs(args.data_root)
    base = simulate(opens, closes, close_returns, allocator_target, BASE_COST)
    stress = simulate(opens, closes, close_returns, allocator_target, STRESS_COST)
    benchmark = simulate(opens, closes, close_returns, equal_weight_target, STRESS_COST)
    base_periods = period_metrics(base)
    periods = period_metrics(stress)
    years = yearly_metrics(stress)
    benchmark_periods = period_metrics(benchmark)
    gates = evaluate_gates(periods, years)
    verdict = {
        "experiment": "CORE4_CLEANROOM_WEEKLY_ALLOCATOR_V1",
        "parameter_search_count": 0,
        "checks_passed": sum(gates.values()),
        "checks_total": len(gates),
        "checks": gates,
        "decision": "PAPER_OBSERVATION_ALLOWED" if all(gates.values()) else "REJECT_FIRST_EXPERIMENT",
        "production_changed": False,
    }
    base.to_csv(args.report_root / "base_daily_path.csv")
    stress.to_csv(args.report_root / "stress_daily_path.csv")
    benchmark.to_csv(args.report_root / "benchmark_daily_path.csv")
    base_periods.to_csv(args.report_root / "base_periods.csv", index=False)
    periods.to_csv(args.report_root / "stress_periods.csv", index=False)
    years.to_csv(args.report_root / "stress_years.csv", index=False)
    benchmark_periods.to_csv(args.report_root / "benchmark_periods.csv", index=False)
    (args.report_root / "verdict.json").write_text(
        json.dumps(verdict, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    benchmark_table = benchmark_periods.set_index("period")
    report_lines = [
        "# CORE4 Clean-Room Weekly Allocator V1",
        "",
        f"**{verdict['decision']}**",
        "",
        "The rules and gates were frozen before this run. No parameter search was performed.",
        "Stress results include 20 bps cost per one-way asset turnover.",
        "",
        "| Period | Return | CAGR | Sharpe | Max DD | Average exposure | Equal-weight return | Equal-weight DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in periods.itertuples(index=False):
        comparison = benchmark_table.loc[row.period]
        report_lines.append(
            f"| {row.period} | {row.total_return:.2%} | {row.cagr:.2%} | "
            f"{row.sharpe:.3f} | {row.max_drawdown:.2%} | {row.average_exposure:.1%} | "
            f"{comparison['total_return']:.2%} | {comparison['max_drawdown']:.2%} |"
        )
    report_lines.extend(
        [
            "",
            f"Gate: {sum(gates.values())}/{len(gates)} checks passed.",
            "The first experiment is rejected and cannot proceed to paper observation.",
            "The existing bot, Telegram, Render, and real-money settings were not changed.",
        ]
    )
    (args.report_root / "REPORT.md").write_text(
        "\n".join(report_lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(verdict, indent=2), flush=True)
    print(periods.to_string(index=False), flush=True)
    print(years.to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
