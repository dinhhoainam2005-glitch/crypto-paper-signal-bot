from __future__ import annotations

import math
from collections import deque
from statistics import fmean, pstdev
from typing import Any


ENGINE_ID = "R40B-CORE4-CROWDING-CONSENSUS"
CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
CONSENSUS_MIN = 0.50
MAX_LOOKBACK_DAYS = 180
HISTORY_WARMUP_HOURS = 96
FUNDING_WINDOWS = (1, 3, 9)
PREMIUM_WINDOWS = (8, 24, 72)

# These candidates all cleared the R40A 2020-2023 development and 2024
# validation gates. Frozen results never determine a runtime vote.
STABLE_CANDIDATES = (
    ("COMPOSITE", 0, 180, 1.5),
    ("FUNDING", 0, 180, 2.0),
    ("FUNDING", 0, 90, 2.0),
    ("COMPOSITE", 0, 180, 2.0),
    ("FUNDING", 0, 180, 1.5),
    ("FUNDING", 1, 180, 1.5),
    ("FUNDING", 1, 180, 2.0),
    ("FUNDING", 0, 180, 1.0),
    ("COMPOSITE", 1, 180, 1.5),
    ("COMPOSITE", 2, 180, 2.0),
    ("FUNDING", 0, 90, 1.5),
    ("COMPOSITE", 1, 180, 2.0),
    ("COMPOSITE", 2, 90, 1.5),
    ("PREMIUM", 0, 90, 1.5),
    ("PREMIUM", 1, 180, 1.5),
    ("PREMIUM", 0, 30, 1.5),
    ("COMPOSITE", 2, 30, 1.0),
    ("COMPOSITE", 0, 30, 1.0),
    ("FUNDING", 2, 90, 2.0),
    ("FUNDING", 2, 30, 1.0),
    ("COMPOSITE", 2, 180, 1.0),
    ("PREMIUM", 0, 180, 1.5),
    ("COMPOSITE", 0, 90, 1.0),
    ("FUNDING", 2, 180, 2.0),
    ("FUNDING", 0, 30, 1.0),
    ("COMPOSITE", 0, 90, 1.5),
    ("COMPOSITE", 1, 90, 1.5),
    ("FUNDING", 1, 180, 1.0),
    ("FUNDING", 2, 90, 1.5),
    ("FUNDING", 1, 90, 1.5),
    ("PREMIUM", 2, 180, 1.5),
    ("COMPOSITE", 1, 90, 1.0),
    ("COMPOSITE", 2, 180, 1.5),
    ("PREMIUM", 2, 90, 1.5),
    ("FUNDING", 0, 90, 1.0),
    ("PREMIUM", 0, 90, 1.0),
    ("PREMIUM", 2, 30, 1.0),
    ("PREMIUM", 0, 30, 1.0),
    ("FUNDING", 2, 180, 1.5),
    ("FUNDING", 1, 30, 1.0),
    ("COMPOSITE", 2, 90, 0.5),
    ("COMPOSITE", 2, 180, 0.5),
    ("FUNDING", 1, 90, 1.0),
    ("FUNDING", 2, 90, 1.0),
    ("FUNDING", 2, 180, 0.5),
    ("PREMIUM", 0, 180, 1.0),
    ("COMPOSITE", 1, 90, 0.5),
    ("COMPOSITE", 0, 180, 0.5),
    ("COMPOSITE", 1, 180, 0.5),
    ("COMPOSITE", 0, 90, 0.5),
    ("FUNDING", 0, 30, 0.5),
    ("FUNDING", 1, 180, 0.5),
)


def required_history_start_ms(entry_time_ms: int) -> int:
    hours = MAX_LOOKBACK_DAYS * 24 + HISTORY_WARMUP_HOURS
    return entry_time_ms - hours * 60 * 60 * 1000


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _rolling_event_series(
    events: list[tuple[int, float]], window: int
) -> tuple[list[int], list[float | None]]:
    ordered = sorted(events)
    queue: deque[float] = deque()
    total = 0.0
    times: list[int] = []
    values: list[float | None] = []
    for timestamp, value in ordered:
        queue.append(value)
        total += value
        if len(queue) > window:
            total -= queue.popleft()
        times.append(timestamp)
        values.append(total / window if len(queue) == window else None)
    return times, values


def _align_asof(
    timeline: list[int], event_times: list[int], event_values: list[float | None]
) -> list[float | None]:
    aligned: list[float | None] = []
    position = -1
    for timestamp in timeline:
        while position + 1 < len(event_times) and event_times[position + 1] <= timestamp:
            position += 1
        aligned.append(event_values[position] if position >= 0 else None)
    return aligned


