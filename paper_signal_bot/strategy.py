from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Any


STRATEGY_ID = "R22C_REGIME_SLEEVE_4H_PAPER_OBSERVATION"
INTERVAL_MS = {
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}
SYMBOL_BY_ASSET = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
}
ASSET_BY_SYMBOL = {symbol: asset for asset, symbol in SYMBOL_BY_ASSET.items()}
R22C_MARKETS = tuple((symbol, "4h") for symbol in SYMBOL_BY_ASSET.values())
MIN_HISTORY_BARS = 72


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    symbol: str
    asset: str
    timeframe: str
    direction: str
    family: str
    hold_bars: int
    params: dict[str, Any]
    selection_score: float
    sleeve_id: str
    max_positions: int
    risk_fraction: float


def candidate(
    *,
    candidate_id: str,
    asset: str,
    direction: str,
    family: str,
    hold_bars: int,
    params: dict[str, Any],
    selection_score: float,
    sleeve_id: str,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        symbol=SYMBOL_BY_ASSET[asset],
        asset=asset,
        timeframe="4h",
        direction=direction,
        family=family,
        hold_bars=hold_bars,
        params=params,
        selection_score=selection_score,
        sleeve_id=sleeve_id,
        max_positions=4,
        risk_fraction=0.25,
    )


CANDIDATES: tuple[Candidate, ...] = (
    candidate(
        candidate_id="breadth_breakout_BTC_4h_LONG_h12_2e275a7ccb",
        asset="BTC",
        direction="LONG",
        family="breadth_breakout",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 2, "buffer": 0.0015, "lb": 6, "market_min": 0.0026666666666666666, "regime_lb": 6, "volz_min": -0.75},
        selection_score=108.039256538311,
        sleeve_id="4h_LONG_mp4_rf0p25",
    ),
    candidate(
        candidate_id="breadth_momentum_ETH_4h_LONG_h12_0023b0aa49",
        asset="ETH",
        direction="LONG",
        family="breadth_momentum",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 3, "leader_required": False, "market_min": 0.0026666666666666666, "regime_lb": 6, "ret1_min": 0.0, "signal_lb": 3, "signal_min": 0.004, "volz_min": -0.75},
        selection_score=109.56770392918962,
        sleeve_id="4h_LONG_mp4_rf0p25",
    ),
    candidate(
        candidate_id="breadth_breakout_SOL_4h_LONG_h12_2cf0accdcc",
        asset="SOL",
        direction="LONG",
        family="breadth_breakout",
        hold_bars=12,
        params={"breadth_min": 0.018, "breadth_n": 3, "buffer": 0.0, "lb": 6, "market_min": 0.005999999999999999, "regime_lb": 6, "volz_min": -0.75},
        selection_score=101.64559277014436,
        sleeve_id="4h_LONG_mp4_rf0p25",
    ),
    candidate(
        candidate_id="breadth_momentum_BNB_4h_LONG_h12_16f12c5aba",
        asset="BNB",
        direction="LONG",
        family="breadth_momentum",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 3, "leader_required": False, "market_min": 0.0026666666666666666, "regime_lb": 6, "ret1_min": 0.003, "signal_lb": 3, "signal_min": 0.004, "volz_min": -0.75},
        selection_score=111.95003078299469,
        sleeve_id="4h_LONG_mp4_rf0p25",
    ),
    candidate(
        candidate_id="breadth_breakout_BTC_4h_SHORT_h6_23bbde5b98",
        asset="BTC",
        direction="SHORT",
        family="breadth_breakout",
        hold_bars=6,
        params={"breadth_min": 0.008, "breadth_n": 2, "buffer": 0.0015, "lb": 24, "market_min": 0.0026666666666666666, "regime_lb": 24, "volz_min": -0.75},
        selection_score=61.0,
        sleeve_id="4h_SHORT_mp4_rf0p25",
    ),
    candidate(
        candidate_id="breadth_momentum_ETH_4h_SHORT_h12_94d12c6fac",
        asset="ETH",
        direction="SHORT",
        family="breadth_momentum",
        hold_bars=12,
        params={"breadth_min": 0.018, "breadth_n": 2, "leader_required": False, "market_min": 0.005999999999999999, "regime_lb": 12, "ret1_min": 0.003, "signal_lb": 6, "signal_min": 0.018, "volz_min": 0.0},
        selection_score=39.36631786310146,
        sleeve_id="4h_SHORT_mp4_rf0p25",
    ),
    candidate(
        candidate_id="breadth_ema_stack_SOL_4h_SHORT_h12_25effefd8e",
        asset="SOL",
        direction="SHORT",
        family="breadth_ema_stack",
        hold_bars=12,
        params={"breadth_min": 0.018, "breadth_n": 2, "leader_required": False, "market_min": 0.005999999999999999, "regime_lb": 12, "slope_min": 0.003},
        selection_score=62.34603737930004,
        sleeve_id="4h_SHORT_mp4_rf0p25",
    ),
    candidate(
        candidate_id="breadth_breakout_BNB_4h_SHORT_h6_d617960bc9",
        asset="BNB",
        direction="SHORT",
        family="breadth_breakout",
        hold_bars=6,
        params={"breadth_min": 0.018, "breadth_n": 3, "buffer": 0.0, "lb": 24, "market_min": 0.005999999999999999, "regime_lb": 24, "volz_min": -0.75},
        selection_score=30.786670056883686,
        sleeve_id="4h_SHORT_mp4_rf0p25",
    ),
)


def utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def ms_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_kline(row: list[Any]) -> dict[str, Any]:
    return {
        "open_time": safe_int(row[0]),
        "open": safe_float(row[1]),
        "high": safe_float(row[2]),
        "low": safe_float(row[3]),
        "close": safe_float(row[4]),
        "volume": safe_float(row[5]),
        "close_time": safe_int(row[6]),
        "quote_volume": safe_float(row[7]),
        "number_of_trades": safe_int(row[8]),
        "taker_buy_base_volume": safe_float(row[9]),
        "taker_buy_quote_volume": safe_float(row[10]),
    }


def parse_premium_kline(row: list[Any]) -> dict[str, Any]:
    return {
        "open_time": safe_int(row[0]),
        "premium_open": safe_float(row[1]),
        "premium_high": safe_float(row[2]),
        "premium_low": safe_float(row[3]),
        "premium_close": safe_float(row[4]),
        "close_time": safe_int(row[6]),
    }


def closed_rows(rows: list[list[Any]], interval: str, now_ms: int | None = None, premium: bool = False) -> list[dict[str, Any]]:
    now = utc_now_ms() if now_ms is None else now_ms
    interval_ms = INTERVAL_MS[interval]
    parser = parse_premium_kline if premium else parse_kline
    parsed = [parser(row) for row in rows]
    parsed.sort(key=lambda item: item["open_time"])
    return [row for row in parsed if row["open_time"] + interval_ms <= now]


def prior_zscore(values: list[float], index: int, window: int) -> float:
    if index < window:
        return 0.0
    prior = values[index - window : index]
    mean = fmean(prior)
    std = pstdev(prior)
    if std <= 0.0:
        return 0.0
    return (values[index] - mean) / std


def realized_volatility(values: list[float], index: int, window: int = 24, min_periods: int = 12) -> float:
    start = max(1, index - window + 1)
    returns = [
        math.log(values[i] / values[i - 1])
        for i in range(start, index + 1)
        if values[i] > 0.0 and values[i - 1] > 0.0
    ]
    if len(returns) < min_periods:
        return 0.0
    return pstdev(returns)


def ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out: list[float] = []
    current = values[0]
    for value in values:
        current = alpha * value + (1.0 - alpha) * current
        out.append(current)
    return out


def directional(value: float, direction: str) -> float:
    return value if direction == "LONG" else -value


def trend(closes: list[float], index: int, lookback: int) -> float | None:
    if index < lookback or closes[index - lookback] <= 0.0:
        return None
    return closes[index] / closes[index - lookback] - 1.0


def prior_high_low(rows: list[dict[str, Any]], index: int, lookback: int) -> tuple[float, float] | None:
    if index < lookback:
        return None
    prior = rows[index - lookback : index]
    return max(row["high"] for row in prior), min(row["low"] for row in prior)


def candidate_groups() -> dict[tuple[str, str], list[Candidate]]:
    groups: dict[tuple[str, str], list[Candidate]] = {}
    for candidate_item in CANDIDATES:
        groups.setdefault((candidate_item.symbol, candidate_item.timeframe), []).append(candidate_item)
    return groups


def latest_by_open(rows: list[dict[str, Any]], open_time: int) -> tuple[int, dict[str, Any]] | None:
    for index in range(len(rows) - 1, -1, -1):
        if rows[index]["open_time"] <= open_time:
            return index, rows[index]
    return None


