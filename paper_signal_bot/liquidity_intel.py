from __future__ import annotations

import math
import os
from statistics import median
from typing import Any

from .strategy import INTERVAL_MS, SYMBOL_BY_ASSET, closed_rows, ms_to_iso, prior_zscore, safe_float


LIQUIDITY_ID = "R27A_LIQUIDITY_INTEL_V1"
LIQUIDITY_SYMBOLS = tuple(SYMBOL_BY_ASSET.values())
HYPERLIQUID_COIN_BY_SYMBOL = {symbol: asset for asset, symbol in SYMBOL_BY_ASSET.items()}
DEPTH_BANDS_BPS = (25, 50, 100)
VOLUME_INTERVAL = "15m"
MOVE_THRESHOLDS = {
    "BTCUSDT": 0.0035,
    "ETHUSDT": 0.0045,
    "SOLUSDT": 0.0065,
    "BNBUSDT": 0.0055,
}


def max_liquidity_event_lag_seconds() -> int:
    return max(int(os.getenv("MAX_LIQUIDITY_EVENT_LAG_SECONDS", "600")), 60)


def liquidity_alert_score_min() -> float:
    return max(float(os.getenv("LIQUIDITY_ALERT_SCORE_MIN", "55")), 0.0)


def normalize_levels(levels: list[Any], side: str) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    for level in levels or []:
        if isinstance(level, dict):
            price = safe_float(level.get("px", level.get("price")))
            size = safe_float(level.get("sz", level.get("qty", level.get("size"))))
        else:
            price = safe_float(level[0] if len(level) > 0 else None)
            size = safe_float(level[1] if len(level) > 1 else None)
        if math.isfinite(price) and math.isfinite(size) and price > 0.0 and size > 0.0:
            result.append({"side": side, "price": price, "size": size, "quote": price * size})
    return result


def extract_hyperliquid_levels(book: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    levels = book.get("levels")
    if isinstance(levels, list) and len(levels) >= 2:
        return levels[0] or [], levels[1] or []
    return book.get("bids", []) or [], book.get("asks", []) or []


def book_metrics(book: dict[str, Any] | None, *, source: str, symbol: str) -> dict[str, Any]:
    if not isinstance(book, dict):
        return {"status": "UNAVAILABLE", "source": source, "symbol": symbol}
    if source == "hyperliquid":
        raw_bids, raw_asks = extract_hyperliquid_levels(book)
    else:
        raw_bids, raw_asks = book.get("bids", []) or [], book.get("asks", []) or []
    bids = normalize_levels(raw_bids, "BID")
    asks = normalize_levels(raw_asks, "ASK")
    if not bids or not asks:
        return {"status": "INVALID_BOOK", "source": source, "symbol": symbol}

    best_bid = max(level["price"] for level in bids)
    best_ask = min(level["price"] for level in asks)
    if best_bid <= 0.0 or best_ask <= 0.0 or best_bid >= best_ask:
        return {"status": "INVALID_BOOK", "source": source, "symbol": symbol}
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask / best_bid - 1.0) * 10000.0
    metrics: dict[str, Any] = {
        "status": "OK",
        "source": source,
        "symbol": symbol,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": spread_bps,
    }
    all_near: list[dict[str, float]] = []
    for band in DEPTH_BANDS_BPS:
        bid_depth = sum(level["quote"] for level in bids if (mid / level["price"] - 1.0) * 10000.0 <= band)
        ask_depth = sum(level["quote"] for level in asks if (level["price"] / mid - 1.0) * 10000.0 <= band)
        total = bid_depth + ask_depth
        metrics[f"bid_depth_{band}bps"] = bid_depth
        metrics[f"ask_depth_{band}bps"] = ask_depth
        metrics[f"imbalance_{band}bps"] = (bid_depth - ask_depth) / total if total > 0.0 else 0.0
    for level in bids:
        distance = (mid / level["price"] - 1.0) * 10000.0
        if distance <= 100.0:
            all_near.append({**level, "distance_bps": distance})
    for level in asks:
        distance = (level["price"] / mid - 1.0) * 10000.0
        if distance <= 100.0:
            all_near.append({**level, "distance_bps": distance})
    quotes = [level["quote"] for level in all_near if level["quote"] > 0.0]
    largest = max(all_near, key=lambda item: item["quote"]) if all_near else None
    median_quote = median(quotes) if quotes else 0.0
    if largest:
        metrics.update(
            wall_side=largest["side"],
            wall_price=largest["price"],
            wall_quote=largest["quote"],
            wall_distance_bps=largest["distance_bps"],
            wall_intensity=largest["quote"] / median_quote if median_quote > 0.0 else 0.0,
            heatmap_levels=sorted(all_near, key=lambda item: item["quote"], reverse=True)[:5],
        )
    else:
        metrics.update(
            wall_side=None,
            wall_price=None,
            wall_quote=0.0,
            wall_distance_bps=None,
            wall_intensity=0.0,
            heatmap_levels=[],
        )
    return metrics


