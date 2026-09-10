"""R29C causal, availability-aware regime ensemble search.

This is a deterministic rule-library audit across BTC/ETH/SOL/BNB and
15m/1h/4h/1d. Candidate selection uses train plus validation only. The 2025+
frozen window is evaluated after selection and never contributes to ranking.
True historical L2/liquidation maps are intentionally excluded because the
warehouse does not contain them; flow/large-trade features remain labelled as
proxies and derivatives rules require explicit availability flags.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "r29c_output" / "reports" / "R29C_regime_ensemble_search"
WAREHOUSE = Path(r"D:\@Nam\btc_eth_signal_research\research_base\R14E_multi_asset_mtf")
SPOT_BTC_DAILY = Path(
    r"D:\@Nam\bot18_data_cleanroom\data\canonical\coinbase_exchange_spot_mtf"
    r"\R18AL\BTC-USD\candles\1d.csv"
)

ASSETS = ("BTC", "ETH", "SOL", "BNB")
TIMEFRAMES = ("15m", "1h", "4h", "1d")
TF_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}
HOLD_GRID = {
    "15m": (4, 8, 16),
    "1h": (3, 6, 12),
    "4h": (2, 3, 6),
    "1d": (1, 3, 5),
}
FREQUENCY_TARGET_TPW = {"15m": 8.0, "1h": 7.0, "4h": 2.0, "1d": 0.9}
MIN_VALIDATION_TRADES = {"15m": 80, "1h": 60, "4h": 30, "1d": 12}

TRAIN_START = pd.Timestamp("2020-01-01T00:00:00Z")
TRAIN_END = pd.Timestamp("2023-01-01T00:00:00Z")
VALIDATION_END = pd.Timestamp("2025-01-01T00:00:00Z")
FROZEN_END = pd.Timestamp("2026-08-01T00:00:00Z")
BASE_COST_BPS = 12.0
STRESS_COST_BPS = 20.0
RISK_FRACTION = 0.25
MAX_PORTFOLIO_POSITIONS = 4

READ_COLUMNS = [
    "asset",
    "symbol",
    "timeframe",
    "bar_open_utc",
    "feature_available_utc",
    "px_open",
    "px_high",
    "px_low",
    "px_close",
    "px_quote_volume",
    "mkt_taker_quote_imbalance_derived",
    "quote_imbalance",
    "signed_large_100k_share",
    "signed_large_1m_share",
    "premium_close_prior_z_24",
    "drv_open_interest_log_change_1",
    "drv_open_interest_pct_change_24h",
    "drv_basis_close",
    "drv_basis_change_1",
    "drv_funding_last_rate",
    "drv_funding_sum_trailing_24h",
    "drv_funding_sum_trailing_72h",
    "quote_volume_prior_z_20",
    "full_derivatives_state_available",
    "micro_source_available",
    "market_flow_source_available",
]


@dataclass
class CandidateResult:
    candidate_id: str
    asset: str
    timeframe: str
    direction: str
    family: str
    hold_bars: int
    params: dict[str, Any]
    selected_indices: np.ndarray
    train: dict[str, Any]
    validation: dict[str, Any]
    validation_stress: dict[str, Any]
    selection_score: float
    selection_pass: bool


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
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def candidate_id(asset: str, timeframe: str, direction: str, family: str, hold: int, params: dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    return f"r29c_{asset}_{timeframe}_{direction}_{family}_h{hold}_{digest}"


def profit_factor(returns: np.ndarray) -> float:
    wins = float(returns[returns > 0.0].sum())
    losses = float(returns[returns < 0.0].sum())
    return wins / abs(losses) if losses < 0.0 else (math.inf if wins > 0.0 else 0.0)


def max_drawdown(returns: np.ndarray) -> float:
    if len(returns) == 0:
        return 0.0
    equity = np.cumprod(1.0 + np.clip(returns, -0.99, 10.0))
    equity = np.concatenate(([1.0], equity))
    return float(np.min(equity / np.maximum.accumulate(equity) - 1.0))


def metrics_from_returns(returns: np.ndarray, days: float) -> dict[str, Any]:
    returns = returns[np.isfinite(returns)]
    if len(returns) == 0:
        return {
            "trades": 0,
            "trades_per_week": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_net_bps": 0.0,
        }
    trades_per_week = len(returns) / max(days / 7.0, 1.0 / 7.0)
    std = float(np.std(returns))
    sharpe = (
        float(np.mean(returns)) / std * math.sqrt(max(trades_per_week * 52.0, 1.0))
        if std > 0.0
        else 0.0
    )
    return {
        "trades": int(len(returns)),
        "trades_per_week": float(trades_per_week),
        "win_rate": float(np.mean(returns > 0.0)),
        "profit_factor": float(profit_factor(returns)),
        "sharpe": float(sharpe),
        "max_drawdown_pct": max_drawdown(returns) * 100.0,
        "avg_net_bps": float(np.mean(returns) * 10000.0),
    }


def period_days(start: pd.Timestamp, end: pd.Timestamp) -> float:
    return max((end - start).total_seconds() / 86400.0, 1.0)


def non_overlapping_indices(mask: np.ndarray, hold_bars: int) -> np.ndarray:
    raw = np.flatnonzero(mask)
    if len(raw) == 0:
        return raw
    selected: list[int] = []
    next_allowed = -1
    for index in raw:
        if index < next_allowed:
            continue
        selected.append(int(index))
        next_allowed = int(index) + int(hold_bars)
    return np.asarray(selected, dtype=np.int64)


def load_btc_regimes() -> pd.DataFrame:
    frame = pd.read_csv(SPOT_BTC_DAILY, usecols=["timestamp_utc", "close"])
    frame["bar_open"] = pd.to_datetime(frame.pop("timestamp_utc"), utc=True)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    close = frame["close"]
    frame["ret_1d"] = close.pct_change()
    frame["ret_3d"] = close.pct_change(3)
    frame["ret_30d"] = close.pct_change(30)
    frame["ret_90d"] = close.pct_change(90)
    frame["ma50"] = close.rolling(50, min_periods=30).mean()
    frame["ma200"] = close.rolling(200, min_periods=120).mean()
    frame["prior_ath"] = close.cummax().shift(1)
    frame["drawdown"] = close / frame["prior_ath"] - 1.0
    frame["recent_worst_drawdown"] = frame["drawdown"].rolling(120, min_periods=30).min()
    conditions = {
        "SIDEWAY": frame["ret_90d"].abs() <= 0.10,
        "BEAR": (frame["ret_90d"] <= -0.20) & (close < frame["ma200"]),
        "BULL": (frame["ret_90d"] >= 0.20) & (close > frame["ma200"]),
        "RECOVERY": (
            (frame["recent_worst_drawdown"] <= -0.25)
            & (frame["ret_30d"] >= 0.10)
            & (close > frame["ma50"])
        ),
        "ATH": (close >= frame["prior_ath"] * 0.97) & (frame["ret_90d"] > 0.0),
        "BLACK_SWAN": (frame["ret_1d"] <= -0.10) | (frame["ret_3d"] <= -0.20),
    }
    frame["regime"] = "TRANSITION"
    for name, condition in conditions.items():
        frame.loc[condition.fillna(False), "regime"] = name
    frame["available_time"] = (frame["bar_open"] + pd.Timedelta(days=1)).astype(
        "datetime64[ns, UTC]"
    )
    return frame[["available_time", "regime"]].dropna().sort_values("available_time")


def parquet_path(asset: str, timeframe: str) -> Path:
    matches = list((WAREHOUSE / timeframe).glob(f"{asset}_common_research_base_{timeframe}_*.parquet"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one parquet for {asset} {timeframe}, found {matches}")
    return matches[0]


def load_cell(asset: str, timeframe: str, regimes: pd.DataFrame) -> pd.DataFrame:
    frame = pd.read_parquet(parquet_path(asset, timeframe), columns=READ_COLUMNS)
    frame["bar_open"] = pd.to_datetime(
        frame.pop("bar_open_utc"), utc=True, errors="coerce"
    ).astype("datetime64[ns, UTC]")
    frame["signal_time"] = pd.to_datetime(
        frame.pop("feature_available_utc"), utc=True, errors="coerce"
    ).astype("datetime64[ns, UTC]")
    numeric = [column for column in READ_COLUMNS if column not in {"asset", "symbol", "timeframe", "bar_open_utc", "feature_available_utc"}]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["bar_open", "signal_time", "px_open", "px_close"]).sort_values("bar_open").reset_index(drop=True)

    close = frame["px_close"]
    high = frame["px_high"]
    low = frame["px_low"]
    log_return = np.log(close).diff()
    frame["ret_1"] = close.pct_change()
    for lookback in (4, 6, 12, 24, 48, 72):
        frame[f"ret_{lookback}"] = close.pct_change(lookback)
    frame["rv_24"] = log_return.rolling(24, min_periods=16).std().clip(lower=1e-6)
    frame["rv_72"] = log_return.rolling(72, min_periods=48).std().clip(lower=1e-6)
    frame["ema_20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    frame["ema_72"] = close.ewm(span=72, adjust=False, min_periods=48).mean()
    rolling_mean = close.rolling(20, min_periods=20).mean()
    rolling_std = close.rolling(20, min_periods=20).std().replace(0.0, np.nan)
    frame["close_z_20"] = (close - rolling_mean) / rolling_std
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    frame["atr_pct_14"] = true_range.rolling(14, min_periods=14).mean() / close
    frame["lower_wick"] = np.minimum(frame["px_open"], close) - low
    frame["upper_wick"] = high - np.maximum(frame["px_open"], close)
    frame["body_abs"] = (close - frame["px_open"]).abs().clip(lower=close * 1e-6)
    flow = frame["mkt_taker_quote_imbalance_derived"].where(
        frame["mkt_taker_quote_imbalance_derived"].notna(), frame["quote_imbalance"]
    )
    frame["flow"] = flow
    frame["large_flow"] = frame["signed_large_100k_share"].fillna(0.0) + frame["signed_large_1m_share"].fillna(0.0)
    for lookback in (12, 24, 48):
        frame[f"prior_high_{lookback}"] = high.rolling(lookback, min_periods=lookback).max().shift(1)
        frame[f"prior_low_{lookback}"] = low.rolling(lookback, min_periods=lookback).min().shift(1)

    frame = pd.merge_asof(
        frame.sort_values("signal_time"),
        regimes,
        left_on="signal_time",
        right_on="available_time",
        direction="backward",
    ).drop(columns=["available_time"])
    return frame.sort_values("bar_open").reset_index(drop=True)


def load_breadth(timeframe: str) -> pd.DataFrame:
    path = next((WAREHOUSE / timeframe).glob(f"ALL_assets_common_research_base_{timeframe}_*.parquet"))
    frame = pd.read_parquet(path, columns=["asset", "bar_open_utc", "px_close"])
    frame["bar_open"] = pd.to_datetime(
        frame.pop("bar_open_utc"), utc=True, errors="coerce"
    ).astype("datetime64[ns, UTC]")
    frame["px_close"] = pd.to_numeric(frame["px_close"], errors="coerce")
    frame = frame.sort_values(["asset", "bar_open"])
    frame["breadth_ret_24"] = frame.groupby("asset")["px_close"].pct_change(24)
    summary = frame.groupby("bar_open", as_index=False).agg(
        breadth_up_24=("breadth_ret_24", lambda values: int((values > 0.0).sum())),
        breadth_down_24=("breadth_ret_24", lambda values: int((values < 0.0).sum())),
        breadth_total=("breadth_ret_24", lambda values: int(values.notna().sum())),
        market_mean_24=("breadth_ret_24", "mean"),
    )
    return summary


def split_mask(frame: pd.DataFrame, split: str) -> np.ndarray:
    time = frame["signal_time"]
    if split == "train":
        return ((time >= TRAIN_START) & (time < TRAIN_END)).to_numpy()
    if split == "validation":
        return ((time >= TRAIN_END) & (time < VALIDATION_END)).to_numpy()
    if split == "frozen":
        return ((time >= VALIDATION_END) & (time < FROZEN_END)).to_numpy()
    raise ValueError(split)


def candidate_metrics(
    frame: pd.DataFrame,
    indices: np.ndarray,
    direction: str,
    hold_bars: int,
    split: str,
    cost_bps: float,
) -> dict[str, Any]:
    in_split = split_mask(frame, split)
    selected = indices[in_split[indices]]
    entry = frame["px_open"].shift(-1).to_numpy(dtype=float)
    exit_price = frame["px_open"].shift(-(hold_bars + 1)).to_numpy(dtype=float)
    gross = exit_price[selected] / entry[selected] - 1.0
    sign = 1.0 if direction == "LONG" else -1.0
    returns = sign * gross - cost_bps / 10000.0
    bounds = {
        "train": (TRAIN_START, TRAIN_END),
        "validation": (TRAIN_END, VALIDATION_END),
        "frozen": (VALIDATION_END, FROZEN_END),
    }[split]
    return metrics_from_returns(returns, period_days(*bounds))


def masks_for_family(frame: pd.DataFrame, direction: str) -> Iterable[tuple[str, dict[str, Any], np.ndarray]]:
    sign = 1.0 if direction == "LONG" else -1.0
    close = frame["px_close"]
    ret1 = frame["ret_1"] * sign
    volume_z = frame["quote_volume_prior_z_20"]
    flow = frame["flow"] * sign
    regime = frame["regime"].fillna("TRANSITION")
    breadth = frame["breadth_up_24"] if direction == "LONG" else frame["breadth_down_24"]
    market = frame["market_mean_24"] * sign
    trend_aligned = (frame["ret_72"] * sign > 0.0) & (
        (close > frame["ema_72"]) if direction == "LONG" else (close < frame["ema_72"])
    )
    regime_aligned = ~regime.isin(["BEAR"] if direction == "LONG" else ["BULL", "ATH"])

    for lookback in (12, 24, 48):
        boundary = frame[f"prior_high_{lookback}"] if direction == "LONG" else frame[f"prior_low_{lookback}"]
        for buffer in (0.0, 0.0015):
            crossed = close >= boundary * (1.0 + buffer) if direction == "LONG" else close <= boundary * (1.0 - buffer)
            for vol_min in (-0.5, 0.5):
                params = {"lookback": lookback, "buffer": buffer, "vol_min": vol_min}
                mask = crossed & trend_aligned & regime_aligned & (volume_z >= vol_min) & (breadth >= 2) & (market > 0.0)
                yield "breakout", params, mask.to_numpy(dtype=bool)

    for lookback in (6, 12, 24):
        standardized = frame[f"ret_{lookback}"] * sign / (frame["rv_24"] * math.sqrt(lookback))
        for threshold in (0.75, 1.25):
            for vol_min in (-0.5, 0.5):
                params = {"lookback": lookback, "z_threshold": threshold, "vol_min": vol_min}
                mask = (standardized >= threshold) & trend_aligned & regime_aligned & (volume_z >= vol_min) & (breadth >= 2)
                yield "adaptive_momentum", params, mask.to_numpy(dtype=bool)

    sideway = regime.isin(["SIDEWAY", "TRANSITION"])
    close_z = frame["close_z_20"] * sign
    for z_threshold in (1.5, 2.0):
        params = {"z_threshold": z_threshold, "regime": "sideway_transition"}
        mask = (close_z <= -z_threshold) & (ret1 > 0.0) & (flow > -0.05) & sideway
        yield "mean_reversion", params, mask.to_numpy(dtype=bool)

    for atr_touch in (0.5, 1.0):
        if direction == "LONG":
            touched = frame["px_low"] <= frame["ema_20"] * (1.0 + frame["atr_pct_14"] * atr_touch)
            reclaimed = close > frame["ema_20"]
        else:
            touched = frame["px_high"] >= frame["ema_20"] * (1.0 - frame["atr_pct_14"] * atr_touch)
            reclaimed = close < frame["ema_20"]
        params = {"atr_touch": atr_touch, "flow_min": 0.0}
        mask = touched & reclaimed & trend_aligned & (ret1 > 0.0) & (flow > 0.0)
        yield "pullback_reclaim", params, mask.to_numpy(dtype=bool)

    shock_strength = ret1 / frame["rv_24"]
    for sigma in (2.0, 3.0):
        params = {"sigma": sigma, "mode": "continuation"}
        mask = (shock_strength >= sigma) & (volume_z >= 1.0) & (breadth >= 2) & (market > 0.0)
        yield "shock_continuation", params, mask.to_numpy(dtype=bool)

        adverse_shock = shock_strength <= -sigma
        wick = frame["lower_wick"] if direction == "LONG" else frame["upper_wick"]
        params = {"sigma": sigma, "mode": "reversal", "wick_body_ratio": 1.5}
        mask = adverse_shock & (wick >= frame["body_abs"] * 1.5) & (volume_z >= 1.0) & (flow > -0.05)
        yield "shock_reversal_proxy", params, mask.to_numpy(dtype=bool)

    available = frame["full_derivatives_state_available"].fillna(0).astype(bool)
    oi = frame["drv_open_interest_pct_change_24h"]
    funding = frame["drv_funding_sum_trailing_24h"] * sign
    basis_change = frame["drv_basis_change_1"] * sign
    for oi_min in (0.01, 0.03):
        params = {"oi_change_24h_min": oi_min, "funding_abs_max": 0.003}
        mask = available & trend_aligned & (oi >= oi_min) & (funding <= 0.003) & (basis_change > 0.0) & (flow > 0.0)
        yield "derivatives_squeeze", params, mask.to_numpy(dtype=bool)

    for flow_min in (0.05, 0.15):
        for vol_min in (0.0, 1.0):
            params = {"flow_min": flow_min, "vol_min": vol_min, "momentum_12_min": 0.0}
            mask = (flow >= flow_min) & (volume_z >= vol_min) & (frame["ret_12"] * sign > 0.0) & regime_aligned
            yield "taker_flow", params, mask.to_numpy(dtype=bool)


def evaluate_cell(frame: pd.DataFrame, asset: str, timeframe: str) -> list[CandidateResult]:
    results: list[CandidateResult] = []
    valid_base = (
        frame["px_open"].shift(-1).notna()
        & frame["rv_24"].notna()
        & frame["breadth_total"].ge(2)
        & ~frame["signal_time"].isna()
    ).to_numpy()
    for direction in ("LONG", "SHORT"):
        for family, params, raw_mask in masks_for_family(frame, direction):
            for hold in HOLD_GRID[timeframe]:
                exit_valid = frame["px_open"].shift(-(hold + 1)).notna().to_numpy()
                mask = raw_mask & valid_base & exit_valid
                indices = non_overlapping_indices(mask, hold)
                train = candidate_metrics(frame, indices, direction, hold, "train", BASE_COST_BPS)
                validation = candidate_metrics(frame, indices, direction, hold, "validation", BASE_COST_BPS)
                validation_stress = candidate_metrics(frame, indices, direction, hold, "validation", STRESS_COST_BPS)
                minimum = MIN_VALIDATION_TRADES[timeframe]
                selection_pass = bool(
                    train["trades"] >= minimum
                    and validation["trades"] >= minimum
                    and train["profit_factor"] >= 1.03
                    and validation["profit_factor"] >= 1.15
                    and validation_stress["profit_factor"] >= 1.02
                    and train["avg_net_bps"] > 0.0
                    and validation["avg_net_bps"] > 0.0
                    and validation["max_drawdown_pct"] >= -25.0
                )
                min_pf = min(train["profit_factor"], validation["profit_factor"], validation_stress["profit_factor"])
                target = FREQUENCY_TARGET_TPW[timeframe]
                frequency_ratio = min(validation["trades_per_week"] / target, 1.5) if target else 1.0
                score = (
                    min(min_pf, 4.0) * 30.0
                    + min(train["win_rate"], validation["win_rate"]) * 35.0
                    + min(validation["sharpe"], 5.0) * 4.0
                    + frequency_ratio * 8.0
                    + validation_stress["avg_net_bps"] / 10.0
                    + validation["max_drawdown_pct"] / 10.0
                )
                results.append(
                    CandidateResult(
                        candidate_id=candidate_id(asset, timeframe, direction, family, hold, params),
                        asset=asset,
                        timeframe=timeframe,
                        direction=direction,
                        family=family,
                        hold_bars=hold,
                        params=params,
                        selected_indices=indices,
                        train=train,
                        validation=validation,
                        validation_stress=validation_stress,
                        selection_score=float(score),
                        selection_pass=selection_pass,
                    )
                )
    return results


def result_row(result: CandidateResult, frozen: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "candidate_id": result.candidate_id,
        "asset": result.asset,
        "timeframe": result.timeframe,
        "direction": result.direction,
        "family": result.family,
        "hold_bars": result.hold_bars,
        "params_json": json.dumps(result.params, sort_keys=True),
        "selection_score": result.selection_score,
        "selection_pass": result.selection_pass,
    }
    for prefix, block in (("train", result.train), ("validation", result.validation), ("validation_stress", result.validation_stress)):
        for key, value in block.items():
            row[f"{prefix}_{key}"] = value
    if frozen is not None:
        for key, value in frozen.items():
            row[f"frozen_{key}"] = value
    return row


def selected_trade_rows(frame: pd.DataFrame, result: CandidateResult) -> pd.DataFrame:
    indices = result.selected_indices
    entry = frame["px_open"].shift(-1)
    exit_price = frame["px_open"].shift(-(result.hold_bars + 1))
    sign = 1.0 if result.direction == "LONG" else -1.0
    rows = pd.DataFrame(
        {
            "candidate_id": result.candidate_id,
            "asset": result.asset,
            "timeframe": result.timeframe,
            "direction": result.direction,
            "family": result.family,
            "signal_time": frame.loc[indices, "signal_time"].to_numpy(),
            "entry_time": (frame.loc[indices, "signal_time"] + pd.to_timedelta(TF_MINUTES[result.timeframe], unit="m")).to_numpy(),
            "exit_time": (frame.loc[indices, "signal_time"] + pd.to_timedelta(TF_MINUTES[result.timeframe] * (result.hold_bars + 1), unit="m")).to_numpy(),
            "gross_return": sign * (exit_price.iloc[indices].to_numpy() / entry.iloc[indices].to_numpy() - 1.0),
            "regime": frame.loc[indices, "regime"].to_numpy(),
            "selection_score": result.selection_score,
        }
    )
    return rows.dropna(subset=["signal_time", "entry_time", "exit_time", "gross_return"])


def portfolio_select(raw: pd.DataFrame) -> pd.DataFrame:
    work = raw.sort_values(["entry_time", "selection_score"], ascending=[True, False]).drop_duplicates(
        ["entry_time", "asset"], keep="first"
    )
    selected: list[int] = []
    active: list[tuple[pd.Timestamp, str]] = []
    for entry_time, group in work.groupby("entry_time", sort=True):
        active = [(exit_time, asset) for exit_time, asset in active if exit_time > entry_time]
        active_assets = {asset for _, asset in active}
        for index, row in group.sort_values("selection_score", ascending=False).iterrows():
            if row["asset"] in active_assets or len(active) >= MAX_PORTFOLIO_POSITIONS:
                continue
            selected.append(index)
            active.append((row["exit_time"], row["asset"]))
            active_assets.add(row["asset"])
    return work.loc[selected].sort_values("exit_time").reset_index(drop=True)


def portfolio_metrics(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, cost_bps: float) -> dict[str, Any]:
    subset = frame.loc[(frame["entry_time"] >= start) & (frame["entry_time"] < end)].copy()
    returns = RISK_FRACTION * (subset["gross_return"].to_numpy(dtype=float) - cost_bps / 10000.0)
    return metrics_from_returns(returns, period_days(start, end))


def markdown_table(frame: pd.DataFrame, columns: list[str], limit: int = 100) -> str:
    if frame.empty:
        return "_No rows._\n"
    display = frame.loc[:, columns].head(limit).copy()
    for column in display.columns:
        if pd.api.types.is_float_dtype(display[column]):
            display[column] = display[column].map(lambda value: f"{value:.4f}" if pd.notna(value) else "")
    header = "| " + " | ".join(display.columns) + " |"
    separator = "| " + " | ".join(["---"] * len(display.columns)) + " |"
    rows = [
        "| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |"
        for row in display.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows]) + "\n"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    regimes = load_btc_regimes()
    all_result_rows: list[dict[str, Any]] = []
    selected_results: list[CandidateResult] = []
    selected_trades: list[pd.DataFrame] = []
    cell_frames: dict[tuple[str, str], pd.DataFrame] = {}

    for timeframe in TIMEFRAMES:
        breadth = load_breadth(timeframe)
        for asset in ASSETS:
            frame = load_cell(asset, timeframe, regimes).merge(breadth, on="bar_open", how="left")
            cell_frames[(asset, timeframe)] = frame
            results = evaluate_cell(frame, asset, timeframe)
            passing = [result for result in results if result.selection_pass]
            chosen_by_direction: list[CandidateResult] = []
            for direction in ("LONG", "SHORT"):
                eligible = [result for result in passing if result.direction == direction]
                if eligible:
                    chosen_by_direction.append(max(eligible, key=lambda result: result.selection_score))
            for result in results:
                all_result_rows.append(result_row(result))
            for result in chosen_by_direction:
                frozen = candidate_metrics(
                    frame,
                    result.selected_indices,
                    result.direction,
                    result.hold_bars,
                    "frozen",
                    BASE_COST_BPS,
                )
                all_result_rows.append({**result_row(result, frozen), "row_type": "SELECTED_WITH_FROZEN"})
                selected_results.append(result)
                selected_trades.append(selected_trade_rows(frame, result))
            print(
                f"cell={asset} {timeframe} candidates={len(results)} passing={len(passing)} "
                f"selected={len(chosen_by_direction)}",
                flush=True,
            )

    candidate_table = pd.DataFrame(all_result_rows)
    chosen_rows: list[dict[str, Any]] = []
    for result in selected_results:
        frame = cell_frames[(result.asset, result.timeframe)]
        frozen = candidate_metrics(frame, result.selected_indices, result.direction, result.hold_bars, "frozen", BASE_COST_BPS)
        chosen_rows.append(result_row(result, frozen))
    chosen = pd.DataFrame(chosen_rows)

    raw_trades = pd.concat(selected_trades, ignore_index=True) if selected_trades else pd.DataFrame()
    portfolio = portfolio_select(raw_trades) if not raw_trades.empty else raw_trades
    portfolio_rows: list[dict[str, Any]] = []
    for split, start, end in (
        ("train", TRAIN_START, TRAIN_END),
        ("validation", TRAIN_END, VALIDATION_END),
        ("frozen", VALIDATION_END, FROZEN_END),
    ):
        for cost in (BASE_COST_BPS, STRESS_COST_BPS):
            portfolio_rows.append(
                {"split": split, "cost_bps": cost, **portfolio_metrics(portfolio, start, end, cost)}
            )
    portfolio_summary = pd.DataFrame(portfolio_rows)

    frozen_portfolio = portfolio.loc[
        (portfolio["entry_time"] >= VALIDATION_END) & (portfolio["entry_time"] < FROZEN_END)
    ].copy() if not portfolio.empty else portfolio
    cell_summary_rows: list[dict[str, Any]] = []
    if not frozen_portfolio.empty:
        for keys, group in frozen_portfolio.groupby(["asset", "timeframe", "direction"]):
            returns = RISK_FRACTION * (group["gross_return"].to_numpy(dtype=float) - BASE_COST_BPS / 10000.0)
            cell_summary_rows.append(
                {
                    "asset": keys[0],
                    "timeframe": keys[1],
                    "direction": keys[2],
                    **metrics_from_returns(returns, period_days(VALIDATION_END, FROZEN_END)),
                }
            )
    cell_summary = pd.DataFrame(cell_summary_rows)

    regime_summary_rows: list[dict[str, Any]] = []
    if not frozen_portfolio.empty:
        for regime, group in frozen_portfolio.groupby("regime"):
            returns = RISK_FRACTION * (group["gross_return"].to_numpy(dtype=float) - BASE_COST_BPS / 10000.0)
            regime_summary_rows.append(
                {"regime": regime, **metrics_from_returns(returns, period_days(VALIDATION_END, FROZEN_END))}
            )
    regime_summary = pd.DataFrame(regime_summary_rows)

    frozen_base = portfolio_summary.loc[
        portfolio_summary["split"].eq("frozen") & portfolio_summary["cost_bps"].eq(BASE_COST_BPS)
    ]
    frozen_stress = portfolio_summary.loc[
        portfolio_summary["split"].eq("frozen") & portfolio_summary["cost_bps"].eq(STRESS_COST_BPS)
    ]
    frozen_base_row = frozen_base.iloc[0].to_dict() if not frozen_base.empty else {}
    frozen_stress_row = frozen_stress.iloc[0].to_dict() if not frozen_stress.empty else {}
    all_cells = {(asset, timeframe, direction) for asset in ASSETS for timeframe in TIMEFRAMES for direction in ("LONG", "SHORT")}
    covered_cells = set(zip(chosen.get("asset", []), chosen.get("timeframe", []), chosen.get("direction", [])))
    promotion_gate = bool(
        frozen_base_row
        and frozen_base_row["profit_factor"] >= 1.60
        and frozen_base_row["win_rate"] >= 0.55
        and frozen_base_row["sharpe"] >= 2.0
        and frozen_base_row["max_drawdown_pct"] >= -15.0
        and frozen_stress_row.get("profit_factor", 0.0) >= 1.20
    )
    verdict = {
        "stage": "R29C_REGIME_ENSEMBLE_SEARCH",
        "candidates_evaluated": int(len(candidate_table.loc[candidate_table.get("row_type").isna()])) if "row_type" in candidate_table else int(len(candidate_table)),
        "selected_candidate_count": int(len(chosen)),
        "covered_trade_cells": int(len(covered_cells)),
        "total_trade_cells": int(len(all_cells)),
        "missing_trade_cells": sorted("|".join(cell) for cell in all_cells - covered_cells),
        "frozen_metrics_12bps": frozen_base_row,
        "frozen_metrics_20bps": frozen_stress_row,
        "historical_true_l2_or_liquidation_used": False,
        "liquidity_policy": "FORWARD_CONFIRMATION_ONLY_UNTIL_TRUE_ARCHIVE_EXISTS",
        "promotion_quality_gate_pass": promotion_gate,
        "production_promotion_allowed": False,
        "decision": (
            "PASS_TO_R29D_PARITY_AND_FORWARD_CHALLENGER_NO_DEPLOY"
            if promotion_gate
            else "CONTINUE_RESEARCH_NO_DEPLOY"
        ),
    }

    candidate_table.to_csv(OUTPUT_DIR / "R29C_CANDIDATE_RESULTS.csv", index=False)
    chosen.to_csv(OUTPUT_DIR / "R29C_SELECTED_CANDIDATES.csv", index=False)
    raw_trades.to_csv(OUTPUT_DIR / "R29C_SELECTED_RAW_TRADES.csv", index=False)
    portfolio.to_csv(OUTPUT_DIR / "R29C_PORTFOLIO_TRADES.csv", index=False)
    portfolio_summary.to_csv(OUTPUT_DIR / "R29C_PORTFOLIO_METRICS.csv", index=False)
    cell_summary.to_csv(OUTPUT_DIR / "R29C_FROZEN_CELL_METRICS.csv", index=False)
    regime_summary.to_csv(OUTPUT_DIR / "R29C_FROZEN_REGIME_METRICS.csv", index=False)
    (OUTPUT_DIR / "R29C_VERDICT.json").write_text(
        json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )

    report = [
        "# R29C Regime Ensemble Search",
        "",
        "## Decision",
        "",
        f"**{verdict['decision']}**",
        "",
        "Candidate ranking used only 2020-2022 train and 2023-2024 validation. The "
        "2025-2026 frozen window was evaluated only after one LONG and one SHORT candidate "
        "per asset/timeframe had been selected.",
        "",
        "True historical liquidity and liquidation heatmaps were not used because they are "
        "absent from the warehouse. They remain forward-only confirmation layers.",
        "",
        "## Portfolio metrics",
        "",
        markdown_table(
            portfolio_summary,
            ["split", "cost_bps", "trades", "trades_per_week", "win_rate", "profit_factor", "sharpe", "max_drawdown_pct", "avg_net_bps"],
        ),
        "## Selected candidates",
        "",
        markdown_table(
            chosen,
            ["asset", "timeframe", "direction", "family", "hold_bars", "validation_trades_per_week", "validation_win_rate", "validation_profit_factor", "validation_stress_profit_factor", "frozen_win_rate", "frozen_profit_factor", "frozen_max_drawdown_pct"],
        ),
        "## Frozen cell metrics",
        "",
        markdown_table(
            cell_summary,
            ["asset", "timeframe", "direction", "trades", "trades_per_week", "win_rate", "profit_factor", "sharpe", "max_drawdown_pct"],
        ),
        "## Frozen regime metrics",
        "",
        markdown_table(
            regime_summary,
            ["regime", "trades", "trades_per_week", "win_rate", "profit_factor", "sharpe", "max_drawdown_pct"],
        ),
        "",
    ]
    (OUTPUT_DIR / "R29C_REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(json_safe(verdict), indent=2, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
