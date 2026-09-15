from __future__ import annotations

import math
import os
from typing import Any

from .strategy import INTERVAL_MS, SYMBOL_BY_ASSET, closed_rows, ms_to_iso, prior_zscore


PULSE_ID = "R25A_MARKET_PULSE_V1"
PULSE_INTERVALS = ("1h", "4h")
PULSE_MARKETS = tuple((s, tf) for tf in PULSE_INTERVALS for s in SYMBOL_BY_ASSET.values())
MAX_PULSE_LAG_SECONDS = int(os.getenv("MAX_MARKET_PULSE_LAG_SECONDS", "600"))
# Initial watch thresholds, not selected from backtest profitability.
THRESHOLDS = {
    "1h": {"BTCUSDT": .01, "ETHUSDT": .012, "SOLUSDT": .018, "BNBUSDT": .015},
    "4h": {"BTCUSDT": .02, "ETHUSDT": .024, "SOLUSDT": .035, "BNBUSDT": .03},
}


def market_snapshot(rows: list[list[Any]], timeframe: str, now_ms: int) -> dict[str, Any]:
    """Require aligned consecutive candles, including the latest expected close."""
    step = INTERVAL_MS[timeframe]
    closed = closed_rows(rows, timeframe, now_ms)
    result: dict[str, Any] = {"status": "INSUFFICIENT_HISTORY", "data_state": "UNKNOWN"}
    if not closed:
        return result
    latest = closed[-1]
    close_ms = latest["open_time"] + step
    expected_close_ms = now_ms // step * step
    # Small publication grace at the exact boundary; never labels long outages fresh.
    stale = expected_close_ms - close_ms > (step if now_ms % step <= 5000 else 0)
    result.update(
        latest_candle_close_utc=ms_to_iso(close_ms),
        close_time_ms=close_ms,
        candle_age_seconds=(now_ms - close_ms) / 1000,
        data_state="STALE" if stale else "FRESH",
    )
    if stale:
        result["status"] = "STALE_DATA"
        return result
    if len(closed) < 25:
        return result
    recent = closed[-25:]
    if any(row["open_time"] % step for row in recent) or any(
        b["open_time"] - a["open_time"] != step for a, b in zip(recent, recent[1:])
    ):
        result.update(status="DATA_GAP", data_state="INVALID")
        return result
    for row in recent:
        if any(not math.isfinite(row[k]) or row[k] <= 0 for k in ("open", "high", "low", "close")):
            result.update(status="INVALID_DATA", data_state="INVALID")
            return result
        if not math.isfinite(row["quote_volume"]) or row["quote_volume"] < 0:
            result.update(status="INVALID_DATA", data_state="INVALID")
            return result
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]):
            result.update(status="INVALID_DATA", data_state="INVALID")
            return result
    ret = latest["close"] / closed[-2]["close"] - 1
    result.update(
        status="NO_EVENT", open_time_ms=latest["open_time"], price=latest["close"],
        return_fraction=ret, return_pct=ret * 100,
        candle_return_pct=(latest["close"] / latest["open"] - 1) * 100,
        range_pct=(latest["high"] / latest["low"] - 1) * 100,
        volume_z20=prior_zscore([r["quote_volume"] for r in closed], len(closed)-1, 20),
    )
    return result


def evaluate_market_pulses(
    klines_cache: dict[tuple[str, str], list[list[Any]]], now_ms: int,
) -> dict[str, Any]:
    snapshots = {}
    for symbol, tf in PULSE_MARKETS:
        try:
            snapshot = market_snapshot(klines_cache.get((symbol, tf), []), tf, now_ms)
        except (TypeError, ValueError, IndexError, OverflowError):
            snapshot = {"status": "INVALID_DATA", "data_state": "INVALID"}
        snapshots[(symbol, tf)] = {"symbol": symbol, "timeframe": tf, **snapshot}
    events = []
    for (symbol, tf), snap in snapshots.items():
        if snap["status"] != "NO_EVENT":
            continue
        ret, threshold = snap["return_fraction"], THRESHOLDS[tf][symbol]
        if abs(ret) < threshold:
            continue
        direction = 1 if ret > 0 else -1
        peers = [
            other for (s, t), other in snapshots.items()
            if t == tf and "return_fraction" in other and other["close_time_ms"] == snap["close_time_ms"]
        ]
        breadth = sum(
            other["return_fraction"] * direction >= THRESHOLDS[tf][other["symbol"]] * .5
            for other in peers
        )
        if snap["volume_z20"] < .25 and abs(ret) < threshold * 1.5:
            continue
        if breadth < 2 and abs(ret) < threshold * 1.8:
            continue
        if snap["candle_age_seconds"] > MAX_PULSE_LAG_SECONDS:
            snap["status"] = "EXPIRED_EVENT"
            continue
        side = "LONG" if ret > 0 else "SHORT"
        snap["status"] = "PULSE"
        events.append({
            "event_id": f"{PULSE_ID}:{symbol}:{tf}:{side}:{snap['close_time_ms']}",
            "event_type": "MARKET_PULSE", "engine_id": PULSE_ID,
            "symbol": symbol, "timeframe": tf, "side": side,
            "candle_open_time_utc": ms_to_iso(snap["open_time_ms"]),
            "candle_close_time_ms": snap["close_time_ms"],
            "candle_close_time_utc": snap["latest_candle_close_utc"],
            "detected_utc": ms_to_iso(now_ms), "notify_time_utc": ms_to_iso(now_ms),
            "expires_at_ms": snap["close_time_ms"] + MAX_PULSE_LAG_SECONDS * 1000,
            "candle_close_price": snap["price"], "return_pct": snap["return_pct"],
            "range_pct": snap["range_pct"], "volume_z20": snap["volume_z20"],
            "threshold_pct": threshold * 100, "market_breadth_count": breadth,
            "market_breadth_assets": len(peers), "freshness_lag_seconds": snap["candle_age_seconds"],
            "severity_ratio": abs(ret) / threshold, "watch_only": True,
            "paper_only": True, "auto_trade": False, "delivery_status": "PENDING",
        })
    events.sort(key=lambda e: (-e["severity_ratio"], e["event_id"]))
    return {"engine_id": PULSE_ID, "events": events, "groups": list(snapshots.values())}