def volume_metrics(
    klines_cache: dict[tuple[str, str], list[list[Any]]],
    *,
    symbol: str,
    now_ms: int,
    interval: str = VOLUME_INTERVAL,
) -> dict[str, Any]:
    rows = closed_rows(klines_cache.get((symbol, interval), []), interval, now_ms=now_ms)
    if len(rows) < 25:
        return {"status": "INSUFFICIENT_HISTORY", "symbol": symbol, "timeframe": interval}
    step = INTERVAL_MS[interval]
    latest = rows[-1]
    close_ms = latest["open_time"] + step
    expected_close_ms = now_ms // step * step
    data_state = "STALE" if expected_close_ms - close_ms > step else "FRESH"
    prev_close = rows[-2]["close"]
    ret = latest["close"] / prev_close - 1.0 if prev_close > 0.0 else 0.0
    quote_volume = [row["quote_volume"] for row in rows]
    taker_buy_quote = safe_float(latest.get("taker_buy_quote_volume"))
    latest_quote_volume = safe_float(latest.get("quote_volume"))
    taker_ratio = taker_buy_quote / latest_quote_volume if latest_quote_volume > 0.0 else 0.0
    return {
        "status": "OK",
        "symbol": symbol,
        "timeframe": interval,
        "data_state": data_state,
        "latest_candle_close_ms": close_ms,
        "latest_candle_close_utc": ms_to_iso(close_ms),
        "candle_age_seconds": (now_ms - close_ms) / 1000.0,
        "price": latest["close"],
        "return_fraction": ret,
        "return_pct": ret * 100.0,
        "candle_return_pct": (latest["close"] / latest["open"] - 1.0) * 100.0 if latest["open"] > 0.0 else 0.0,
        "range_pct": (latest["high"] / latest["low"] - 1.0) * 100.0 if latest["low"] > 0.0 else 0.0,
        "volume_z20": prior_zscore(quote_volume, len(rows) - 1, 20),
        "quote_volume": latest_quote_volume,
        "taker_buy_quote_ratio": taker_ratio,
        "taker_imbalance": taker_ratio * 2.0 - 1.0,
    }


def open_interest_metrics(rows: list[dict[str, Any]] | None, *, symbol: str) -> dict[str, Any]:
    if not rows:
        return {"status": "UNAVAILABLE", "symbol": symbol}
    parsed = sorted(rows, key=lambda item: int(item.get("timestamp", 0)))
    values = [
        safe_float(item.get("sumOpenInterestValue")) or safe_float(item.get("sumOpenInterest"))
        for item in parsed
    ]
    values = [value for value in values if math.isfinite(value) and value > 0.0]
    if len(values) < 2:
        return {"status": "INSUFFICIENT_HISTORY", "symbol": symbol}
    latest = values[-1]
    prior = values[max(0, len(values) - 13)]
    change_pct = (latest / prior - 1.0) * 100.0 if prior > 0.0 else 0.0
    one_step_pct = (values[-1] / values[-2] - 1.0) * 100.0 if values[-2] > 0.0 else 0.0
    return {
        "status": "OK",
        "symbol": symbol,
        "latest_open_interest_value": latest,
        "open_interest_change_pct_12": change_pct,
        "open_interest_change_pct_1": one_step_pct,
        "sample_count": len(values),
    }