def market_breadth(
    *,
    market_klines_by_symbol: dict[str, list[list[Any]]],
    timeframe: str,
    open_time: int,
    now_ms: int | None,
    direction: str,
    regime_lb: int,
    breadth_min: float,
) -> dict[str, Any]:
    directed: dict[str, float] = {}
    for symbol, rows in market_klines_by_symbol.items():
        asset = ASSET_BY_SYMBOL.get(symbol)
        if asset is None:
            continue
        closed = closed_rows(rows, timeframe, now_ms=now_ms)
        located = latest_by_open(closed, open_time)
        if located is None:
            continue
        index, _row = located
        closes = [item["close"] for item in closed]
        value = trend(closes, index, regime_lb)
        if value is None:
            continue
        directed[asset] = directional(value, direction)
    values = list(directed.values())
    count = sum(1 for value in values if value >= breadth_min)
    mean = fmean(values) if values else 0.0
    return {
        "market_breadth_count": count,
        "market_breadth_assets": len(values),
        "market_directional_mean": mean,
        "market_asset_trends": directed,
    }


def trigger_candidate(
    *,
    candidate_item: Candidate,
    closed: list[dict[str, Any]],
    index: int,
    market_klines_by_symbol: dict[str, list[list[Any]]],
    now_ms: int | None,
) -> tuple[bool, dict[str, Any]]:
    latest = closed[index]
    closes = [row["close"] for row in closed]
    quote_volumes = [row["quote_volume"] for row in closed]
    params = candidate_item.params
    regime_lb = int(params["regime_lb"])
    breadth_min = float(params["breadth_min"])
    market = market_breadth(
        market_klines_by_symbol=market_klines_by_symbol,
        timeframe=candidate_item.timeframe,
        open_time=latest["open_time"],
        now_ms=now_ms,
        direction=candidate_item.direction,
        regime_lb=regime_lb,
        breadth_min=breadth_min,
    )
    volz = prior_zscore(quote_volumes, index, 20)
    ret1 = directional(closes[index] / closes[index - 1] - 1.0, candidate_item.direction) if index >= 1 and closes[index - 1] > 0.0 else 0.0
    market_ok = (
        market["market_breadth_count"] >= int(params["breadth_n"])
        and market["market_directional_mean"] >= float(params["market_min"])
    )
    features: dict[str, Any] = {
        **market,
        "close": latest["close"],
        "quote_volume_prior_z_20": volz,
        "ret_1_directional": ret1,
        "breadth_min": breadth_min,
        "breadth_n": int(params["breadth_n"]),
        "market_min": float(params["market_min"]),
        "regime_lb": regime_lb,
        "risk_fraction": candidate_item.risk_fraction,
        "max_positions": candidate_item.max_positions,
        "sleeve_id": candidate_item.sleeve_id,
    }
    if not market_ok:
        return False, features

    if candidate_item.family == "breadth_momentum":
        signal_lb = int(params["signal_lb"])
        signal_trend_raw = trend(closes, index, signal_lb)
        signal_trend = directional(signal_trend_raw or 0.0, candidate_item.direction)
        trigger = (
            signal_trend >= float(params["signal_min"])
            and ret1 >= float(params["ret1_min"])
            and volz >= float(params["volz_min"])
        )
        features.update(
            {
                "family": candidate_item.family,
                "signal_lb": signal_lb,
                "signal_trend_directional": signal_trend,
                "signal_min": float(params["signal_min"]),
                "ret1_min": float(params["ret1_min"]),
                "volz_min": float(params["volz_min"]),
            }
        )
        return trigger, features

    if candidate_item.family == "breadth_breakout":
        lb = int(params["lb"])
        levels = prior_high_low(closed, index, lb)
        if levels is None:
            return False, features
        prior_high, prior_low = levels
        if candidate_item.direction == "LONG":
            breakout_level = prior_high * (1.0 + float(params["buffer"]))
            trigger = latest["close"] > breakout_level and volz >= float(params["volz_min"])
        else:
            breakout_level = prior_low * (1.0 - float(params["buffer"]))
            trigger = latest["close"] < breakout_level and volz >= float(params["volz_min"])
        features.update(
            {
                "family": candidate_item.family,
                "breakout_lb": lb,
                "prior_high": prior_high,
                "prior_low": prior_low,
                "breakout_level": breakout_level,
                "buffer": float(params["buffer"]),
                "volz_min": float(params["volz_min"]),
            }
        )
        return trigger, features

    if candidate_item.family == "breadth_ema_stack":
        ema_fast = ema(closes, 6)
        ema_mid = ema(closes, 12)
        ema_slow = ema(closes, 24)
        if index < 24 or index < 12 or ema_mid[index - 12] <= 0.0:
            return False, features
        if candidate_item.direction == "LONG":
            stack = ema_fast[index] > ema_mid[index] > ema_slow[index]
            slope = ema_mid[index] / ema_mid[index - 12] - 1.0
        else:
            stack = ema_fast[index] < ema_mid[index] < ema_slow[index]
            slope = -(ema_mid[index] / ema_mid[index - 12] - 1.0)
        trigger = stack and slope >= float(params["slope_min"])
        features.update(
            {
                "family": candidate_item.family,
                "ema_stack": stack,
                "ema_mid_slope_directional": slope,
                "slope_min": float(params["slope_min"]),
            }
        )
        return trigger, features

    return False, features


