"""Causal, point-in-time replay of the exact deployed R26A signal core.

The replay imports the frozen production candidate registry. It does not search,
retune, rank, or remove candidates. Signals are generated from information known
at each candle close, entered at NEXT_OPEN, exited at the planned candle open,
and deconflicted in the same group order as the web service.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_signal_bot.strategy import (  # noqa: E402
    ASSET_BY_SYMBOL,
    CANDIDATES,
    PORTFOLIO_METRICS,
    STRATEGY_ID,
    candidate_groups,
    evaluate_latest,
)


WAREHOUSE = Path(r"D:\@Nam\btc_eth_signal_research\research_base\R14E_multi_asset_mtf")
OUTPUT_DIR = ROOT / "r31a_output" / "reports" / "R31A_R26A_production_parity"
ASSETS = ("BTC", "ETH", "SOL", "BNB")
TIMEFRAMES = ("1h", "4h")
TF_DELTA = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4)}
BASE_COST = 0.0012
STRESS_COST = 0.0020
SEED = 20260910

SPLITS = {
    "development": (pd.Timestamp("2020-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "validation": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "frozen_claimed": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-08-01", tz="UTC")),
    "recent_diagnostic": (pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-08-01", tz="UTC")),
}

READ_COLUMNS = [
    "bar_open_utc",
    "feature_available_utc",
    "px_open",
    "px_high",
    "px_low",
    "px_close",
    "px_quote_volume",
    "mkt_taker_buy_quote_asset_volume",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--parity-samples", type=int, default=24)
    return parser.parse_args()


def parquet_path(root: Path, asset: str, timeframe: str) -> Path:
    matches = list((root / timeframe).glob(f"{asset}_common_research_base_{timeframe}_*.parquet"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one parquet for {asset} {timeframe}, found {matches}")
    return matches[0]


def load_cell(root: Path, asset: str, timeframe: str) -> pd.DataFrame:
    frame = pd.read_parquet(parquet_path(root, asset, timeframe), columns=READ_COLUMNS)
    frame["bar_open"] = pd.to_datetime(frame.pop("bar_open_utc"), utc=True, errors="coerce")
    frame["feature_available"] = pd.to_datetime(
        frame.pop("feature_available_utc"), utc=True, errors="coerce"
    )
    numeric = [column for column in READ_COLUMNS if not column.endswith("_utc")]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["bar_open", "px_open", "px_high", "px_low", "px_close", "px_quote_volume"])
        .drop_duplicates("bar_open", keep="last")
        .sort_values("bar_open")
        .set_index("bar_open")
    )
    delta = TF_DELTA[timeframe]
    expected_available = frame.index.to_series(index=frame.index) + delta
    mismatch = frame["feature_available"].ne(expected_available)
    if bool(mismatch.any()):
        raise ValueError(f"Feature availability mismatch in {asset} {timeframe}: {int(mismatch.sum())}")

    close = frame["px_close"]
    quote = frame["px_quote_volume"]
    prior_mean = quote.shift(1).rolling(20, min_periods=20).mean()
    prior_std = quote.shift(1).rolling(20, min_periods=20).std(ddof=0)
    frame["runtime_volz20"] = ((quote - prior_mean) / prior_std).where(prior_std > 0.0, 0.0)
    frame["runtime_ret1"] = close.pct_change()
    frame["runtime_logret"] = np.log(close / close.shift(1))
    frame["runtime_rv24"] = frame["runtime_logret"].rolling(24, min_periods=12).std(ddof=0)
    frame["runtime_ema12"] = close.ewm(span=12, adjust=False).mean()
    buy_quote = frame["mkt_taker_buy_quote_asset_volume"]
    frame["runtime_quote_imbalance"] = buy_quote / quote * 2.0 - 1.0
    frame["row_number"] = np.arange(len(frame), dtype=np.int64)
    return frame


def trend(frame: pd.DataFrame, lookback: int) -> pd.Series:
    key = f"runtime_trend_{lookback}"
    if key not in frame:
        frame[key] = frame["px_close"] / frame["px_close"].shift(lookback) - 1.0
    return frame[key]


def levels(frame: pd.DataFrame, lookback: int) -> tuple[pd.Series, pd.Series]:
    high_key = f"runtime_prior_high_{lookback}"
    low_key = f"runtime_prior_low_{lookback}"
    if high_key not in frame:
        frame[high_key] = frame["px_high"].shift(1).rolling(lookback, min_periods=lookback).max()
        frame[low_key] = frame["px_low"].shift(1).rolling(lookback, min_periods=lookback).min()
    return frame[high_key], frame[low_key]


class ReplayFeatures:
    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self.frames = frames
        self.breadth_cache: dict[tuple[str, int, str, float], pd.DataFrame] = {}

    def breadth(self, timeframe: str, lookback: int, direction: str, minimum: float) -> pd.DataFrame:
        key = (timeframe, lookback, direction, minimum)
        if key in self.breadth_cache:
            return self.breadth_cache[key]
        panel = pd.concat(
            {asset: trend(self.frames[(asset, timeframe)], lookback) for asset in ASSETS},
            axis=1,
        ).sort_index()
        if direction == "SHORT":
            panel = -panel
        result = pd.DataFrame(index=panel.index)
        result["count"] = panel.ge(minimum).sum(axis=1)
        result["assets"] = panel.notna().sum(axis=1)
        result["mean"] = panel.mean(axis=1, skipna=True)
        result["leader"] = panel["BTC"]
        self.breadth_cache[key] = result
        return result

    def candidate_mask(self, candidate: Any) -> pd.Series:
        frame = self.frames[(candidate.asset, candidate.timeframe)]
        p = candidate.params
        sign = 1.0 if candidate.direction == "LONG" else -1.0
        ret1 = frame["runtime_ret1"] * sign
        volz = frame["runtime_volz20"]
        mask = frame["row_number"].ge(71)

        has_breadth = all(key in p for key in ("breadth_min", "breadth_n", "market_min", "regime_lb"))
        if has_breadth:
            breadth = self.breadth(
                candidate.timeframe,
                int(p["regime_lb"]),
                candidate.direction,
                float(p["breadth_min"]),
            ).reindex(frame.index)
            market_ok = breadth["count"].ge(int(p["breadth_n"])) & breadth["mean"].ge(float(p["market_min"]))
            if bool(p.get("leader_required", False)):
                market_ok &= breadth["leader"].ge(float(p["breadth_min"]))
            mask &= market_ok.fillna(False)

        if candidate.family == "breadth_momentum":
            signal = trend(frame, int(p["signal_lb"])) * sign
            mask &= signal.ge(float(p["signal_min"]))
            mask &= ret1.ge(float(p["ret1_min"]))
            mask &= volz.ge(float(p["volz_min"]))
        elif candidate.family == "breadth_pullback_reclaim":
            prior_high, prior_low = levels(frame, int(p["signal_lb"]))
            asset_trend = trend(frame, int(p["regime_lb"])) * sign
            if candidate.direction == "LONG":
                pullback = -(frame["px_close"] / prior_high - 1.0)
                reclaim = frame["px_close"].gt(frame["runtime_ema12"]) & ret1.gt(0.0)
            else:
                pullback = frame["px_close"] / prior_low - 1.0
                reclaim = frame["px_close"].lt(frame["runtime_ema12"]) & ret1.gt(0.0)
            mask &= asset_trend.ge(float(p["asset_regime_min"]))
            mask &= pullback.ge(float(p["pullback_min"]))
            mask &= reclaim
        elif candidate.family in {"breadth_breakout", "taker_flow_breakout"}:
            prior_high, prior_low = levels(frame, int(p["lb"]))
            if candidate.direction == "LONG":
                breakout = frame["px_close"].gt(prior_high * (1.0 + float(p["buffer"])))
            else:
                breakout = frame["px_close"].lt(prior_low * (1.0 - float(p["buffer"])))
            mask &= breakout
            if candidate.family == "breadth_breakout":
                mask &= volz.ge(float(p["volz_min"]))
            else:
                min_volz = max(float(p["volz_min"]), float(p["quality_volz_min"]))
                mask &= volz.ge(min_volz)
                mask &= (frame["runtime_quote_imbalance"] * sign).ge(float(p["flow_thr"]))
                mask &= frame["runtime_rv24"].ge(float(p["quality_realized_vol_24_min"]))
        elif candidate.family == "breadth_ema_stack":
            ema6 = frame["px_close"].ewm(span=6, adjust=False).mean()
            ema12 = frame["runtime_ema12"]
            ema24 = frame["px_close"].ewm(span=24, adjust=False).mean()
            if candidate.direction == "LONG":
                stack = (ema6 > ema12) & (ema12 > ema24)
                slope = ema12 / ema12.shift(12) - 1.0
            else:
                stack = (ema6 < ema12) & (ema12 < ema24)
                slope = -(ema12 / ema12.shift(12) - 1.0)
            mask &= stack & slope.ge(float(p["slope_min"]))
        else:
            raise ValueError(f"Unsupported family: {candidate.family}")
        return mask.fillna(False)


def build_raw_events(
    frames: dict[tuple[str, str], pd.DataFrame],
    common_start: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[tuple[str, str], pd.Series], pd.DataFrame]:
    features = ReplayFeatures(frames)
    records: list[pd.DataFrame] = []
    trigger_records: list[pd.DataFrame] = []
    winners: dict[tuple[str, str], pd.Series] = {}
    for group_order, ((symbol, timeframe), candidates) in enumerate(candidate_groups().items()):
        asset = ASSET_BY_SYMBOL[symbol]
        frame = frames[(asset, timeframe)]
        winner = pd.Series(pd.NA, index=frame.index, dtype="object")
        ranked = sorted(candidates, key=lambda item: item.selection_score, reverse=True)
        for candidate in ranked:
            triggered = features.candidate_mask(candidate)
            selected = triggered & winner.isna()
            winner.loc[selected] = candidate.candidate_id
            for event_mask, destination in ((triggered, trigger_records), (selected, records)):
                if not bool(event_mask.any()):
                    continue
                chosen = frame.loc[event_mask, ["feature_available", "px_open"]].copy()
                chosen["signal_time"] = chosen.index
                chosen["entry_time"] = chosen["feature_available"]
                chosen["entry_price"] = frame["px_open"].shift(-1).loc[event_mask].to_numpy()
                chosen["exit_time"] = chosen["entry_time"] + TF_DELTA[timeframe] * candidate.hold_bars
                chosen["exit_price"] = frame["px_open"].shift(-(candidate.hold_bars + 1)).loc[event_mask].to_numpy()
                chosen["symbol"] = symbol
                chosen["asset"] = asset
                chosen["timeframe"] = timeframe
                chosen["side"] = candidate.direction
                chosen["family"] = candidate.family
                chosen["candidate_id"] = candidate.candidate_id
                chosen["sleeve_id"] = candidate.sleeve_id
                chosen["hold_bars"] = candidate.hold_bars
                chosen["selection_score"] = candidate.selection_score
                chosen["group_order"] = group_order
                destination.append(chosen.reset_index(drop=True))
        winners[(symbol, timeframe)] = winner

    def prepare(items: list[pd.DataFrame]) -> pd.DataFrame:
        events = pd.concat(items, ignore_index=True)
        events = events.loc[events["signal_time"].ge(common_start)].dropna(subset=["entry_price", "exit_price"]).copy()
        side = np.where(events["side"].eq("SHORT"), -1.0, 1.0)
        events["gross_return"] = side * (events["exit_price"] / events["entry_price"] - 1.0)
        events["net_return_12bps"] = events["gross_return"] - BASE_COST
        events["net_return_20bps"] = events["gross_return"] - STRESS_COST
        return events.sort_values(
            ["entry_time", "group_order", "selection_score"],
            ascending=[True, True, False],
        ).reset_index(drop=True)

    return prepare(records), winners, prepare(trigger_records)


def apply_production_deconfliction(events: pd.DataFrame) -> pd.DataFrame:
    active_until: dict[str, pd.Timestamp] = {}
    accepted: list[bool] = []
    reasons: list[str] = []
    for row in events.itertuples(index=False):
        prior_exit = active_until.get(row.asset)
        is_open = prior_exit is not None and row.entry_time < prior_exit
        if is_open:
            accepted.append(False)
            reasons.append("ACTIVE_POSITION")
            continue
        accepted.append(True)
        reasons.append("")
        active_until[row.asset] = row.exit_time
    result = events.copy()
    result["accepted"] = accepted
    result["suppressed_reason"] = reasons
    return result


def build_regimes(btc_4h: pd.DataFrame) -> pd.DataFrame:
    daily = btc_4h[["px_close"]].resample("1D").last().dropna().rename(columns={"px_close": "close"})
    close = daily["close"]
    daily["ret_1d"] = close.pct_change()
    daily["ret_3d"] = close.pct_change(3)
    daily["ret_30d"] = close.pct_change(30)
    daily["ret_90d"] = close.pct_change(90)
    daily["ma50"] = close.rolling(50, min_periods=30).mean()
    daily["ma200"] = close.rolling(200, min_periods=120).mean()
    daily["prior_ath"] = close.cummax().shift(1)
    daily["drawdown"] = close / daily["prior_ath"] - 1.0
    daily["recent_worst_drawdown"] = daily["drawdown"].rolling(120, min_periods=30).min()
    daily["regime"] = "TRANSITION"
    conditions = [
        ("SIDEWAY", daily["ret_90d"].abs().le(0.10)),
        ("BEAR", daily["ret_90d"].le(-0.20) & close.lt(daily["ma200"])),
        ("BULL", daily["ret_90d"].ge(0.20) & close.gt(daily["ma200"])),
        (
            "RECOVERY",
            daily["recent_worst_drawdown"].le(-0.25)
            & daily["ret_30d"].ge(0.10)
            & close.gt(daily["ma50"]),
        ),
        ("ATH", close.ge(daily["prior_ath"] * 0.97) & daily["ret_90d"].gt(0.0)),
        ("BLACK_SWAN", daily["ret_1d"].le(-0.10) | daily["ret_3d"].le(-0.20)),
    ]
    for name, condition in conditions:
        daily.loc[condition.fillna(False), "regime"] = name
    daily["available_time"] = daily.index + pd.Timedelta(days=1)
    return daily[["available_time", "regime"]].reset_index(drop=True).sort_values("available_time")


def attach_regimes(trades: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame:
    left = trades.copy()
    right = regimes.copy()
    left["entry_time"] = pd.to_datetime(left["entry_time"], utc=True).astype("datetime64[ns, UTC]")
    right["available_time"] = pd.to_datetime(right["available_time"], utc=True).astype("datetime64[ns, UTC]")
    return pd.merge_asof(
        left.sort_values("entry_time"),
        right,
        left_on="entry_time",
        right_on="available_time",
        direction="backward",
    ).drop(columns=["available_time"])


def profit_factor(values: list[float]) -> float:
    wins = sum(value for value in values if value > 0.0)
    losses = abs(sum(value for value in values if value < 0.0))
    if losses > 0.0:
        return wins / losses
    return 999.0 if wins > 0.0 else 0.0


def max_drawdown_pct(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= max(1.0 + value, 0.000001)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst * 100.0


def probability_positive(values: list[float], iterations: int = 2000) -> float:
    if not values:
        return 0.0
    rng = random.Random(SEED)
    block = max(2, int(math.sqrt(len(values))))
    positive = 0
    for _ in range(iterations):
        sample: list[float] = []
        while len(sample) < len(values):
            start = rng.randrange(len(values))
            sample.extend(values[(start + offset) % len(values)] for offset in range(block))
        positive += fmean(sample[: len(values)]) > 0.0
    return positive / iterations


def metrics(
    trades: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    ordered = trades.sort_values(["exit_time", "group_order"])
    returns12 = ordered["net_return_12bps"].astype(float).tolist()
    returns20 = ordered["net_return_20bps"].astype(float).tolist()
    days = max((end - start).total_seconds() / 86400.0, 1.0)
    tpw = len(ordered) / days * 7.0
    std = pstdev(returns12) if len(returns12) > 1 else 0.0
    sharpe = fmean(returns12) / std * math.sqrt(max(tpw * 52.0, 1.0)) if std > 0.0 else 0.0
    contributions = ordered.groupby("symbol")["net_return_12bps"].sum() if not ordered.empty else pd.Series(dtype=float)
    total = float(contributions.sum())
    max_contribution = (
        max((max(float(value), 0.0) / total for value in contributions), default=1.0)
        if total > 0.0
        else 1.0
    )
    return {
        "trades": int(len(ordered)),
        "trades_per_week": float(tpw),
        "win_rate_12bps": float(np.mean(np.asarray(returns12) > 0.0)) if returns12 else 0.0,
        "profit_factor_12bps": float(profit_factor(returns12)),
        "profit_factor_20bps": float(profit_factor(returns20)),
        "sharpe_12bps": float(sharpe),
        "max_drawdown_pct_12bps": float(max_drawdown_pct(returns12)),
        "risk_adjusted_max_drawdown_pct_12bps": float(max_drawdown_pct([value * 0.25 for value in returns12])),
        "avg_net_bps_12bps": float(fmean(returns12) * 10000.0) if returns12 else 0.0,
        "compounded_return_pct_12bps": float((np.prod(1.0 + np.clip(returns12, -0.99, 10.0)) - 1.0) * 100.0) if returns12 else 0.0,
        "probability_positive": float(probability_positive(returns12)) if returns12 else 0.0,
        "long_trades": int(ordered["side"].eq("LONG").sum()) if not ordered.empty else 0,
        "short_trades": int(ordered["side"].eq("SHORT").sum()) if not ordered.empty else 0,
        "trades_1h": int(ordered["timeframe"].eq("1h").sum()) if not ordered.empty else 0,
        "trades_4h": int(ordered["timeframe"].eq("4h").sum()) if not ordered.empty else 0,
        "max_symbol_profit_contribution": float(max_contribution),
    }


def summary_rows(
    trades: pd.DataFrame,
    full_start: pd.Timestamp,
    full_end: pd.Timestamp,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    windows = {"full": (full_start, full_end), **SPLITS}
    for name, (start, end) in windows.items():
        subset = trades.loc[trades["entry_time"].ge(max(start, full_start)) & trades["entry_time"].lt(min(end, full_end))]
        rows.append({"scope": "period", "key": name, "start": max(start, full_start), "end": min(end, full_end), **metrics(subset, max(start, full_start), min(end, full_end))})

    for (symbol, timeframe, side), subset in trades.groupby(["symbol", "timeframe", "side"], sort=True):
        rows.append({"scope": "cell_full", "key": f"{symbol}|{timeframe}|{side}", "start": full_start, "end": full_end, **metrics(subset, full_start, full_end)})

    frozen_start, frozen_end = SPLITS["frozen_claimed"]
    frozen = trades.loc[trades["entry_time"].ge(frozen_start) & trades["entry_time"].lt(frozen_end)]
    for (symbol, timeframe, side), subset in frozen.groupby(["symbol", "timeframe", "side"], sort=True):
        rows.append({"scope": "cell_frozen", "key": f"{symbol}|{timeframe}|{side}", "start": frozen_start, "end": frozen_end, **metrics(subset, frozen_start, frozen_end)})

    for regime, subset in trades.groupby("regime", dropna=False, sort=True):
        rows.append({"scope": "regime_full", "key": str(regime), "start": full_start, "end": full_end, **metrics(subset, full_start, full_end)})

    half_year_key = trades["entry_time"].dt.year.astype(str) + "H" + np.where(trades["entry_time"].dt.month.le(6), "1", "2")
    for period, subset in trades.groupby(half_year_key, sort=True):
        year = int(period[:4])
        start = pd.Timestamp(f"{year}-{'01' if period.endswith('H1') else '07'}-01", tz="UTC")
        end = pd.Timestamp(f"{year + 1}-01-01", tz="UTC") if period.endswith("H2") else pd.Timestamp(f"{year}-07-01", tz="UTC")
        rows.append({"scope": "half_year", "key": period, "start": start, "end": end, **metrics(subset, start, end)})
    return pd.DataFrame(rows)


def kline_rows(frame: pd.DataFrame, until: pd.Timestamp) -> list[list[Any]]:
    selected = frame.loc[:until].tail(221)
    output: list[list[Any]] = []
    for timestamp, row in selected.iterrows():
        open_ms = int(timestamp.timestamp() * 1000)
        close_ms = int((timestamp + pd.Timedelta(milliseconds=1)).timestamp() * 1000)
        output.append(
            [
                open_ms,
                row["px_open"],
                row["px_high"],
                row["px_low"],
                row["px_close"],
                0.0,
                close_ms,
                row["px_quote_volume"],
                0,
                0.0,
                row["mkt_taker_buy_quote_asset_volume"],
            ]
        )
    return output


def runtime_parity_check(
    frames: dict[tuple[str, str], pd.DataFrame],
    winners: dict[tuple[str, str], pd.Series],
    sample_count: int,
) -> dict[str, Any]:
    rng = random.Random(SEED)
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for symbol, timeframe in candidate_groups():
        asset = ASSET_BY_SYMBOL[symbol]
        winner = winners[(symbol, timeframe)].dropna()
        frame = frames[(asset, timeframe)]
        signal_times = list(winner.index)
        no_signal_times = list(winners[(symbol, timeframe)].loc[lambda value: value.isna()].index[220:-2])
        rng.shuffle(signal_times)
        rng.shuffle(no_signal_times)
        samples = signal_times[:sample_count] + no_signal_times[:sample_count]
        for signal_time in samples:
            delta = TF_DELTA[timeframe]
            until = signal_time + delta
            market = {
                f"{market_asset}USDT": kline_rows(frames[(market_asset, timeframe)], until)
                for market_asset in ASSETS
            }
            result = evaluate_latest(
                symbol=symbol,
                timeframe=timeframe,
                klines=market[symbol],
                premium_klines=[],
                derivatives_state_available=False,
                market_klines_by_symbol=market,
                now_ms=int(until.timestamp() * 1000),
            )
            actual = result["signals"][0]["candidate"]["candidate_id"] if result.get("signals") else None
            expected_value = winners[(symbol, timeframe)].get(signal_time)
            expected = None if pd.isna(expected_value) else str(expected_value)
            checked += 1
            if actual != expected:
                mismatches.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "signal_time": signal_time.isoformat(),
                        "expected": expected,
                        "actual": actual,
                    }
                )
    return {
        "checked": checked,
        "mismatches": len(mismatches),
        "pass": not mismatches,
        "examples": mismatches[:20],
    }


def gate_assessment(summary: pd.DataFrame, trades: pd.DataFrame, parity: dict[str, Any]) -> dict[str, Any]:
    frozen = summary.loc[(summary["scope"] == "period") & (summary["key"] == "frozen_claimed")].iloc[0]
    cell_frozen = summary.loc[summary["scope"] == "cell_frozen"]
    populated_regimes = summary.loc[(summary["scope"] == "regime_full") & summary["trades"].ge(20)]
    half_year = summary.loc[summary["scope"] == "half_year"]
    checks = {
        "runtime_parity": bool(parity["pass"]),
        "closed_trades_ge_150": int(frozen["trades"]) >= 150,
        "win_rate_ge_60pct": float(frozen["win_rate_12bps"]) >= 0.60,
        "pf12_ge_1p50": float(frozen["profit_factor_12bps"]) >= 1.50,
        "pf20_ge_1p20": float(frozen["profit_factor_20bps"]) >= 1.20,
        "sharpe_ge_1p20": float(frozen["sharpe_12bps"]) >= 1.20,
        "risk_adjusted_max_dd_le_10pct": float(frozen["risk_adjusted_max_drawdown_pct_12bps"]) >= -10.0,
        "probability_positive_ge_80pct": float(frozen["probability_positive"]) >= 0.80,
        "long_ge_30": int(frozen["long_trades"]) >= 30,
        "short_ge_30": int(frozen["short_trades"]) >= 30,
        "each_timeframe_ge_30": int(frozen["trades_1h"]) >= 30 and int(frozen["trades_4h"]) >= 30,
        "max_symbol_contribution_le_50pct": float(frozen["max_symbol_profit_contribution"]) <= 0.50,
        "all_populated_cells_pf20_ge_1p10": bool((cell_frozen["profit_factor_20bps"] >= 1.10).all()) if not cell_frozen.empty else False,
        "all_populated_regimes_pf20_ge_1": bool((populated_regimes["profit_factor_20bps"] >= 1.0).all()) if not populated_regimes.empty else False,
        "positive_half_year_majority": bool((half_year["avg_net_bps_12bps"] > 0.0).mean() >= 0.70) if not half_year.empty else False,
    }
    passed = all(checks.values())
    return {
        "decision": "ELIGIBLE_FOR_ACCELERATED_FORWARD_REVIEW" if passed else "KEEP_TRADE_A_PLUS_LOCKED",
        "historical_gate_pass": passed,
        "checks": checks,
        "passed_checks": sum(checks.values()),
        "total_checks": len(checks),
        "note": "Historical replay can shorten but cannot eliminate independent forward validation.",
        "accepted_trades_full": int(len(trades)),
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


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    if frame.empty:
        return ["No rows."]
    labels = {
        "key": "Segment",
        "trades": "Trades",
        "trades_per_week": "TPW",
        "win_rate_12bps": "Win12",
        "profit_factor_12bps": "PF12",
        "profit_factor_20bps": "PF20",
        "sharpe_12bps": "Sharpe12",
        "max_drawdown_pct_12bps": "MaxDD12",
        "avg_net_bps_12bps": "Avg bps",
    }
    lines = ["| " + " | ".join(labels.get(column, column) for column in columns) + " |"]
    lines.append("| " + " | ".join("---" if column == "key" else "---:" for column in columns) + " |")
    for row in frame[columns].itertuples(index=False, name=None):
        rendered: list[str] = []
        for column, value in zip(columns, row):
            if column == "key":
                rendered.append(str(value))
            elif column == "trades":
                rendered.append(str(int(value)))
            elif column == "win_rate_12bps":
                rendered.append(f"{float(value):.1%}")
            else:
                rendered.append(f"{float(value):.3f}")
        lines.append("| " + " | ".join(rendered) + " |")
    return lines


def write_report(
    path: Path,
    summary: pd.DataFrame,
    raw_events: pd.DataFrame,
    trades: pd.DataFrame,
    parity: dict[str, Any],
    gate: dict[str, Any],
    common_start: pd.Timestamp,
    common_end: pd.Timestamp,
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
        "# R31A R26A Production-Parity Backtest",
        "",
        "## Audit Contract",
        "",
        f"- Strategy: `{STRATEGY_ID}`.",
        f"- Candidate registry: exact deployed set, `{len(CANDIDATES)}` candidates; no search or retuning.",
        f"- Common evidence window: `{common_start.isoformat()}` to `{common_end.isoformat()}`.",
        "- Decision timing: completed candle only; entry `NEXT_OPEN`; exit at planned candle open.",
        "- Portfolio rule: one active position per asset, production group order preserved.",
        "- Costs: 12 bps base and 20 bps stress per round trip.",
        f"- Runtime equivalence samples: `{parity['checked']}`; mismatches: `{parity['mismatches']}`.",
        "",
        "## Period Results",
        "",
        *markdown_table(summary.loc[summary["scope"] == "period"], columns),
        "",
        "## Frozen Cell Results",
        "",
        *markdown_table(summary.loc[summary["scope"] == "cell_frozen"], columns),
        "",
        "## Full-Cycle Regimes",
        "",
        *markdown_table(summary.loc[summary["scope"] == "regime_full"], columns),
        "",
        "## Half-Year Stability",
        "",
        *markdown_table(summary.loc[summary["scope"] == "half_year"], columns),
        "",
        "## Historical Gate",
        "",
        f"- Decision: **{gate['decision']}**.",
        f"- Checks passed: `{gate['passed_checks']}/{gate['total_checks']}`.",
    ]
    for name, passed in gate["checks"].items():
        lines.append(f"- `{'PASS' if passed else 'FAIL'}` {name}")
    lines.extend(
        [
            "",
            "## Integrity Notes",
            "",
            f"- Raw selected group signals: `{len(raw_events)}`; accepted after active-asset deconfliction: `{len(trades)}`.",
            "- The `frozen_claimed` label follows the original research split. It is not a newly created untouched future sample.",
            "- Historical L2/liquidation truth is not part of R26A trade triggers, so it is not injected retroactively here.",
            "- This audit can justify a shorter forward gate only if every robustness check passes. It cannot by itself authorize real-money trading.",
            "",
            "## Comparison With Embedded Metrics",
            "",
            "The production constants are retained below for discrepancy review; they are not treated as evidence in the R31A verdict.",
            "",
            "```json",
            json.dumps(json_safe(PORTFOLIO_METRICS), indent=2, sort_keys=True),
            "```",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("Loading eight production context cells...", flush=True)
    frames = {(asset, timeframe): load_cell(args.warehouse, asset, timeframe) for timeframe in TIMEFRAMES for asset in ASSETS}
    common_start = max(frame.index[71] for frame in frames.values())
    common_end = min(frame.index[-1] + TF_DELTA[timeframe] for (asset, timeframe), frame in frames.items())
    print(f"Common causal window: {common_start} -> {common_end}", flush=True)

    raw_events, winners, candidate_triggers = build_raw_events(frames, common_start)
    replay = apply_production_deconfliction(raw_events)
    accepted = replay.loc[replay["accepted"]].copy()
    accepted = attach_regimes(accepted, build_regimes(frames[("BTC", "4h")]))
    print(f"Signals: raw={len(raw_events)} accepted={len(accepted)}", flush=True)

    parity = runtime_parity_check(frames, winners, max(args.parity_samples, 1))
    print(f"Runtime parity: {parity['checked']} checks, {parity['mismatches']} mismatches", flush=True)
    summary = summary_rows(accepted, common_start, common_end)
    gate = gate_assessment(summary, accepted, parity)

    replay.to_csv(args.output_dir / "R31A_SIGNAL_REPLAY.csv", index=False)
    candidate_triggers.to_csv(args.output_dir / "R31A_CANDIDATE_TRIGGERS.csv", index=False)
    accepted.to_csv(args.output_dir / "R31A_ACCEPTED_TRADES.csv", index=False)
    summary.to_csv(args.output_dir / "R31A_METRICS.csv", index=False)
    payload = {
        "audit_id": "R31A_R26A_PRODUCTION_PARITY",
        "strategy_id": STRATEGY_ID,
        "candidate_count": len(CANDIDATES),
        "common_start": common_start,
        "common_end": common_end,
        "raw_signals": len(raw_events),
        "accepted_trades": len(accepted),
        "parity": parity,
        "gate": gate,
    }
    (args.output_dir / "R31A_VERDICT.json").write_text(
        json.dumps(json_safe(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.output_dir / "R31A_REPORT.md",
        summary,
        raw_events,
        accepted,
        parity,
        gate,
        common_start,
        common_end,
    )
    print(json.dumps(json_safe(payload), indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
