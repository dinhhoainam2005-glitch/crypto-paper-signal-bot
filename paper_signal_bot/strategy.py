from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Any


STRATEGY_ID = "R15C_TAKER_FLOW_COMPOSITE_VOL_FILTER_PAPER_OBSERVATION"
VOLUME_Z_MIN = 1.659151276879225
REALIZED_VOL_24_MIN = 0.005616411766518594
PREMIUM_Z_MAX: float | None = None
INTERVAL_MS = {
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    symbol: str
    asset: str
    timeframe: str
    direction: str
    lookback: int
    hold_bars: int
    buffer: float
    volz_min: float
    flow_threshold: float
    selection_score: float


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        candidate_id="taker_flow_breakout_LONG_4h_lb12_h3_b0.001_vz1.0_f0.05",
        symbol="ETHUSDT",
        asset="ETH",
        timeframe="4h",
        direction="LONG",
        lookback=12,
        hold_bars=3,
        buffer=0.001,
        volz_min=1.0,
        flow_threshold=0.05,
        selection_score=6.325308541997136,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_LONG_4h_lb12_h3_b0.0_vz1.0_f0.05",
        symbol="ETHUSDT",
        asset="ETH",
        timeframe="4h",
        direction="LONG",
        lookback=12,
        hold_bars=3,
        buffer=0.0,
        volz_min=1.0,
        flow_threshold=0.05,
        selection_score=6.294633853195612,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_LONG_4h_lb6_h3_b0.001_vz1.0_f0.05",
        symbol="ETHUSDT",
        asset="ETH",
        timeframe="4h",
        direction="LONG",
        lookback=6,
        hold_bars=3,
        buffer=0.001,
        volz_min=1.0,
        flow_threshold=0.05,
        selection_score=5.815152544451789,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_LONG_4h_lb6_h3_b0.0_vz1.0_f0.05",
        symbol="ETHUSDT",
        asset="ETH",
        timeframe="4h",
        direction="LONG",
        lookback=6,
        hold_bars=3,
        buffer=0.0,
        volz_min=1.0,
        flow_threshold=0.05,
        selection_score=5.808655810628579,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_SHORT_1h_lb24_h12_b0.0_vz0.5_f0.15",
        symbol="ETHUSDT",
        asset="ETH",
        timeframe="1h",
        direction="SHORT",
        lookback=24,
        hold_bars=12,
        buffer=0.0,
        volz_min=0.5,
        flow_threshold=0.15,
        selection_score=4.840290029078901,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_SHORT_1h_lb24_h12_b0.0_vz1.0_f0.15",
        symbol="ETHUSDT",
        asset="ETH",
        timeframe="1h",
        direction="SHORT",
        lookback=24,
        hold_bars=12,
        buffer=0.0,
        volz_min=1.0,
        flow_threshold=0.15,
        selection_score=4.907248182318826,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_SHORT_1h_lb24_h12_b0.001_vz1.0_f0.15",
        symbol="ETHUSDT",
        asset="ETH",
        timeframe="1h",
        direction="SHORT",
        lookback=24,
        hold_bars=12,
        buffer=0.001,
        volz_min=1.0,
        flow_threshold=0.15,
        selection_score=4.662307652002193,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_LONG_4h_lb6_h3_b0.001_vz1.0_f0.05",
        symbol="BTCUSDT",
        asset="BTC",
        timeframe="4h",
        direction="LONG",
        lookback=6,
        hold_bars=3,
        buffer=0.001,
        volz_min=1.0,
        flow_threshold=0.05,
        selection_score=4.597104732753622,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_SHORT_1h_lb24_h4_b0.0_vz0.5_f0.15",
        symbol="ETHUSDT",
        asset="ETH",
        timeframe="1h",
        direction="SHORT",
        lookback=24,
        hold_bars=4,
        buffer=0.0,
        volz_min=0.5,
        flow_threshold=0.15,
        selection_score=4.593240975607019,
    ),
    Candidate(
        candidate_id="taker_flow_breakout_LONG_4h_lb6_h3_b0.0_vz1.0_f0.05",
        symbol="BTCUSDT",
        asset="BTC",
        timeframe="4h",
        direction="LONG",
        lookback=6,
        hold_bars=3,
        buffer=0.0,
        volz_min=1.0,
        flow_threshold=0.05,
        selection_score=4.590553135629625,
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


def taker_quote_imbalance(row: dict[str, Any]) -> float:
    quote_volume = row["quote_volume"]
    if quote_volume <= 0.0:
        return 0.0
    return 2.0 * (row["taker_buy_quote_volume"] / quote_volume) - 1.0


def candidate_groups() -> dict[tuple[str, str], list[Candidate]]:
    groups: dict[tuple[str, str], list[Candidate]] = {}
    for candidate in CANDIDATES:
        groups.setdefault((candidate.symbol, candidate.timeframe), []).append(candidate)
    return groups


def evaluate_latest(
    *,
    symbol: str,
    timeframe: str,
    klines: list[list[Any]],
    premium_klines: list[list[Any]],
    derivatives_state_available: bool,
    now_ms: int | None = None,
) -> dict[str, Any]:
    candidates = [candidate for candidate in CANDIDATES if candidate.symbol == symbol and candidate.timeframe == timeframe]
    if not candidates:
        return {"symbol": symbol, "timeframe": timeframe, "status": "NO_CANDIDATES", "signals": []}

    closed = closed_rows(klines, timeframe, now_ms=now_ms, premium=False)
    premium_closed = closed_rows(premium_klines, timeframe, now_ms=now_ms, premium=True) if premium_klines else []
    min_bars = max(max(candidate.lookback for candidate in candidates) + 1, 25)
    if len(closed) < min_bars:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "INSUFFICIENT_HISTORY",
            "closed_bars": len(closed),
            "premium_closed_bars": len(premium_closed),
            "signals": [],
        }

    latest = closed[-1]
    index = len(closed) - 1
    quote_volumes = [row["quote_volume"] for row in closed]
    closes = [row["close"] for row in closed]
    volz = prior_zscore(quote_volumes, index, 20)
    imbalance = taker_quote_imbalance(latest)
    realized_vol_24 = realized_volatility(closes, index, 24, 12)

    premium_z: float | None = None
    if len(premium_closed) >= 25:
        premium_by_open = {row["open_time"]: row for row in premium_closed}
        aligned_premium = premium_by_open.get(latest["open_time"], premium_closed[-1])
        premium_values = [row["premium_close"] for row in premium_closed]
        premium_index = max(0, len(premium_closed) - 1)
        for idx, row in enumerate(premium_closed):
            if row["open_time"] == aligned_premium["open_time"]:
                premium_index = idx
                break
        premium_z = prior_zscore(premium_values, premium_index, 24)

    meta_ok = volz >= VOLUME_Z_MIN and realized_vol_24 >= REALIZED_VOL_24_MIN
    raw_signals: list[dict[str, Any]] = []
    for candidate in candidates:
        prior_window = closed[index - candidate.lookback : index]
        prior_high = max(row["high"] for row in prior_window)
        prior_low = min(row["low"] for row in prior_window)
        trigger = False
        if candidate.direction == "LONG":
            trigger = (
                latest["close"] > prior_high * (1.0 + candidate.buffer)
                and volz >= candidate.volz_min
                and imbalance >= candidate.flow_threshold
            )
        elif candidate.direction == "SHORT":
            trigger = (
                latest["close"] < prior_low * (1.0 - candidate.buffer)
                and volz >= candidate.volz_min
                and imbalance <= -candidate.flow_threshold
            )
        if not (trigger and meta_ok):
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
                "candidate": asdict(candidate),
                "symbol": symbol,
                "asset": candidate.asset,
                "timeframe": timeframe,
                "side": candidate.direction,
                "signal_time_ms": latest["open_time"],
                "signal_time_utc": ms_to_iso(latest["open_time"]),
                "entry_time_ms": entry_time,
                "entry_time_utc": ms_to_iso(entry_time),
                "planned_exit_time_ms": entry_time + interval_ms * candidate.hold_bars,
                "planned_exit_time_utc": ms_to_iso(entry_time + interval_ms * candidate.hold_bars),
                "entry_model": "NEXT_OPEN",
                "entry_price": next_open,
                "paper_only": True,
                "features": {
                    "close": latest["close"],
                    "prior_high": prior_high,
                    "prior_low": prior_low,
                    "quote_volume_prior_z_20": volz,
                    "mkt_taker_quote_imbalance_derived": imbalance,
                    "premium_close_prior_z_24": premium_z,
                    "premium_z_max": PREMIUM_Z_MAX,
                    "volume_z_min": VOLUME_Z_MIN,
                    "realized_vol_24": realized_vol_24,
                    "realized_vol_24_min": REALIZED_VOL_24_MIN,
                    "full_derivatives_state_available": derivatives_state_available,
                },
            }
        )

    raw_signals.sort(key=lambda item: item["candidate"]["selection_score"], reverse=True)
    selected = raw_signals[:1]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": "SIGNAL" if selected else "NO_SIGNAL",
        "closed_bars": len(closed),
        "premium_closed_bars": len(premium_closed),
        "latest_closed_bar_utc": ms_to_iso(latest["open_time"]),
        "features": {
            "quote_volume_prior_z_20": volz,
            "mkt_taker_quote_imbalance_derived": imbalance,
            "premium_close_prior_z_24": premium_z,
            "volume_z_min": VOLUME_Z_MIN,
            "realized_vol_24": realized_vol_24,
            "realized_vol_24_min": REALIZED_VOL_24_MIN,
            "full_derivatives_state_available": derivatives_state_available,
        },
        "raw_signal_count": len(raw_signals),
        "signals": selected,
    }
