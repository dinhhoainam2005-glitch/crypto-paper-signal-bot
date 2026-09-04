from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Any


STRATEGY_ID = "R23B_QUALITY_MODE_R15C_R22A_PAPER_OBSERVATION"
PORTFOLIO_ID = "quality_expand_size_17_mp4_rf0p25_2092c4ac40"
PORTFOLIO_NAME = "R23B_QUALITY_MODE"
PORTFOLIO_METRICS = {
    "frozen_trades_per_week": 5.580589254766031,
    "frozen_profit_factor": 1.6301594151558279,
    "frozen_sharpe": 2.8967160803678764,
    "frozen_win_rate": 0.558695652173913,
    "frozen_max_drawdown_pct": -9.132704618939592,
    "validation_trades_per_week": 6.770491803278689,
    "validation_profit_factor": 1.6836047380401198,
    "validation_sharpe": 3.610803908352226,
    "validation_win_rate": 0.5480225988700564,
    "recent_trades_per_week": 4.6073245577930555,
    "recent_profit_factor": 8.683072308710456,
    "recent_win_rate": 0.7777777777777778,
}
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
    timeframe: str = "4h",
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
        timeframe=timeframe,
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
        candidate_id="breadth_momentum_BNB_4h_LONG_h12_16f12c5aba",
        asset="BNB",
        direction="LONG",
        family="breadth_momentum",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 3, "leader_required": False, "market_min": 0.0026666666666666666, "regime_lb": 6, "ret1_min": 0.003, "signal_lb": 3, "signal_min": 0.004, "volz_min": -0.75},
        selection_score=96.39932140563236,
        sleeve_id="r23b_quality_long",
    ),
    candidate(
        candidate_id="breadth_momentum_BNB_4h_LONG_h12_ad92d46d33",
        asset="BNB",
        direction="LONG",
        family="breadth_momentum",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 3, "leader_required": False, "market_min": 0.0026666666666666666, "regime_lb": 6, "ret1_min": 0.003, "signal_lb": 3, "signal_min": 0.004, "volz_min": 0.0},
        selection_score=96.39932140563236,
        sleeve_id="r23b_quality_long",
    ),
    candidate(
        candidate_id="breadth_momentum_ETH_4h_LONG_h12_0023b0aa49",
        asset="ETH",
        direction="LONG",
        family="breadth_momentum",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 3, "leader_required": False, "market_min": 0.0026666666666666666, "regime_lb": 6, "ret1_min": 0.0, "signal_lb": 3, "signal_min": 0.004, "volz_min": -0.75},
        selection_score=95.72881141514557,
        sleeve_id="r23b_quality_long",
    ),
    candidate(
        candidate_id="breadth_momentum_ETH_4h_LONG_h12_c9d65c1d56",
        asset="ETH",
        direction="LONG",
        family="breadth_momentum",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 3, "leader_required": False, "market_min": 0.0026666666666666666, "regime_lb": 6, "ret1_min": 0.003, "signal_lb": 3, "signal_min": 0.004, "volz_min": -0.75},
        selection_score=95.3596976814344,
        sleeve_id="r23b_quality_long",
    ),
    candidate(
        candidate_id="breadth_momentum_BTC_4h_LONG_h12_c0613ac7dc",
        asset="BTC",
        direction="LONG",
        family="breadth_momentum",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 2, "leader_required": False, "market_min": 0.0026666666666666666, "regime_lb": 6, "ret1_min": 0.003, "signal_lb": 3, "signal_min": 0.008, "volz_min": -0.75},
        selection_score=93.30012232382694,
        sleeve_id="r23b_quality_long",
    ),
    candidate(
        candidate_id="breadth_breakout_BNB_4h_LONG_h12_45899086a2",
        asset="BNB",
        direction="LONG",
        family="breadth_breakout",
        hold_bars=12,
        params={"breadth_min": 0.008, "breadth_n": 2, "buffer": 0.0, "lb": 6, "market_min": 0.0026666666666666666, "regime_lb": 6, "volz_min": -0.75},
        selection_score=93.18093096228357,
        sleeve_id="r23b_quality_long",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_BTC_1h_LONG_h12_c933124895",
        asset="BTC",
        timeframe="1h",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=12,
        params={"asset_regime_min": 0.006, "breadth_min": 0.006, "breadth_n": 2, "market_min": 0.002, "pullback_min": 0.018, "regime_lb": 24, "signal_lb": 6},
        selection_score=78.8001425145812,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_BTC_1h_LONG_h12_5ca3f75ebb",
        asset="BTC",
        timeframe="1h",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=12,
        params={"asset_regime_min": 0.012, "breadth_min": 0.012, "breadth_n": 2, "market_min": 0.004, "pullback_min": 0.018, "regime_lb": 24, "signal_lb": 6},
        selection_score=78.8001425145812,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_BTC_1h_LONG_h12_7f16f9c924",
        asset="BTC",
        timeframe="1h",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=12,
        params={"asset_regime_min": 0.006, "breadth_min": 0.006, "breadth_n": 3, "market_min": 0.002, "pullback_min": 0.018, "regime_lb": 24, "signal_lb": 6},
        selection_score=77.56937328381196,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_BTC_1h_LONG_h12_b9ff12b4cf",
        asset="BTC",
        timeframe="1h",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=12,
        params={"asset_regime_min": 0.012, "breadth_min": 0.012, "breadth_n": 3, "market_min": 0.004, "pullback_min": 0.018, "regime_lb": 24, "signal_lb": 6},
        selection_score=77.56937328381196,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_SOL_4h_LONG_h6_58330d2582",
        asset="SOL",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=6,
        params={"asset_regime_min": 0.008, "breadth_min": 0.008, "breadth_n": 3, "market_min": 0.0026666666666666666, "pullback_min": 0.025, "regime_lb": 12, "signal_lb": 6},
        selection_score=56.5051698798565,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_SOL_4h_LONG_h12_4481e3a8c2",
        asset="SOL",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=12,
        params={"asset_regime_min": 0.008, "breadth_min": 0.008, "breadth_n": 2, "market_min": 0.0026666666666666666, "pullback_min": 0.055, "regime_lb": 12, "signal_lb": 6},
        selection_score=55.62637193629375,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_SOL_4h_LONG_h12_636660ae3b",
        asset="SOL",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=12,
        params={"asset_regime_min": 0.018, "breadth_min": 0.018, "breadth_n": 2, "market_min": 0.005999999999999999, "pullback_min": 0.055, "regime_lb": 12, "signal_lb": 6},
        selection_score=55.62637193629375,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_SOL_4h_LONG_h12_7a535fa2dd",
        asset="SOL",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=12,
        params={"asset_regime_min": 0.008, "breadth_min": 0.008, "breadth_n": 3, "market_min": 0.0026666666666666666, "pullback_min": 0.055, "regime_lb": 12, "signal_lb": 6},
        selection_score=54.95970526962708,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_SOL_4h_LONG_h12_1123546e4a",
        asset="SOL",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=12,
        params={"asset_regime_min": 0.018, "breadth_min": 0.018, "breadth_n": 3, "market_min": 0.005999999999999999, "pullback_min": 0.055, "regime_lb": 12, "signal_lb": 6},
        selection_score=54.95970526962708,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="breadth_pullback_reclaim_SOL_4h_LONG_h6_9b4b50c243",
        asset="SOL",
        direction="LONG",
        family="breadth_pullback_reclaim",
        hold_bars=6,
        params={"asset_regime_min": 0.018, "breadth_min": 0.018, "breadth_n": 3, "market_min": 0.005999999999999999, "pullback_min": 0.025, "regime_lb": 12, "signal_lb": 6},
        selection_score=54.89772669878655,
        sleeve_id="r23b_quality_pullback",
    ),
    candidate(
        candidate_id="r15c_ETH_4h_LONG_lb12_h3_b0p001_vz1p0_f0p05",
        asset="ETH",
        direction="LONG",
        family="taker_flow_breakout",
        hold_bars=3,
        params={"buffer": 0.001, "flow_thr": 0.05, "lb": 12, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 1.0},
        selection_score=160.0,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_ETH_4h_LONG_lb12_h3_b0p0_vz1p0_f0p05",
        asset="ETH",
        direction="LONG",
        family="taker_flow_breakout",
        hold_bars=3,
        params={"buffer": 0.0, "flow_thr": 0.05, "lb": 12, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 1.0},
        selection_score=159.5,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_ETH_4h_LONG_lb6_h3_b0p001_vz1p0_f0p05",
        asset="ETH",
        direction="LONG",
        family="taker_flow_breakout",
        hold_bars=3,
        params={"buffer": 0.001, "flow_thr": 0.05, "lb": 6, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 1.0},
        selection_score=159.0,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_ETH_4h_LONG_lb6_h3_b0p0_vz1p0_f0p05",
        asset="ETH",
        direction="LONG",
        family="taker_flow_breakout",
        hold_bars=3,
        params={"buffer": 0.0, "flow_thr": 0.05, "lb": 6, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 1.0},
        selection_score=158.5,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_ETH_1h_SHORT_lb24_h12_b0p0_vz1p0_f0p15",
        asset="ETH",
        timeframe="1h",
        direction="SHORT",
        family="taker_flow_breakout",
        hold_bars=12,
        params={"buffer": 0.0, "flow_thr": 0.15, "lb": 24, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 1.0},
        selection_score=158.0,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_ETH_1h_SHORT_lb24_h12_b0p0_vz0p5_f0p15",
        asset="ETH",
        timeframe="1h",
        direction="SHORT",
        family="taker_flow_breakout",
        hold_bars=12,
        params={"buffer": 0.0, "flow_thr": 0.15, "lb": 24, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 0.5},
        selection_score=157.5,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_ETH_1h_SHORT_lb24_h12_b0p001_vz1p0_f0p15",
        asset="ETH",
        timeframe="1h",
        direction="SHORT",
        family="taker_flow_breakout",
        hold_bars=12,
        params={"buffer": 0.001, "flow_thr": 0.15, "lb": 24, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 1.0},
        selection_score=157.0,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_BTC_4h_LONG_lb6_h3_b0p001_vz1p0_f0p05",
        asset="BTC",
        direction="LONG",
        family="taker_flow_breakout",
        hold_bars=3,
        params={"buffer": 0.001, "flow_thr": 0.05, "lb": 6, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 1.0},
        selection_score=156.5,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_ETH_1h_SHORT_lb24_h4_b0p0_vz0p5_f0p15",
        asset="ETH",
        timeframe="1h",
        direction="SHORT",
        family="taker_flow_breakout",
        hold_bars=4,
        params={"buffer": 0.0, "flow_thr": 0.15, "lb": 24, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 0.5},
        selection_score=156.0,
        sleeve_id="r15c_taker_flow_quality",
    ),
    candidate(
        candidate_id="r15c_BTC_4h_LONG_lb6_h3_b0p0_vz1p0_f0p05",
        asset="BTC",
        direction="LONG",
        family="taker_flow_breakout",
        hold_bars=3,
        params={"buffer": 0.0, "flow_thr": 0.05, "lb": 6, "quality_realized_vol_24_min": 0.005616411766518594, "quality_volz_min": 1.659151276879225, "volz_min": 1.0},
        selection_score=155.5,
        sleeve_id="r15c_taker_flow_quality",
    ),
)