def _prior_z(values: list[float | None], window: int) -> float | None:
    current = values[-1] if values else None
    if current is None:
        return None
    prior = [value for value in values[-window - 1 : -1] if value is not None]
    minimum = max(window // 3, 24)
    if len(prior) < minimum:
        return None
    scale = pstdev(prior)
    if scale <= 0.0:
        return None
    return (current - fmean(prior)) / scale


def parse_funding_rows(rows: list[dict[str, Any]]) -> list[tuple[int, float]]:
    output: list[tuple[int, float]] = []
    for row in rows:
        value = _finite(row.get("fundingRate"))
        try:
            timestamp = int(row.get("fundingTime"))
        except (TypeError, ValueError):
            continue
        if value is not None:
            output.append((timestamp, value))
    return sorted(set(output))


def parse_premium_rows(rows: list[list[Any]]) -> list[tuple[int, float]]:
    output: list[tuple[int, float]] = []
    for row in rows:
        if len(row) < 5:
            continue
        value = _finite(row[4])
        try:
            available_at = int(row[0]) + 60 * 60 * 1000
        except (TypeError, ValueError):
            continue
        if value is not None:
            output.append((available_at, value))
    return sorted(set(output))


def evaluate_crowding_confirmation(
    *,
    symbol: str,
    timeframe: str,
    side: str,
    entry_time_ms: int,
    funding_rows: list[dict[str, Any]],
    premium_rows: list[list[Any]],
) -> dict[str, Any]:
    base = {
        "engine_id": ENGINE_ID,
        "mode": "SHADOW_CONFIRMATION_ONLY",
        "symbol": symbol,
        "timeframe": timeframe,
        "side": side,
        "consensus_min": CONSENSUS_MIN,
        "candidate_total": len(STABLE_CANDIDATES),
        "real_money_authorized": False,
    }
    if symbol not in CORE_SYMBOLS or timeframe not in {"1h", "4h"} or side not in {"LONG", "SHORT"}:
        return {**base, "status": "UNAVAILABLE", "data_state": "INVALID_INPUT"}

    funding = parse_funding_rows(funding_rows)
    premium = parse_premium_rows(premium_rows)
    if len(funding) < 9 or len(premium) < 72:
        return {
            **base,
            "status": "UNAVAILABLE",
            "data_state": "INSUFFICIENT_HISTORY",
            "funding_events": len(funding),
            "premium_hours": len(premium),
        }

    step_hours = 1 if timeframe == "1h" else 4
    max_window = MAX_LOOKBACK_DAYS * 24 // step_hours
    step_ms = step_hours * 60 * 60 * 1000
    timeline = [entry_time_ms - offset * step_ms for offset in range(max_window, -1, -1)]
    series: dict[tuple[str, int], list[float | None]] = {}
    for horizon, window in enumerate(FUNDING_WINDOWS):
        times, values = _rolling_event_series(funding, window)
        series[("FUNDING", horizon)] = _align_asof(timeline, times, values)
    for horizon, window in enumerate(PREMIUM_WINDOWS):
        times, values = _rolling_event_series(premium, window)
        series[("PREMIUM", horizon)] = _align_asof(timeline, times, values)

    z_values: dict[tuple[str, int, int], float | None] = {}
    for metric in ("FUNDING", "PREMIUM"):
        for horizon in range(3):
            for days in (30, 90, 180):
                window = days * 24 // step_hours
                z_values[(metric, horizon, days)] = _prior_z(
                    series[(metric, horizon)], window
                )
    for horizon in range(3):
        for days in (30, 90, 180):
            funding_z = z_values[("FUNDING", horizon, days)]
            premium_z = z_values[("PREMIUM", horizon, days)]
            z_values[("COMPOSITE", horizon, days)] = (
                (funding_z + premium_z) / 2.0
                if funding_z is not None and premium_z is not None
                else None
            )

    sign = 1.0 if side == "LONG" else -1.0
    votes = 0
    available = 0
    for metric, horizon, days, threshold in STABLE_CANDIDATES:
        value = z_values[(metric, horizon, days)]
        if value is None:
            continue
        available += 1
        votes += int(value * sign <= threshold)
    consensus = votes / len(STABLE_CANDIDATES)
    full_history = available == len(STABLE_CANDIDATES)
    confirmed = full_history and consensus >= CONSENSUS_MIN
    diagnostic = z_values[("COMPOSITE", 2, 30)]
    return {
        **base,
        "status": "CONFIRMED" if confirmed else "CROWDING_RISK" if full_history else "UNAVAILABLE",
        "data_state": "FRESH" if full_history else "INSUFFICIENT_HISTORY",
        "confirmed": confirmed,
        "votes": votes,
        "candidate_available": available,
        "consensus": consensus,
        "directional_composite_z_h2_d30": diagnostic * sign if diagnostic is not None else None,
        "funding_events": len(funding),
        "premium_hours": len(premium),
    }


def crowding_forward_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
    annotated = [
        item
        for item in signals
        if isinstance(item.get("crowding_confirmation"), dict)
        and item["crowding_confirmation"].get("status")
    ]
    confirmed = [
        item
        for item in annotated
        if item["crowding_confirmation"].get("status") == "CONFIRMED"
    ]
    crowding_risk = [
        item
        for item in annotated
        if item["crowding_confirmation"].get("status") == "CROWDING_RISK"
    ]
    unavailable = [
        item
        for item in annotated
        if item["crowding_confirmation"].get("status") == "UNAVAILABLE"
    ]
    returns = [
        float(item["net_return_12bps"])
        for item in confirmed
        if item.get("status") == "PAPER_CLOSED"
        and _finite(item.get("net_return_12bps")) is not None
    ]
    gross_win = sum(value for value in returns if value > 0.0)
    gross_loss = abs(sum(value for value in returns if value < 0.0))
    profit_factor = (
        gross_win / gross_loss
        if gross_loss > 0.0
        else 999.0 if gross_win > 0.0 else 0.0
    )
    return {
        "engine_id": ENGINE_ID,
        "mode": "SHADOW_CONFIRMATION_ONLY",
        "annotated_signals": len(annotated),
        "confirmed_signals": len(confirmed),
        "crowding_risk_signals": len(crowding_risk),
        "unavailable_signals": len(unavailable),
        "confirmed_closed_trades": len(returns),
        "confirmed_win_rate": (
            round(sum(value > 0.0 for value in returns) / len(returns), 6)
            if returns
            else 0.0
        ),
        "confirmed_profit_factor_12bps": round(profit_factor, 6),
        "confirmed_average_return_bps": (
            round(fmean(returns) * 10_000.0, 3) if returns else 0.0
        ),
        "blocks_base_signals": False,
        "real_money_authorized": False,
    }