def evaluate_latest(
    *,
    symbol: str,
    timeframe: str,
    klines: list[list[Any]],
    premium_klines: list[list[Any]],
    derivatives_state_available: bool,
    market_klines_by_symbol: dict[str, list[list[Any]]] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    candidates = [candidate_item for candidate_item in CANDIDATES if candidate_item.symbol == symbol and candidate_item.timeframe == timeframe]
    if not candidates:
        return {"symbol": symbol, "timeframe": timeframe, "status": "NO_CANDIDATES", "signals": []}

    closed = closed_rows(klines, timeframe, now_ms=now_ms, premium=False)
    premium_closed = closed_rows(premium_klines, timeframe, now_ms=now_ms, premium=True) if premium_klines else []
    if len(closed) < MIN_HISTORY_BARS:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "INSUFFICIENT_HISTORY",
            "closed_bars": len(closed),
            "premium_closed_bars": len(premium_closed),
            "candidate_count": len(candidates),
            "signals": [],
        }

    latest = closed[-1]
    index = len(closed) - 1
    market_rows = market_klines_by_symbol or {symbol: klines}
    closes = [row["close"] for row in closed]
    realized_vol_24 = realized_volatility(closes, index, 24, 12)
    premium_z: float | None = None
    if len(premium_closed) >= 25:
        premium_values = [row["premium_close"] for row in premium_closed]
        premium_index = len(premium_closed) - 1
        premium_z = prior_zscore(premium_values, premium_index, 24)

    raw_signals: list[dict[str, Any]] = []
    raw_feature_snapshots: list[dict[str, Any]] = []
    for candidate_item in candidates:
        trigger, features = trigger_candidate(
            candidate_item=candidate_item,
            closed=closed,
            index=index,
            market_klines_by_symbol=market_rows,
            now_ms=now_ms,
        )
        features["premium_close_prior_z_24"] = premium_z
        features["realized_vol_24"] = realized_vol_24
        features["full_derivatives_state_available"] = derivatives_state_available
        features["candidate_id"] = candidate_item.candidate_id
        raw_feature_snapshots.append(features)
        if not trigger:
            continue

        interval_ms = INTERVAL_MS[timeframe]
        entry_time = latest["open_time"] + interval_ms
        next_open = None
        for row in [parse_kline(raw) for raw in klines]:
            if row["open_time"] == entry_time:
                next_open = row["open"]
                break
        raw_signals.append(
            {
                "strategy_id": STRATEGY_ID,
                "candidate": asdict(candidate_item),
                "symbol": symbol,
                "asset": candidate_item.asset,
                "timeframe": timeframe,
                "side": candidate_item.direction,
                "signal_time_ms": latest["open_time"],
                "signal_time_utc": ms_to_iso(latest["open_time"]),
                "entry_time_ms": entry_time,
                "entry_time_utc": ms_to_iso(entry_time),
                "planned_exit_time_ms": entry_time + interval_ms * candidate_item.hold_bars,
                "planned_exit_time_utc": ms_to_iso(entry_time + interval_ms * candidate_item.hold_bars),
                "entry_model": "NEXT_OPEN",
                "entry_price": next_open,
                "paper_only": True,
                "risk_fraction": candidate_item.risk_fraction,
                "max_positions": candidate_item.max_positions,
                "sleeve_id": candidate_item.sleeve_id,
                "features": features,
            }
        )

    raw_signals.sort(key=lambda item: item["candidate"]["selection_score"], reverse=True)
    selected = raw_signals[:1]
    snapshot = raw_feature_snapshots[0] if raw_feature_snapshots else {}
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": "SIGNAL" if selected else "NO_SIGNAL",
        "closed_bars": len(closed),
        "premium_closed_bars": len(premium_closed),
        "candidate_count": len(candidates),
        "latest_closed_bar_utc": ms_to_iso(latest["open_time"]),
        "features": {
            "quote_volume_prior_z_20": snapshot.get("quote_volume_prior_z_20"),
            "market_breadth_count": snapshot.get("market_breadth_count"),
            "market_breadth_assets": snapshot.get("market_breadth_assets"),
            "market_directional_mean": snapshot.get("market_directional_mean"),
            "breadth_n": snapshot.get("breadth_n"),
            "breadth_min": snapshot.get("breadth_min"),
            "premium_close_prior_z_24": premium_z,
            "realized_vol_24": realized_vol_24,
            "full_derivatives_state_available": derivatives_state_available,
        },
        "raw_signal_count": len(raw_signals),
        "signals": selected,
    }