R23B_SCAN_MARKETS = tuple(dict.fromkeys((item.symbol, item.timeframe) for item in CANDIDATES))
R23B_CONTEXT_MARKETS = tuple((symbol, timeframe) for timeframe in ("1h", "4h") for symbol in SYMBOL_BY_ASSET.values())
R22C_MARKETS = R23B_SCAN_MARKETS


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
    volz = prior_zscore(quote_volumes, index, 20)
    ret1 = directional(closes[index] / closes[index - 1] - 1.0, candidate_item.direction) if index >= 1 and closes[index - 1] > 0.0 else 0.0
    realized_vol_24 = realized_volatility(closes, index, 24, 12)
    taker_buy_quote = safe_float(latest.get("taker_buy_quote_volume"))
    quote_volume = safe_float(latest.get("quote_volume"))
    taker_buy_quote_ratio = taker_buy_quote / quote_volume if quote_volume > 0.0 else 0.0
    quote_imbalance = taker_buy_quote_ratio * 2.0 - 1.0
    regime_lb = int(params.get("regime_lb", 0))
    breadth_min = float(params.get("breadth_min", 0.0))
    has_breadth_gate = all(key in params for key in ("breadth_min", "breadth_n", "market_min", "regime_lb"))
    if has_breadth_gate:
        market = market_breadth(
            market_klines_by_symbol=market_klines_by_symbol,
            timeframe=candidate_item.timeframe,
            open_time=latest["open_time"],
            now_ms=now_ms,
            direction=candidate_item.direction,
            regime_lb=regime_lb,
            breadth_min=breadth_min,
        )
        market_ok = (
            market["market_breadth_count"] >= int(params["breadth_n"])
            and market["market_directional_mean"] >= float(params["market_min"])
        )
        if bool(params.get("leader_required", False)):
            leader_value = market.get("market_asset_trends", {}).get("BTC")
            market_ok = market_ok and leader_value is not None and leader_value >= breadth_min
    else:
        market = {
            "market_breadth_count": None,
            "market_breadth_assets": None,
            "market_directional_mean": None,
            "market_asset_trends": {},
        }
        market_ok = True
    features: dict[str, Any] = {
        **market,
        "close": latest["close"],
        "quote_volume_prior_z_20": volz,
        "quote_imbalance": quote_imbalance,
        "taker_buy_quote_ratio": taker_buy_quote_ratio,
        "ret_1_directional": ret1,
        "realized_vol_24": realized_vol_24,
        "breadth_min": breadth_min if has_breadth_gate else None,
        "breadth_n": int(params["breadth_n"]) if has_breadth_gate else None,
        "market_min": float(params["market_min"]) if has_breadth_gate else None,
        "regime_lb": regime_lb if regime_lb else None,
        "risk_fraction": candidate_item.risk_fraction,
        "max_positions": candidate_item.max_positions,
        "sleeve_id": candidate_item.sleeve_id,
        "portfolio_id": PORTFOLIO_ID,
        "portfolio_name": PORTFOLIO_NAME,
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

    if candidate_item.family == "breadth_pullback_reclaim":
        signal_lb = int(params["signal_lb"])
        levels = prior_high_low(closed, index, signal_lb)
        ema_mid = ema(closes, 12)
        asset_trend_raw = trend(closes, index, regime_lb)
        asset_trend = directional(asset_trend_raw or 0.0, candidate_item.direction)
        if levels is None or index < 12:
            return False, features
        prior_high, prior_low = levels
        if candidate_item.direction == "LONG":
            distance_from_high = latest["close"] / prior_high - 1.0 if prior_high > 0.0 else 0.0
            pullback = -distance_from_high
            reclaim = latest["close"] > ema_mid[index] and ret1 > 0.0
        else:
            distance_from_low = latest["close"] / prior_low - 1.0 if prior_low > 0.0 else 0.0
            pullback = distance_from_low
            reclaim = latest["close"] < ema_mid[index] and ret1 > 0.0
        trigger = (
            asset_trend >= float(params["asset_regime_min"])
            and pullback >= float(params["pullback_min"])
            and reclaim
        )
        features.update(
            {
                "family": candidate_item.family,
                "asset_regime_directional": asset_trend,
                "asset_regime_min": float(params["asset_regime_min"]),
                "signal_lb": signal_lb,
                "pullback": pullback,
                "pullback_min": float(params["pullback_min"]),
                "reclaim": reclaim,
                "ema_mid_12": ema_mid[index],
                "prior_high": prior_high,
                "prior_low": prior_low,
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

    if candidate_item.family == "taker_flow_breakout":
        lb = int(params["lb"])
        levels = prior_high_low(closed, index, lb)
        if levels is None:
            return False, features
        prior_high, prior_low = levels
        if candidate_item.direction == "LONG":
            breakout_level = prior_high * (1.0 + float(params["buffer"]))
            breakout = latest["close"] > breakout_level
        else:
            breakout_level = prior_low * (1.0 - float(params["buffer"]))
            breakout = latest["close"] < breakout_level
        flow_directional = directional(quote_imbalance, candidate_item.direction)
        min_volz = max(float(params["volz_min"]), float(params["quality_volz_min"]))
        trigger = (
            breakout
            and volz >= min_volz
            and flow_directional >= float(params["flow_thr"])
            and realized_vol_24 >= float(params["quality_realized_vol_24_min"])
        )
        features.update(
            {
                "family": candidate_item.family,
                "breakout_lb": lb,
                "prior_high": prior_high,
                "prior_low": prior_low,
                "breakout": breakout,
                "breakout_level": breakout_level,
                "buffer": float(params["buffer"]),
                "flow_directional": flow_directional,
                "flow_thr": float(params["flow_thr"]),
                "volz_min": min_volz,
                "quality_volz_min": float(params["quality_volz_min"]),
                "quality_realized_vol_24_min": float(params["quality_realized_vol_24_min"]),
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