def hyperliquid_alignment(binance: dict[str, Any], hyperliquid: dict[str, Any]) -> dict[str, Any]:
    if binance.get("status") != "OK" or hyperliquid.get("status") != "OK":
        return {"hyperliquid_state": hyperliquid.get("status", "UNAVAILABLE")}
    binance_mid = safe_float(binance.get("mid"))
    hyper_mid = safe_float(hyperliquid.get("mid"))
    diff_bps = (hyper_mid / binance_mid - 1.0) * 10000.0 if binance_mid > 0.0 and hyper_mid > 0.0 else 0.0
    return {
        "hyperliquid_state": "OK",
        "hyperliquid_mid": hyper_mid,
        "hyperliquid_mid_diff_bps": diff_bps,
        "hyperliquid_wall_side": hyperliquid.get("wall_side"),
        "hyperliquid_wall_distance_bps": hyperliquid.get("wall_distance_bps"),
        "hyperliquid_imbalance_100bps": hyperliquid.get("imbalance_100bps"),
    }


def liquidation_proxy(volume: dict[str, Any], oi: dict[str, Any]) -> str:
    ret = safe_float(volume.get("return_fraction"))
    taker = safe_float(volume.get("taker_imbalance"))
    oi_change = safe_float(oi.get("open_interest_change_pct_12"))
    if ret > 0.0 and oi_change <= -0.15 and taker >= 0.05:
        return "SHORT_LIQUIDATION_PRESSURE"
    if ret < 0.0 and oi_change <= -0.15 and taker <= -0.05:
        return "LONG_LIQUIDATION_PRESSURE"
    if ret > 0.0 and oi_change >= 0.25:
        return "LONG_LEVERAGE_BUILDUP"
    if ret < 0.0 and oi_change >= 0.25:
        return "SHORT_LEVERAGE_BUILDUP"
    return "NEUTRAL"


def classify_liquidity_event(features: dict[str, Any], symbol: str) -> tuple[bool, str, str, float]:
    ret = safe_float(features.get("return_fraction"))
    volz = safe_float(features.get("volume_z20"))
    imbalance = safe_float(features.get("binance_imbalance_100bps"))
    wall_distance = features.get("binance_wall_distance_bps")
    wall_distance_value = safe_float(wall_distance, 9999.0)
    wall_intensity = safe_float(features.get("binance_wall_intensity"))
    oi_change = safe_float(features.get("open_interest_change_pct_12"))
    hl_diff = abs(safe_float(features.get("hyperliquid_mid_diff_bps")))
    threshold = MOVE_THRESHOLDS.get(symbol, 0.005)
    move_ratio = abs(ret) / threshold if threshold > 0.0 else 0.0
    score = 0.0
    score += min(move_ratio, 3.0) * 16.0
    score += min(max(volz, 0.0), 3.0) * 10.0
    score += min(abs(imbalance) / 0.45, 2.0) * 11.0
    score += min(wall_intensity / 5.0, 2.0) * 12.0
    score += min(abs(oi_change) / 0.9, 2.0) * 8.0
    score += min(hl_diff / 8.0, 1.0) * 5.0

    liquidation_state = str(features.get("liquidation_pressure_proxy") or "NEUTRAL")
    if liquidation_state != "NEUTRAL":
        reason = liquidation_state
    elif wall_distance_value <= 40.0 and wall_intensity >= 3.0:
        side = str(features.get("binance_wall_side") or "WALL")
        reason = f"NEAR_{side}_LIQUIDITY_WALL"
    elif abs(imbalance) >= 0.35:
        reason = "ORDER_BOOK_IMBALANCE"
    elif move_ratio >= 1.0 and volz >= 0.5:
        reason = "VOLUME_IMPULSE_TO_LIQUIDITY"
    else:
        reason = "NO_ACTIONABLE_CLUSTER"

    if ret > 0.0:
        side = "LONG"
    elif ret < 0.0:
        side = "SHORT"
    else:
        side = "LONG" if imbalance >= 0.0 else "SHORT"
    return score >= liquidity_alert_score_min() and reason != "NO_ACTIONABLE_CLUSTER", reason, side, min(score, 100.0)


def evaluate_liquidity_intel(
    *,
    klines_cache: dict[tuple[str, str], list[list[Any]]],
    depth_cache: dict[str, dict[str, Any]],
    open_interest_cache: dict[str, list[dict[str, Any]]],
    hyperliquid_cache: dict[str, dict[str, Any]],
    now_ms: int,
    errors: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    errors = errors or {}
    for symbol in LIQUIDITY_SYMBOLS:
        volume = volume_metrics(klines_cache, symbol=symbol, now_ms=now_ms)
        binance_book = book_metrics(depth_cache.get(symbol), source="binance", symbol=symbol)
        oi = open_interest_metrics(open_interest_cache.get(symbol), symbol=symbol)
        hyper_book = book_metrics(hyperliquid_cache.get(symbol), source="hyperliquid", symbol=symbol)
        hyper = hyperliquid_alignment(binance_book, hyper_book)
        data_ok = volume.get("status") == "OK" and binance_book.get("status") == "OK"
        features = {
            "price": volume.get("price"),
            "return_fraction": volume.get("return_fraction"),
            "return_pct": volume.get("return_pct"),
            "volume_z20": volume.get("volume_z20"),
            "taker_imbalance": volume.get("taker_imbalance"),
            "quote_volume": volume.get("quote_volume"),
            "binance_mid": binance_book.get("mid"),
            "binance_spread_bps": binance_book.get("spread_bps"),
            "binance_bid_depth_100bps": binance_book.get("bid_depth_100bps"),
            "binance_ask_depth_100bps": binance_book.get("ask_depth_100bps"),
            "binance_imbalance_100bps": binance_book.get("imbalance_100bps"),
            "binance_wall_side": binance_book.get("wall_side"),
            "binance_wall_price": binance_book.get("wall_price"),
            "binance_wall_quote": binance_book.get("wall_quote"),
            "binance_wall_distance_bps": binance_book.get("wall_distance_bps"),
            "binance_wall_intensity": binance_book.get("wall_intensity"),
            "open_interest_status": oi.get("status"),
            "latest_open_interest_value": oi.get("latest_open_interest_value"),
            "open_interest_change_pct_12": oi.get("open_interest_change_pct_12"),
            "open_interest_change_pct_1": oi.get("open_interest_change_pct_1"),
            **hyper,
        }
        features["liquidation_pressure_proxy"] = liquidation_proxy(volume, oi) if data_ok else "UNKNOWN"
        should_alert, reason, side, score = classify_liquidity_event(features, symbol) if data_ok else (False, "DATA_NOT_READY", "NONE", 0.0)
        status = "LIQUIDITY_EVENT" if should_alert else "NO_EVENT" if data_ok else "DATA_NOT_READY"
        group = {
            "symbol": symbol,
            "timeframe": VOLUME_INTERVAL,
            "status": status,
            "data_state": volume.get("data_state", "UNKNOWN") if data_ok else "DEGRADED",
            "latest_candle_close_utc": volume.get("latest_candle_close_utc"),
            "candidate_count": 1,
            "binance_depth_state": binance_book.get("status"),
            "open_interest_state": oi.get("status"),
            "hyperliquid_state": hyper.get("hyperliquid_state"),
            "error": errors.get(("liquidity", symbol)),
            "features": features,
        }
        groups.append(group)
        close_ms = int(volume.get("latest_candle_close_ms") or 0)
        if not should_alert or close_ms <= 0:
            continue
        expiry_ms = close_ms + max_liquidity_event_lag_seconds() * 1000
        if now_ms > expiry_ms:
            group["status"] = "EXPIRED_EVENT"
            continue
        event = {
            "event_id": f"{LIQUIDITY_ID}:{symbol}:{side}:{close_ms}:{reason}",
            "event_type": "LIQUIDITY_MAP",
            "engine_id": LIQUIDITY_ID,
            "symbol": symbol,
            "timeframe": VOLUME_INTERVAL,
            "side": side,
            "reason": reason,
            "score": round(score, 2),
            "confidence": "HIGH" if score >= 78.0 else "MEDIUM" if score >= 64.0 else "LOW",
            "candle_close_time_ms": close_ms,
            "candle_close_time_utc": volume.get("latest_candle_close_utc"),
            "detected_utc": ms_to_iso(now_ms),
            "notify_time_utc": ms_to_iso(now_ms),
            "expires_at_ms": expiry_ms,
            "watch_only": True,
            "paper_only": True,
            "auto_trade": False,
            "delivery_status": "PENDING",
            "features": features,
        }
        events.append(event)
    events.sort(key=lambda item: (-safe_float(item.get("score")), item["event_id"]))
    return {"engine_id": LIQUIDITY_ID, "events": events, "groups": groups}
