from __future__ import annotations

import copy
import math
import threading
from datetime import datetime, timezone
from typing import Any

from ..signal_contract import TradePlan
from .clients import (
    BinanceClient,
    Candle,
    SOURCE_USDM_FUTURES,
    market_client_from_env,
)
from .config import RuntimeConfig, load_locked_spec, spec_sha256
from .decision_support import annotate_market_readiness, update_readiness_state
from .production_readiness import update_production_readiness
from .state import ForwardStore, now_iso


DAY_MS = 86_400_000
FOUR_HOUR_MS = 14_400_000
MINUTE_MS = 60_000
COST_RATE = 0.0020
ENTRY_CHANNEL = 55
EXIT_CHANNEL = 20
SMA_DAYS = 200
ATR_DAYS = 20
ATR_MULTIPLIER = 2.0
TP_R = (1.0, 2.0, 4.0)
TP_FRACTIONS = (0.20, 0.30, 0.50)
MAX_HOLD_HOURS = 2160
RISK_PER_TRADE = 0.0035
MAX_SYMBOL_NOTIONAL = 0.20
MAX_INITIAL_GROSS = 0.60
CROSS_MARKET_MIN_CONFIRMATIONS = 2
QUALITY_STANDARD = "STANDARD"
QUALITY_CROSS_MARKET = "A_PLUS_CROSS_MARKET"
V17_STRATEGY_ID = "CORE4_V17_ALT_FAST_CONFIRMED_DAILY"
V17_ENTRY_CHANNEL = 20
V17_EXIT_CHANNEL = 10
V17_TRADABLE_SYMBOLS = ("BNBUSDT", "SOLUSDT")
V17_RISK_PER_TRADE = RISK_PER_TRADE * 0.30
V17_MAX_SYMBOL_NOTIONAL = MAX_SYMBOL_NOTIONAL * 0.30
V17_MAX_INITIAL_GROSS = MAX_INITIAL_GROSS * 0.30
V20_STRATEGY_ID = "CORE4_V20_TREND_BREADTH2_SHADOW"
V20_FAST_SYMBOLS = V17_TRADABLE_SYMBOLS
V20_CORE_WEIGHT_WITH_FAST = 0.60
V20_FAST_WEIGHT = 1.0 - V20_CORE_WEIGHT_WITH_FAST
V20_MIN_BREADTH = 2
V20_SMA_SLOPE_DAYS = 20
V20_MAX_INITIAL_GROSS = MAX_INITIAL_GROSS
V22_STRATEGY_ID = "CORE4_V22_EXECUTION_TRUTH_SHADOW"
V24_STRATEGY_ID = "CORE4_V24_EARLY_EXECUTION_WATCH"
V24_ALERT_WINDOW_SECONDS = 600


def utc_iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc).isoformat()


def true_ranges(candles: list[Candle]) -> list[float]:
    output: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        candidates = [candle.high - candle.low]
        if previous_close is not None:
            candidates.extend([abs(candle.high - previous_close), abs(candle.low - previous_close)])
        output.append(max(candidates))
        previous_close = candle.close
    return output


def completed_and_current(candles: list[Candle], now_ms: int) -> tuple[list[Candle], Candle | None]:
    completed = [candle for candle in candles if candle.close_time_ms <= now_ms]
    current = next(
        (
            candle
            for candle in reversed(candles)
            if candle.open_time_ms <= now_ms < candle.close_time_ms
        ),
        None,
    )
    return completed, current


def evaluate_candidate(
    symbol: str,
    daily_by_symbol: dict[str, list[Candle]],
    now_ms: int,
) -> dict[str, Any] | None:
    completed, current = completed_and_current(daily_by_symbol[symbol], now_ms)
    btc_completed, _ = completed_and_current(daily_by_symbol["BTCUSDT"], now_ms)
    if len(completed) < max(ENTRY_CHANNEL + 1, ATR_DAYS + 1) or len(btc_completed) < SMA_DAYS:
        return None
    decision = completed[-1]
    if current is None or current.open_time_ms != decision.open_time_ms + DAY_MS:
        return None
    prior = completed[-(ENTRY_CHANNEL + 1) : -1]
    entry_high = max(candle.high for candle in prior)
    entry_low = min(candle.low for candle in prior)
    side = None
    if decision.close > entry_high:
        side = "LONG"
    elif decision.close < entry_low:
        side = "SHORT"
    if side is None:
        return None
    btc_sma = sum(candle.close for candle in btc_completed[-SMA_DAYS:]) / SMA_DAYS
    btc_close = btc_completed[-1].close
    if side == "LONG" and not btc_close > btc_sma:
        return None
    if side == "SHORT" and not btc_close < btc_sma:
        return None
    atr = sum(true_ranges(completed)[-ATR_DAYS:]) / ATR_DAYS
    distance = ATR_MULTIPLIER * atr
    entry = current.open
    sign = 1.0 if side == "LONG" else -1.0
    if not math.isfinite(distance) or distance <= 0.0 or distance >= entry:
        return None
    targets = tuple(entry + sign * multiple * distance for multiple in TP_R)
    if min(targets) <= 0.0:
        return None
    generated = datetime.fromtimestamp(current.open_time_ms / 1000.0, tz=timezone.utc)
    plan = TradePlan(
        symbol=symbol,
        side=side,
        timeframe="1d",
        generated_at_utc=generated,
        entry=entry,
        stop_loss=entry - sign * distance,
        take_profit_1=targets[0],
        take_profit_2=targets[1],
        take_profit_3=targets[2],
        invalid_after_utc=datetime.fromtimestamp(
            (current.open_time_ms + DAY_MS) / 1000.0, tz=timezone.utc
        ),
        max_holding_hours=MAX_HOLD_HOURS,
        review_interval_hours=24,
        capital_at_risk_fraction=RISK_PER_TRADE,
        early_exit_condition="Dong cua ngay cat kenh Donchian 20 ngay nguoc huong",
        paper_only=True,
    )
    plan.validate()
    risk_fraction = distance / entry
    return {
        "signal_id": f"CORE4V7:{symbol}:{side}:{decision.open_time_ms}",
        "strategy_id": "CORE4_V7_BETA_REGIME_DONCHIAN",
        "signal_time_utc": utc_iso(decision.close_time_ms),
        "signal_close_ms": decision.close_time_ms,
        "entry_time_ms": current.open_time_ms,
        "entry_time_utc": utc_iso(current.open_time_ms),
        "entry_high": entry_high,
        "entry_low": entry_low,
        "btc_close": btc_close,
        "btc_sma200": btc_sma,
        "atr20": atr,
        "notional_fraction_requested": min(MAX_SYMBOL_NOTIONAL, RISK_PER_TRADE / risk_fraction),
        "plan": plan.as_payload(),
        "status": "CANDIDATE",
        "paper_only": True,
    }


def evaluate_v17_candidate(
    symbol: str,
    daily_by_symbol: dict[str, list[Candle]],
    now_ms: int,
) -> dict[str, Any] | None:
    """Evaluate the frozen V17 fast hypothesis without changing V7."""
    completed, current = completed_and_current(daily_by_symbol[symbol], now_ms)
    btc_completed, _ = completed_and_current(daily_by_symbol["BTCUSDT"], now_ms)
    if len(completed) < max(V17_ENTRY_CHANNEL + 1, ATR_DAYS + 1) or len(btc_completed) < SMA_DAYS:
        return None
    decision = completed[-1]
    if current is None or current.open_time_ms != decision.open_time_ms + DAY_MS:
        return None
    prior = completed[-(V17_ENTRY_CHANNEL + 1) : -1]
    entry_high = max(candle.high for candle in prior)
    entry_low = min(candle.low for candle in prior)
    side = None
    if decision.close > entry_high:
        side = "LONG"
    elif decision.close < entry_low:
        side = "SHORT"
    if side is None:
        return None
    btc_sma = sum(candle.close for candle in btc_completed[-SMA_DAYS:]) / SMA_DAYS
    btc_close = btc_completed[-1].close
    if side == "LONG" and not btc_close > btc_sma:
        return None
    if side == "SHORT" and not btc_close < btc_sma:
        return None
    atr = sum(true_ranges(completed)[-ATR_DAYS:]) / ATR_DAYS
    distance = ATR_MULTIPLIER * atr
    entry = current.open
    sign = 1.0 if side == "LONG" else -1.0
    if not math.isfinite(distance) or distance <= 0.0 or distance >= entry:
        return None
    targets = tuple(entry + sign * multiple * distance for multiple in TP_R)
    if min(targets) <= 0.0:
        return None
    generated = datetime.fromtimestamp(current.open_time_ms / 1000.0, tz=timezone.utc)
    plan = TradePlan(
        symbol=symbol,
        side=side,
        timeframe="1d",
        generated_at_utc=generated,
        entry=entry,
        stop_loss=entry - sign * distance,
        take_profit_1=targets[0],
        take_profit_2=targets[1],
        take_profit_3=targets[2],
        invalid_after_utc=datetime.fromtimestamp(
            (current.open_time_ms + DAY_MS) / 1000.0, tz=timezone.utc
        ),
        max_holding_hours=MAX_HOLD_HOURS,
        review_interval_hours=24,
        capital_at_risk_fraction=V17_RISK_PER_TRADE,
        early_exit_condition="Dong cua ngay cat kenh Donchian 10 ngay nguoc huong",
        paper_only=True,
    )
    plan.validate()
    risk_fraction = distance / entry
    return {
        "signal_id": f"CORE4V17:{symbol}:{side}:{decision.open_time_ms}",
        "strategy_id": V17_STRATEGY_ID,
        "signal_time_utc": utc_iso(decision.close_time_ms),
        "signal_close_ms": decision.close_time_ms,
        "entry_time_ms": current.open_time_ms,
        "entry_time_utc": utc_iso(current.open_time_ms),
        "entry_high": entry_high,
        "entry_low": entry_low,
        "btc_close": btc_close,
        "btc_sma200": btc_sma,
        "atr20": atr,
        "notional_fraction_requested": min(
            V17_MAX_SYMBOL_NOTIONAL, V17_RISK_PER_TRADE / risk_fraction
        ),
        "exit_channel": V17_EXIT_CHANNEL,
        "quality_tier": "V17_SHADOW",
        "quality_label": "V17 SHADOW",
        "shadow_only": True,
        "plan": plan.as_payload(),
        "status": "CANDIDATE",
        "paper_only": True,
    }


def qualify_v17_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Require two same-side CORE4 breakouts, then retain only BNB/SOL."""
    peers: dict[tuple[int, str], list[str]] = {}
    for candidate in candidates:
        key = (int(candidate["entry_time_ms"]), str(candidate["plan"]["side"]))
        peers.setdefault(key, []).append(str(candidate["plan"]["symbol"]))
    qualified: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (int(candidate["entry_time_ms"]), str(candidate["plan"]["side"]))
        symbols = sorted(set(peers[key]))
        if (
            len(symbols) >= CROSS_MARKET_MIN_CONFIRMATIONS
            and candidate["plan"]["symbol"] in V17_TRADABLE_SYMBOLS
        ):
            candidate.update(
                cross_market_count=len(symbols),
                cross_market_threshold=CROSS_MARKET_MIN_CONFIRMATIONS,
                cross_market_symbols=symbols,
                portfolio_risk_count=sum(
                    symbol in V17_TRADABLE_SYMBOLS for symbol in symbols
                ),
            )
            qualified.append(candidate)
    return qualified


def v20_regime_context(
    daily_by_symbol: dict[str, list[Candle]], now_ms: int
) -> dict[str, Any]:
    """Calculate the frozen causal TREND_BREADTH2 gate for the latest closed day."""
    completed_by_symbol = {
        symbol: completed_and_current(candles, now_ms)[0]
        for symbol, candles in daily_by_symbol.items()
    }
    btc = completed_by_symbol.get("BTCUSDT", [])
    if len(btc) < SMA_DAYS + V20_SMA_SLOPE_DAYS:
        return {
            "allowed": False,
            "side": None,
            "breadth": 0,
            "minimum_breadth": V20_MIN_BREADTH,
            "trend_aligned": False,
            "reason": "INSUFFICIENT_HISTORY",
        }
    btc_sma = sum(candle.close for candle in btc[-SMA_DAYS:]) / SMA_DAYS
    btc_sma_lag = (
        sum(
            candle.close
            for candle in btc[-(SMA_DAYS + V20_SMA_SLOPE_DAYS) : -V20_SMA_SLOPE_DAYS]
        )
        / SMA_DAYS
    )
    btc_close = btc[-1].close
    side = "LONG" if btc_close > btc_sma else "SHORT" if btc_close < btc_sma else None
    slope = btc_sma - btc_sma_lag
    trend_aligned = bool(
        (side == "LONG" and slope > 0.0) or (side == "SHORT" and slope < 0.0)
    )
    breadth = 0
    aligned_symbols: list[str] = []
    if side is not None:
        for symbol, candles in completed_by_symbol.items():
            if len(candles) < SMA_DAYS:
                continue
            symbol_sma = sum(candle.close for candle in candles[-SMA_DAYS:]) / SMA_DAYS
            aligned = (side == "LONG" and candles[-1].close > symbol_sma) or (
                side == "SHORT" and candles[-1].close < symbol_sma
            )
            if aligned:
                breadth += 1
                aligned_symbols.append(symbol)
    allowed = bool(side and trend_aligned and breadth >= V20_MIN_BREADTH)
    return {
        "allowed": allowed,
        "side": side,
        "breadth": breadth,
        "minimum_breadth": V20_MIN_BREADTH,
        "aligned_symbols": sorted(aligned_symbols),
        "trend_aligned": trend_aligned,
        "btc_close": btc_close,
        "btc_sma200": btc_sma,
        "btc_sma200_lag20": btc_sma_lag,
        "btc_sma200_slope20": slope,
        "reason": "PASS" if allowed else "TREND_OR_BREADTH_BLOCKED",
    }


def v24_due_boundary(now_ms: int) -> int | None:
    boundary = now_ms // FOUR_HOUR_MS * FOUR_HOUR_MS
    if boundary % DAY_MS == 0:
        return None
    if now_ms - boundary > V24_ALERT_WINDOW_SECONDS * 1000:
        return None
    return boundary


def build_v24_watch_items(
    daily_by_symbol: dict[str, list[Candle]],
    four_hour_by_symbol: dict[str, list[Candle]],
    now_ms: int,
    seen_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Build causal 4h watch alerts for the frozen V20 daily decision."""
    seen = seen_keys or set()
    latest: dict[str, Candle] = {}
    for symbol in daily_by_symbol:
        completed = [
            candle
            for candle in four_hour_by_symbol.get(symbol, [])
            if candle.close_time_ms <= now_ms
        ]
        if not completed:
            return [], set()
        latest[symbol] = completed[-1]
    close_times = {candle.close_time_ms for candle in latest.values()}
    if len(close_times) != 1:
        return [], set()
    signal_close_ms = next(iter(close_times)) + 1
    if now_ms - signal_close_ms > V24_ALERT_WINDOW_SECONDS * 1000:
        return [], set()
    signal_day_ms = latest["BTCUSDT"].open_time_ms // DAY_MS * DAY_MS
    daily_confirmation_ms = signal_day_ms + DAY_MS
    if signal_close_ms >= daily_confirmation_ms:
        return [], set()

    completed_daily = {
        symbol: completed_and_current(candles, now_ms)[0]
        for symbol, candles in daily_by_symbol.items()
    }
    btc_daily = completed_daily.get("BTCUSDT", [])
    if len(btc_daily) < SMA_DAYS:
        return [], set()
    btc_sma200 = sum(candle.close for candle in btc_daily[-SMA_DAYS:]) / SMA_DAYS
    btc_h4_close = latest["BTCUSDT"].close

    raw: dict[str, list[dict[str, Any]]] = {"CORE": [], "FAST": []}
    for sleeve, channel in (("CORE", ENTRY_CHANNEL), ("FAST", V17_ENTRY_CHANNEL)):
        for symbol, daily in completed_daily.items():
            if len(daily) < channel:
                continue
            entry_high = max(candle.high for candle in daily[-channel:])
            entry_low = min(candle.low for candle in daily[-channel:])
            close = latest[symbol].close
            side = None
            trigger = None
            if close > entry_high and btc_h4_close > btc_sma200:
                side, trigger = "LONG", entry_high
            elif close < entry_low and btc_h4_close < btc_sma200:
                side, trigger = "SHORT", entry_low
            if side is None:
                continue
            raw[sleeve].append(
                {
                    "symbol": symbol,
                    "side": side,
                    "sleeve": sleeve,
                    "timeframe": "4h",
                    "h4_close": close,
                    "daily_trigger": trigger,
                    "signal_close_ms": signal_close_ms,
                    "signal_close_utc": utc_iso(signal_close_ms),
                    "daily_confirmation_ms": daily_confirmation_ms,
                    "daily_confirmation_utc": utc_iso(daily_confirmation_ms),
                    "lead_hours": (
                        daily_confirmation_ms - signal_close_ms
                    ) / 3_600_000.0,
                    "btc_h4_close": btc_h4_close,
                    "btc_sma200_prior_day": btc_sma200,
                }
            )

    selected = list(raw["CORE"])
    fast_counts = {
        side: len(
            {item["symbol"] for item in raw["FAST"] if item["side"] == side}
        )
        for side in ("LONG", "SHORT")
    }
    prior_context = v20_regime_context(daily_by_symbol, now_ms)
    if prior_context.get("allowed"):
        selected.extend(
            item
            for item in raw["FAST"]
            if item["symbol"] in V20_FAST_SYMBOLS
            and fast_counts[item["side"]] >= 2
        )

    output: list[dict[str, Any]] = []
    new_keys: set[str] = set()
    for item in selected:
        key = f"{item['sleeve']}:{item['symbol']}:{signal_day_ms}"
        if key in seen:
            continue
        item.update(
            watch_key=key,
            status="WATCH_ONLY",
            paper_only=True,
            v24_configuration="ROUTED_C1",
            historical_precision=0.704,
            historical_coverage=0.846,
        )
        output.append(item)
        new_keys.add(key)
    return output, new_keys


def _as_v20_candidate(
    source: dict[str, Any], sleeve: str, weight: float, context: dict[str, Any]
) -> dict[str, Any]:
    candidate = copy.deepcopy(source)
    plan = candidate["plan"]
    symbol = str(plan["symbol"])
    side = str(plan["side"])
    risk_budget = RISK_PER_TRADE * weight
    symbol_cap = MAX_SYMBOL_NOTIONAL * weight
    risk_fraction = abs(float(plan["entry"]) - float(plan["stop_loss"])) / float(
        plan["entry"]
    )
    plan["capital_at_risk_fraction"] = risk_budget
    candidate.update(
        signal_id=f"CORE4V20{str(sleeve).upper()}:{symbol}:{side}:{candidate['entry_time_ms']}",
        strategy_id=V20_STRATEGY_ID,
        sleeve=str(sleeve).upper(),
        shadow_version="V20",
        shadow_only=True,
        exit_channel=EXIT_CHANNEL if sleeve == "CORE" else V17_EXIT_CHANNEL,
        quality_tier=f"V20_{str(sleeve).upper()}_SHADOW",
        quality_label=f"V20 {str(sleeve).upper()} SHADOW",
        notional_fraction_requested=min(symbol_cap, risk_budget / risk_fraction),
        v20_regime_side=context.get("side"),
        v20_regime_breadth=int(context.get("breadth", 0)),
        v20_regime_min_breadth=V20_MIN_BREADTH,
        v20_trend_aligned=bool(context.get("trend_aligned", False)),
        v20_btc_sma200_slope20=context.get("btc_sma200_slope20"),
    )
    return candidate


def build_v20_candidates(
    daily_by_symbol: dict[str, list[Candle]],
    now_ms: int,
    symbols: tuple[str, ...] | None = None,
    active_keys: set[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the frozen V20 CORE+FAST request set for one daily decision."""
    universe = symbols or tuple(daily_by_symbol)
    occupied = active_keys or set()
    core_raw = [
        candidate
        for symbol in universe
        if ("CORE", symbol) not in occupied
        if (candidate := evaluate_candidate(symbol, daily_by_symbol, now_ms)) is not None
    ]
    classify_cross_market_candidates(core_raw)
    fast_raw = [
        candidate
        for symbol in universe
        if ("FAST", symbol) not in occupied
        if (candidate := evaluate_v17_candidate(symbol, daily_by_symbol, now_ms))
        is not None
    ]
    fast_confirmed = len(
        {str(candidate["plan"]["symbol"]) for candidate in fast_raw}
    ) >= CROSS_MARKET_MIN_CONFIRMATIONS
    context = v20_regime_context(daily_by_symbol, now_ms)
    fast_qualified = qualify_v17_candidates(fast_raw)
    fast_qualified = [
        candidate
        for candidate in fast_qualified
        if candidate["plan"]["symbol"] in V20_FAST_SYMBOLS
        and candidate["plan"]["side"] == context.get("side")
    ]
    fast_active = bool(fast_confirmed and context.get("allowed") and fast_qualified)
    context = {
        **context,
        "fast_confirmed": fast_confirmed,
        "fast_active": fast_active,
        "fast_symbols": sorted(
            str(candidate["plan"]["symbol"]) for candidate in fast_qualified
        )
        if fast_active
        else [],
    }
    core_weight = V20_CORE_WEIGHT_WITH_FAST if fast_active else 1.0
    candidates = [
        _as_v20_candidate(candidate, "CORE", core_weight, context)
        for candidate in core_raw
    ]
    if fast_active:
        candidates.extend(
            _as_v20_candidate(candidate, "FAST", V20_FAST_WEIGHT, context)
            for candidate in fast_qualified
        )
    cluster_risk_fraction = sum(
        float(candidate["plan"]["capital_at_risk_fraction"])
        for candidate in candidates
    )
    for candidate in candidates:
        candidate["portfolio_risk_count"] = len(candidates)
        candidate["portfolio_risk_fraction"] = cluster_risk_fraction
        candidate["v20_fast_active"] = fast_active
    return candidates, context


def classify_cross_market_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Label executable V7 signals without changing their entry or sizing."""
    symbols_by_decision: dict[int, set[str]] = {}
    for candidate in candidates:
        symbols_by_decision.setdefault(int(candidate["entry_time_ms"]), set()).add(
            str(candidate["plan"]["symbol"])
        )

    for candidate in candidates:
        peers = sorted(symbols_by_decision[int(candidate["entry_time_ms"])])
        cross_market = len(peers) >= CROSS_MARKET_MIN_CONFIRMATIONS
        candidate.update(
            quality_tier=(QUALITY_CROSS_MARKET if cross_market else QUALITY_STANDARD),
            quality_label=("A+ CROSS-MARKET" if cross_market else "V7 STANDARD"),
            cross_market_count=len(peers),
            cross_market_threshold=CROSS_MARKET_MIN_CONFIRMATIONS,
            cross_market_symbols=peers,
            mtf_policy="CONTEXT_ONLY_NO_TRADE_CREATION",
            mtf_timeframes=["1h", "4h"],
            mtf_can_create_trade=False,
        )
    return candidates


def opposite_channel_exit(
    position: dict[str, Any], daily: list[Candle], decision_open_ms: int
) -> bool:
    index_by_time = {candle.open_time_ms: index for index, candle in enumerate(daily)}
    index = index_by_time.get(decision_open_ms)
    exit_channel = int(position.get("exit_channel", EXIT_CHANNEL))
    if index is None or index < exit_channel:
        return False
    decision = daily[index]
    prior = daily[index - exit_channel : index]
    if position["side"] == "LONG":
        return decision.close < min(candle.low for candle in prior)
    return decision.close > max(candle.high for candle in prior)


def new_position(signal: dict[str, Any], equity: float, notional_fraction: float) -> dict[str, Any]:
    plan = signal["plan"]
    notional = equity * notional_fraction
    quantity = notional / float(plan["entry"])
    risk_cash = quantity * abs(float(plan["entry"]) - float(plan["stop_loss"]))
    return {
        "signal_id": signal["signal_id"],
        "strategy_id": signal.get("strategy_id", "CORE4_V7_BETA_REGIME_DONCHIAN"),
        "shadow_only": bool(signal.get("shadow_only", False)),
        "shadow_version": signal.get("shadow_version"),
        "sleeve": signal.get("sleeve"),
        "exit_channel": int(signal.get("exit_channel", EXIT_CHANNEL)),
        "quality_tier": signal.get("quality_tier", QUALITY_STANDARD),
        "quality_label": signal.get("quality_label", "V7 STANDARD"),
        "cross_market_count": int(signal.get("cross_market_count", 1)),
        "cross_market_symbols": list(
            signal.get("cross_market_symbols", [plan["symbol"]])
        ),
        "mtf_policy": signal.get(
            "mtf_policy", "CONTEXT_ONLY_NO_TRADE_CREATION"
        ),
        "symbol": plan["symbol"],
        "side": plan["side"],
        "entry_time_ms": signal["entry_time_ms"],
        "entry_time_utc": signal["entry_time_utc"],
        "entry": float(plan["entry"]),
        "initial_stop": float(plan["stop_loss"]),
        "current_stop": float(plan["stop_loss"]),
        "targets": [float(plan["take_profit_1"]), float(plan["take_profit_2"]), float(plan["take_profit_3"])],
        "next_target_index": 0,
        "tp_fractions": list(TP_FRACTIONS),
        "initial_quantity": quantity,
        "remaining_quantity": quantity,
        "initial_notional": notional,
        "initial_risk_cash": risk_cash,
        "entry_fee": notional * COST_RATE,
        "exit_fees": 0.0,
        "price_pnl": 0.0,
        "funding_pnl": 0.0,
        "events": [],
        "last_processed_minute_ms": None,
        "last_funding_time_ms": signal["entry_time_ms"] - 1,
        "max_exit_time_ms": signal["entry_time_ms"] + MAX_HOLD_HOURS * 3_600_000,
        "paper_only": True,
        "status": "OPEN",
    }


def _direction(position: dict[str, Any]) -> float:
    return 1.0 if position["side"] == "LONG" else -1.0


def _fill(
    state: dict[str, Any],
    position: dict[str, Any],
    quantity: float,
    price: float,
    reason: str,
    time_ms: int,
) -> dict[str, Any]:
    quantity = min(quantity, float(position["remaining_quantity"]))
    fee = quantity * price * COST_RATE
    price_pnl = _direction(position) * quantity * (price - float(position["entry"]))
    position["remaining_quantity"] -= quantity
    position["exit_fees"] += fee
    position["price_pnl"] += price_pnl
    state["cash"] += price_pnl - fee
    event = {
        "event_id": f"{position['signal_id']}:{reason}:{time_ms}",
        "signal_id": position["signal_id"],
        "symbol": position["symbol"],
        "side": position["side"],
        "quality_tier": position.get("quality_tier", QUALITY_STANDARD),
        "quality_label": position.get("quality_label", "V7 STANDARD"),
        "strategy_id": position.get("strategy_id", "CORE4_V7_BETA_REGIME_DONCHIAN"),
        "shadow_only": bool(position.get("shadow_only", False)),
        "shadow_version": position.get("shadow_version"),
        "sleeve": position.get("sleeve"),
        "exit_channel": int(position.get("exit_channel", EXIT_CHANNEL)),
        "reason": reason,
        "time_ms": time_ms,
        "time_utc": utc_iso(time_ms),
        "price": price,
        "quantity": quantity,
        "remaining_fraction": max(0.0, position["remaining_quantity"] / position["initial_quantity"]),
        "paper_only": True,
    }
    position["events"].append(event)
    state.setdefault("events", []).append(event)
    return event


def _move_stop(position: dict[str, Any]) -> None:
    if position["next_target_index"] == 1:
        position["current_stop"] = position["entry"]
    elif position["next_target_index"] == 2:
        position["current_stop"] = position["targets"][0]


def _close_trade(state: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
    final_event = position["events"][-1]
    net_pnl = (
        position["price_pnl"]
        + position["funding_pnl"]
        - position["entry_fee"]
        - position["exit_fees"]
    )
    trade = {
        **{key: position[key] for key in ("signal_id", "symbol", "side", "entry_time_utc", "entry", "initial_stop", "initial_notional", "initial_risk_cash", "paper_only")},
        "quality_tier": position.get("quality_tier", QUALITY_STANDARD),
        "quality_label": position.get("quality_label", "V7 STANDARD"),
        "strategy_id": position.get("strategy_id", "CORE4_V7_BETA_REGIME_DONCHIAN"),
        "shadow_only": bool(position.get("shadow_only", False)),
        "shadow_version": position.get("shadow_version"),
        "sleeve": position.get("sleeve"),
        "exit_channel": int(position.get("exit_channel", EXIT_CHANNEL)),
        "cross_market_count": int(position.get("cross_market_count", 1)),
        "cross_market_symbols": list(
            position.get("cross_market_symbols", [position["symbol"]])
        ),
        "mtf_policy": position.get(
            "mtf_policy", "CONTEXT_ONLY_NO_TRADE_CREATION"
        ),
        "tp1": position["targets"][0],
        "tp2": position["targets"][1],
        "tp3": position["targets"][2],
        "exit_time_utc": final_event["time_utc"],
        "exit_price": final_event["price"],
        "exit_reason": "+".join(dict.fromkeys(event["reason"] for event in position["events"])),
        "holding_hours": (final_event["time_ms"] - position["entry_time_ms"]) / 3_600_000.0,
        "price_pnl": position["price_pnl"],
        "funding_pnl": position["funding_pnl"],
        "fees": position["entry_fee"] + position["exit_fees"],
        "net_pnl": net_pnl,
        "realized_r": net_pnl / position["initial_risk_cash"],
        "status": "CLOSED",
    }
    state.setdefault("closed_trades", []).append(trade)
    for signal in state.get("signals", []):
        if signal.get("signal_id") == position["signal_id"]:
            signal.update(status="CLOSED", closed_trade=trade)
            break
    return trade


def process_position_path(
    state: dict[str, Any],
    position: dict[str, Any],
    minutes: list[Candle],
    daily: list[Candle],
    funding_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    notifications: list[dict[str, Any]] = []
    funding_by_minute: dict[int, list[dict[str, Any]]] = {}
    for row in funding_rows:
        funding_by_minute.setdefault(int(row["fundingTime"]) // MINUTE_MS * MINUTE_MS, []).append(row)
    for candle in minutes:
        if position["remaining_quantity"] <= 1e-12:
            break
        timestamp = candle.open_time_ms
        if timestamp % DAY_MS == 0 and timestamp > position["entry_time_ms"]:
            decision_open = timestamp - DAY_MS
            reason = None
            if timestamp >= position["max_exit_time_ms"]:
                reason = "MAX_HOLD"
            elif opposite_channel_exit(position, daily, decision_open):
                reason = "CHANNEL_EXIT"
            if reason is not None:
                notifications.append(
                    _fill(state, position, position["remaining_quantity"], candle.open, reason, timestamp)
                )
                position["last_processed_minute_ms"] = timestamp
                break

        adverse_open = (
            candle.open <= position["current_stop"]
            if position["side"] == "LONG"
            else candle.open >= position["current_stop"]
        )
        if adverse_open:
            notifications.append(
                _fill(state, position, position["remaining_quantity"], candle.open, "STOP_GAP", timestamp)
            )
            position["last_processed_minute_ms"] = timestamp
            break
        while position["next_target_index"] < 3:
            index = position["next_target_index"]
            target = position["targets"][index]
            favorable_open = candle.open >= target if position["side"] == "LONG" else candle.open <= target
            if not favorable_open:
                break
            notifications.append(
                _fill(
                    state,
                    position,
                    position["initial_quantity"] * position["tp_fractions"][index],
                    target,
                    f"TP{index + 1}_GAP",
                    timestamp,
                )
            )
            position["next_target_index"] += 1
            _move_stop(position)

        for funding in funding_by_minute.get(timestamp, []):
            funding_time = int(funding["fundingTime"])
            if funding_time <= int(position["last_funding_time_ms"]):
                continue
            mark = float(funding.get("markPrice") or candle.open)
            rate = float(funding["fundingRate"])
            funding_pnl = -_direction(position) * position["remaining_quantity"] * mark * rate
            position["funding_pnl"] += funding_pnl
            state["cash"] += funding_pnl
            position["last_funding_time_ms"] = funding_time

        stop_hit = (
            candle.low <= position["current_stop"]
            if position["side"] == "LONG"
            else candle.high >= position["current_stop"]
        )
        if stop_hit:
            notifications.append(
                _fill(
                    state,
                    position,
                    position["remaining_quantity"],
                    position["current_stop"],
                    "STOP",
                    timestamp,
                )
            )
        else:
            while position["next_target_index"] < 3:
                index = position["next_target_index"]
                target = position["targets"][index]
                hit = candle.high >= target if position["side"] == "LONG" else candle.low <= target
                if not hit:
                    break
                notifications.append(
                    _fill(
                        state,
                        position,
                        position["initial_quantity"] * position["tp_fractions"][index],
                        target,
                        f"TP{index + 1}",
                        timestamp,
                    )
                )
                position["next_target_index"] += 1
                _move_stop(position)
        position["last_processed_minute_ms"] = timestamp

    trade = None
    if position["remaining_quantity"] <= 1e-12:
        trade = _close_trade(state, position)
    return notifications, trade


def ensure_v17_state(state: dict[str, Any]) -> dict[str, Any]:
    shadow = state.setdefault(
        "shadow_v17",
        {
            "strategy_id": V17_STRATEGY_ID,
            "cash": 1.0,
            "equity": 1.0,
            "signals": [],
            "active_positions": [],
            "closed_trades": [],
            "events": [],
        },
    )
    for key, default in (
        ("cash", 1.0),
        ("equity", 1.0),
        ("signals", []),
        ("active_positions", []),
        ("closed_trades", []),
        ("events", []),
    ):
        shadow.setdefault(key, default)
    return shadow


def ensure_v20_state(state: dict[str, Any]) -> dict[str, Any]:
    shadow = state.setdefault(
        "shadow_v20",
        {
            "strategy_id": V20_STRATEGY_ID,
            "created_utc": now_iso(),
            "cash": 1.0,
            "equity": 1.0,
            "signals": [],
            "active_positions": [],
            "closed_trades": [],
            "events": [],
            "last_regime_context": {},
        },
    )
    for key, default in (
        ("created_utc", now_iso()),
        ("cash", 1.0),
        ("equity", 1.0),
        ("signals", []),
        ("active_positions", []),
        ("closed_trades", []),
        ("events", []),
        ("last_regime_context", {}),
    ):
        shadow.setdefault(key, default)
    return shadow


def ensure_v22_state(state: dict[str, Any]) -> dict[str, Any]:
    shadow = state.setdefault(
        "shadow_v22",
        {
            "strategy_id": V22_STRATEGY_ID,
            "created_utc": now_iso(),
            "cash": 1.0,
            "equity": 1.0,
            "signals": [],
            "active_positions": [],
            "closed_trades": [],
            "events": [],
            "execution_samples": [],
            "last_quote_error": None,
        },
    )
    for key, default in (
        ("created_utc", now_iso()),
        ("cash", 1.0),
        ("equity", 1.0),
        ("signals", []),
        ("active_positions", []),
        ("closed_trades", []),
        ("events", []),
        ("execution_samples", []),
        ("last_quote_error", None),
    ):
        shadow.setdefault(key, default)
    return shadow


def ensure_v24_state(state: dict[str, Any]) -> dict[str, Any]:
    watch = state.setdefault(
        "watch_v24",
        {
            "strategy_id": V24_STRATEGY_ID,
            "created_utc": now_iso(),
            "alerts": [],
            "seen_keys": [],
            "last_evaluated_boundary_ms": None,
            "last_evaluated_utc": None,
            "last_status": "WAITING_4H_CLOSE",
            "last_error": None,
        },
    )
    watch.setdefault("strategy_id", V24_STRATEGY_ID)
    watch.setdefault("created_utc", now_iso())
    watch.setdefault("alerts", [])
    watch.setdefault("seen_keys", [])
    watch.setdefault("last_evaluated_boundary_ms", None)
    watch.setdefault("last_evaluated_utc", None)
    watch.setdefault("last_status", "WAITING_4H_CLOSE")
    watch.setdefault("last_error", None)
    return watch


def build_v22_execution_signal(
    source: dict[str, Any],
    quote: dict[str, Any],
    quote_source: str,
    detected_ms: int,
) -> dict[str, Any]:
    """Mirror one V20 signal using the executable side of a live Futures quote."""
    signal = copy.deepcopy(source)
    plan = signal["plan"]
    side = str(plan["side"])
    bid = float(quote["bid_price"])
    ask = float(quote["ask_price"])
    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0.0 or ask < bid:
        raise ValueError("invalid book ticker")
    reference_entry = float(plan["entry"])
    executable_entry = ask if side == "LONG" else bid
    midpoint = (bid + ask) / 2.0
    direction = 1.0 if side == "LONG" else -1.0
    spread_bps = (ask - bid) / midpoint * 10_000.0
    adverse_bps = direction * (executable_entry / reference_entry - 1.0) * 10_000.0
    original_id = str(signal["signal_id"])
    symbol = str(plan["symbol"])
    sleeve = str(signal.get("sleeve", "CORE"))
    plan["entry"] = executable_entry
    risk_fraction = abs(executable_entry - float(plan["stop_loss"])) / executable_entry
    risk_budget = float(plan.get("capital_at_risk_fraction", RISK_PER_TRADE))
    if risk_fraction <= 0.0 or not math.isfinite(risk_fraction):
        raise ValueError("invalid execution-adjusted risk distance")
    signal.update(
        signal_id=(
            f"CORE4V22EXEC:{sleeve}:{symbol}:{side}:{source['entry_time_ms']}"
        ),
        origin_v20_signal_id=original_id,
        strategy_id=V22_STRATEGY_ID,
        shadow_version="V22",
        shadow_only=True,
        quality_tier=f"V22_{sleeve}_EXECUTION_TRUTH",
        quality_label=f"V22 {sleeve} EXECUTION TRUTH",
        reference_entry_time_ms=int(source["entry_time_ms"]),
        reference_entry_time_utc=str(source["entry_time_utc"]),
        entry_time_ms=detected_ms,
        entry_time_utc=utc_iso(detected_ms),
        current_price=midpoint,
        detected_at_utc=utc_iso(detected_ms),
        entry_lag_seconds=max(
            0.0, (detected_ms - int(source["entry_time_ms"])) / 1000.0
        ),
        directional_chase_bps=adverse_bps,
        notional_fraction_requested=min(
            float(source["notional_fraction_requested"]), risk_budget / risk_fraction
        ),
        execution_truth={
            "status": "CAPTURED",
            "source": quote_source,
            "captured_at_ms": int(quote.get("captured_at_ms", detected_ms)),
            "captured_at_utc": utc_iso(
                int(quote.get("captured_at_ms", detected_ms))
            ),
            "reference_entry": reference_entry,
            "bid_price": bid,
            "ask_price": ask,
            "mid_price": midpoint,
            "estimated_market_fill": executable_entry,
            "spread_bps": spread_bps,
            "adverse_fill_bps": adverse_bps,
        },
    )
    return signal


class ForwardEngine:
    def __init__(
        self,
        client: BinanceClient | None = None,
        store: ForwardStore | None = None,
        config: RuntimeConfig | None = None,
    ) -> None:
        self.spec = load_locked_spec()
        self.config = config or RuntimeConfig.from_env()
        self.client = client or market_client_from_env()
        self.store = store or ForwardStore(self.config.state_path, spec_sha256())
        self.lock = threading.RLock()
        self.symbols = tuple(self.spec["universe"])

    def _daily_candles(self, symbol: str) -> tuple[list[Candle], str]:
        loader = getattr(self.client, "daily_candles_with_source", None)
        if loader is None:
            return self.client.daily_candles(symbol), SOURCE_USDM_FUTURES
        return loader(symbol)

    def _minute_candles(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> tuple[list[Candle], str]:
        loader = getattr(self.client, "minute_candles_with_source", None)
        if loader is None:
            return (
                self.client.minute_candles(
                    symbol, start_ms, end_ms, self.config.max_replay_minutes
                ),
                SOURCE_USDM_FUTURES,
            )
        return loader(symbol, start_ms, end_ms, self.config.max_replay_minutes)

    def _book_ticker(self, symbol: str) -> tuple[dict[str, Any], str]:
        loader = getattr(self.client, "book_ticker_with_source", None)
        if loader is None:
            raise RuntimeError("book ticker is unavailable on the market client")
        return loader(symbol)

    def _four_hour_candles(self, symbol: str) -> tuple[list[Candle], str]:
        loader = getattr(self.client, "four_hour_candles_with_source", None)
        if loader is None:
            raise RuntimeError("four-hour Futures candles are unavailable")
        return loader(symbol, 3)

    def _replay_position(
        self,
        state: dict[str, Any],
        position: dict[str, Any],
        daily: list[Candle],
        now_ms: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        start_ms = (
            position["entry_time_ms"]
            if position["last_processed_minute_ms"] is None
            else int(position["last_processed_minute_ms"]) + MINUTE_MS
        )
        end_ms = now_ms - 1
        if end_ms < start_ms:
            return [], None
        minutes, minute_source = self._minute_candles(
            position["symbol"], start_ms, end_ms
        )
        if (
            self.config.require_futures_for_signals
            and minute_source != SOURCE_USDM_FUTURES
        ):
            raise RuntimeError(f"untrusted minute data source: {minute_source}")
        minutes = [candle for candle in minutes if candle.close_time_ms <= now_ms]
        funding = self.client.funding_rates(
            position["symbol"], int(position["last_funding_time_ms"]) + 1, now_ms
        )
        return process_position_path(state, position, minutes, daily, funding)

    def scan(self, now_ms: int | None = None) -> dict[str, Any]:
        with self.lock:
            now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
            state = self.store.load()
            daily_by_symbol: dict[str, list[Candle]] = {}
            daily_sources: dict[str, str] = {}
            groups: list[dict[str, Any]] = []
            notifications: list[dict[str, Any]] = []
            errors: list[str] = []
            v22_errors: list[str] = []
            for symbol in self.symbols:
                try:
                    candles, source = self._daily_candles(symbol)
                    daily_by_symbol[symbol] = candles
                    daily_sources[symbol] = source
                    completed, current = completed_and_current(daily_by_symbol[symbol], now_ms)
                    ready = len(completed) >= SMA_DAYS and current is not None
                    source_trusted = source == SOURCE_USDM_FUTURES
                    group_status = "READY" if ready else "DATA_NOT_READY"
                    if ready and self.config.require_futures_for_signals and not source_trusted:
                        group_status = "SOURCE_MISMATCH"
                    groups.append(
                        {
                            "symbol": symbol,
                            "status": group_status,
                            "completed_bars": len(completed),
                            "latest_close_utc": utc_iso(completed[-1].close_time_ms) if completed else None,
                            "data_source": source,
                            "data_transport": getattr(
                                self.client, "data_transport", "BINANCE_REST"
                            ),
                            "source_trusted": source_trusted,
                        }
                    )
                    if len(completed) >= ENTRY_CHANNEL and current is not None:
                        signal_window = completed[-ENTRY_CHANNEL:]
                        long_trigger = max(candle.high for candle in signal_window)
                        short_trigger = min(candle.low for candle in signal_window)
                        groups[-1].update(
                            current_price=current.close,
                            latest_daily_close=completed[-1].close,
                            long_breakout_trigger=long_trigger,
                            short_breakout_trigger=short_trigger,
                            distance_to_long_breakout_pct=(
                                (long_trigger / current.close - 1.0) * 100.0
                                if current.close > 0.0
                                else None
                            ),
                            distance_to_short_breakout_pct=(
                                (current.close / short_trigger - 1.0) * 100.0
                                if short_trigger > 0.0
                                else None
                            ),
                        )
                except Exception as exc:
                    errors.append(f"{symbol} daily: {type(exc).__name__}: {exc}")
                    groups.append({"symbol": symbol, "status": "ERROR", "completed_bars": 0})
            if "BTCUSDT" in daily_by_symbol:
                btc_completed, btc_current = completed_and_current(
                    daily_by_symbol["BTCUSDT"], now_ms
                )
                annotate_market_readiness(groups, btc_completed, btc_current)
            if len(daily_by_symbol) != len(self.symbols):
                state["last_scan_utc"] = utc_iso(now_ms)
                state["scan_count"] = int(state.get("scan_count", 0)) + 1
                state["errors"] = (state.get("errors", []) + [{"time_utc": now_iso(), "message": item} for item in errors])[-50:]
                scan = {
                    "status": "DEGRADED",
                    "time_utc": utc_iso(now_ms),
                    "groups": groups,
                    "new_notifications": 0,
                    "active_positions": len(state.get("active_positions", [])),
                    "closed_trades": len(state.get("closed_trades", [])),
                    "equity": state.get("equity", 1.0),
                    "errors": errors,
                    "source_mismatches": [
                        group["symbol"]
                        for group in groups
                        if group.get("status") == "SOURCE_MISMATCH"
                    ],
                    "require_futures_for_signals": self.config.require_futures_for_signals,
                }
                scan["v21_readiness"] = update_production_readiness(
                    state, scan, scan["time_utc"]
                )
                state["last_scan"] = scan
                self.store.save(state)
                return {**scan, "notifications": [], "state": state}

            active_after_replay: list[dict[str, Any]] = []
            for position in state.get("active_positions", []):
                try:
                    position_source = daily_sources[position["symbol"]]
                    if (
                        self.config.require_futures_for_signals
                        and position_source != SOURCE_USDM_FUTURES
                    ):
                        raise RuntimeError(
                            f"untrusted daily data source: {position_source}"
                        )
                    events, trade = self._replay_position(
                        state, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    notifications.extend(events)
                    if trade is None:
                        position["status"] = "OPEN"
                        active_after_replay.append(position)
                    else:
                        notifications.append({"event_id": f"{position['signal_id']}:CLOSED", "reason": "TRADE_CLOSED", "trade": trade})
                except Exception as exc:
                    errors.append(f"{position['symbol']} replay: {type(exc).__name__}: {exc}")
                    position["status"] = "DATA_GAP"
                    active_after_replay.append(position)
            state["active_positions"] = active_after_replay

            active_symbols = {position["symbol"] for position in active_after_replay}
            existing_signal_ids = {signal["signal_id"] for signal in state.get("signals", [])}
            pending: list[dict[str, Any]] = []
            for symbol in self.symbols:
                if symbol in active_symbols:
                    continue
                candidate = evaluate_candidate(symbol, daily_by_symbol, now_ms)
                if candidate is None or candidate["signal_id"] in existing_signal_ids:
                    continue
                _, current = completed_and_current(daily_by_symbol[symbol], now_ms)
                assert current is not None
                candidate["current_price"] = current.close
                candidate["detected_at_utc"] = utc_iso(now_ms)
                candidate["entry_window_seconds"] = self.config.max_entry_lag_seconds
                candidate["max_chase_bps"] = self.config.max_chase_bps
                candidate["data_sources"] = {
                    symbol: daily_sources[symbol],
                    "BTCUSDT": daily_sources["BTCUSDT"],
                }
                candidate["source_gate_passed"] = all(
                    source == SOURCE_USDM_FUTURES
                    for source in candidate["data_sources"].values()
                )
                lag_seconds = max(0.0, (now_ms - candidate["entry_time_ms"]) / 1000.0)
                direction = 1.0 if candidate["plan"]["side"] == "LONG" else -1.0
                chase_bps = direction * (current.close / candidate["plan"]["entry"] - 1.0) * 10_000.0
                candidate["entry_lag_seconds"] = lag_seconds
                candidate["directional_chase_bps"] = chase_bps
                if (
                    self.config.require_futures_for_signals
                    and not candidate["source_gate_passed"]
                ):
                    candidate["status"] = "SUPPRESSED_SOURCE_MISMATCH"
                    state.setdefault("signals", []).append(candidate)
                    notifications.append(candidate)
                elif errors:
                    candidate["status"] = "SUPPRESSED_DEGRADED"
                    state.setdefault("signals", []).append(candidate)
                    notifications.append(candidate)
                elif lag_seconds > self.config.max_entry_lag_seconds:
                    candidate["status"] = "SUPPRESSED_STALE"
                    state.setdefault("signals", []).append(candidate)
                    notifications.append(candidate)
                elif chase_bps > self.config.max_chase_bps:
                    candidate["status"] = "SUPPRESSED_CHASE"
                    state.setdefault("signals", []).append(candidate)
                    notifications.append(candidate)
                else:
                    pending.append(candidate)
                existing_signal_ids.add(candidate["signal_id"])

            marks = {
                symbol: completed_and_current(daily_by_symbol[symbol], now_ms)[1].close
                for symbol in self.symbols
                if completed_and_current(daily_by_symbol[symbol], now_ms)[1] is not None
            }
            unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in state["active_positions"]
                if position["symbol"] in marks
            )
            equity = float(state["cash"]) + unrealized
            existing_gross = sum(
                position["remaining_quantity"] * marks[position["symbol"]]
                for position in state["active_positions"]
                if position["symbol"] in marks
            ) / equity
            requested = sum(signal["notional_fraction_requested"] for signal in pending)
            classify_cross_market_candidates(pending)
            available = max(0.0, MAX_INITIAL_GROSS - existing_gross)
            scale = min(1.0, available / requested) if requested > 0.0 else 0.0
            for signal in pending:
                allocated = signal["notional_fraction_requested"] * scale
                signal["notional_fraction_allocated"] = allocated
                if allocated <= 0.0:
                    signal["status"] = "SUPPRESSED_CAPACITY"
                    state.setdefault("signals", []).append(signal)
                    notifications.append(signal)
                    continue
                signal["status"] = "OPEN"
                position = new_position(signal, equity, allocated)
                state["cash"] -= position["entry_fee"]
                state.setdefault("signals", []).append(signal)
                state["active_positions"].append(position)
                notifications.append(signal)
                try:
                    events, trade = self._replay_position(
                        state, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    notifications.extend(events)
                    if trade is not None:
                        state["active_positions"].remove(position)
                        notifications.append({"event_id": f"{position['signal_id']}:CLOSED", "reason": "TRADE_CLOSED", "trade": trade})
                except Exception as exc:
                    errors.append(f"{position['symbol']} initial replay: {type(exc).__name__}: {exc}")
                    position["status"] = "DATA_GAP"

            shadow = ensure_v17_state(state)
            shadow_active: list[dict[str, Any]] = []
            for position in shadow.get("active_positions", []):
                try:
                    position_source = daily_sources[position["symbol"]]
                    if (
                        self.config.require_futures_for_signals
                        and position_source != SOURCE_USDM_FUTURES
                    ):
                        raise RuntimeError(
                            f"untrusted daily data source: {position_source}"
                        )
                    events, trade = self._replay_position(
                        shadow, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    notifications.extend(events)
                    if trade is None:
                        position["status"] = "OPEN"
                        shadow_active.append(position)
                    else:
                        notifications.append(
                            {
                                "event_id": f"{position['signal_id']}:CLOSED",
                                "reason": "TRADE_CLOSED",
                                "trade": trade,
                            }
                        )
                except Exception as exc:
                    errors.append(
                        f"{position['symbol']} V17 replay: {type(exc).__name__}: {exc}"
                    )
                    position["status"] = "DATA_GAP"
                    shadow_active.append(position)
            shadow["active_positions"] = shadow_active

            v17_active_symbols = {
                position["symbol"] for position in shadow["active_positions"]
            }
            v17_raw = [
                candidate
                for symbol in self.symbols
                if symbol not in v17_active_symbols
                if (candidate := evaluate_v17_candidate(symbol, daily_by_symbol, now_ms))
                is not None
            ]
            v17_pending: list[dict[str, Any]] = []
            v17_existing_ids = {
                signal["signal_id"] for signal in shadow.get("signals", [])
            }
            for candidate in qualify_v17_candidates(v17_raw):
                symbol = candidate["plan"]["symbol"]
                if symbol in v17_active_symbols or candidate["signal_id"] in v17_existing_ids:
                    continue
                _, current = completed_and_current(daily_by_symbol[symbol], now_ms)
                assert current is not None
                candidate["current_price"] = current.close
                candidate["detected_at_utc"] = utc_iso(now_ms)
                candidate["entry_window_seconds"] = self.config.max_entry_lag_seconds
                candidate["max_chase_bps"] = self.config.max_chase_bps
                candidate["data_sources"] = {
                    peer: daily_sources[peer]
                    for peer in set(candidate["cross_market_symbols"] + ["BTCUSDT"])
                }
                candidate["source_gate_passed"] = all(
                    source == SOURCE_USDM_FUTURES
                    for source in candidate["data_sources"].values()
                )
                lag_seconds = max(
                    0.0, (now_ms - candidate["entry_time_ms"]) / 1000.0
                )
                direction = 1.0 if candidate["plan"]["side"] == "LONG" else -1.0
                chase_bps = direction * (
                    current.close / candidate["plan"]["entry"] - 1.0
                ) * 10_000.0
                candidate["entry_lag_seconds"] = lag_seconds
                candidate["directional_chase_bps"] = chase_bps
                if (
                    self.config.require_futures_for_signals
                    and not candidate["source_gate_passed"]
                ):
                    candidate["status"] = "SUPPRESSED_SOURCE_MISMATCH"
                    shadow["signals"].append(candidate)
                    notifications.append(candidate)
                elif errors:
                    candidate["status"] = "SUPPRESSED_DEGRADED"
                    shadow["signals"].append(candidate)
                    notifications.append(candidate)
                elif lag_seconds > self.config.max_entry_lag_seconds:
                    candidate["status"] = "SUPPRESSED_STALE"
                    shadow["signals"].append(candidate)
                    notifications.append(candidate)
                elif chase_bps > self.config.max_chase_bps:
                    candidate["status"] = "SUPPRESSED_CHASE"
                    shadow["signals"].append(candidate)
                    notifications.append(candidate)
                else:
                    v17_pending.append(candidate)
                v17_existing_ids.add(candidate["signal_id"])

            shadow_unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in shadow["active_positions"]
                if position["symbol"] in marks
            )
            shadow_equity = float(shadow["cash"]) + shadow_unrealized
            shadow_existing_gross = sum(
                position["remaining_quantity"] * marks[position["symbol"]]
                for position in shadow["active_positions"]
                if position["symbol"] in marks
            ) / shadow_equity
            shadow_requested = sum(
                signal["notional_fraction_requested"] for signal in v17_pending
            )
            shadow_available = max(
                0.0, V17_MAX_INITIAL_GROSS - shadow_existing_gross
            )
            shadow_scale = (
                min(1.0, shadow_available / shadow_requested)
                if shadow_requested > 0.0
                else 0.0
            )
            for signal in v17_pending:
                allocated = signal["notional_fraction_requested"] * shadow_scale
                signal["notional_fraction_allocated"] = allocated
                if allocated <= 0.0:
                    signal["status"] = "SUPPRESSED_CAPACITY"
                    shadow["signals"].append(signal)
                    notifications.append(signal)
                    continue
                signal["status"] = "OPEN"
                position = new_position(signal, shadow_equity, allocated)
                shadow["cash"] -= position["entry_fee"]
                shadow["signals"].append(signal)
                shadow["active_positions"].append(position)
                notifications.append(signal)
                try:
                    events, trade = self._replay_position(
                        shadow, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    notifications.extend(events)
                    if trade is not None:
                        shadow["active_positions"].remove(position)
                        notifications.append(
                            {
                                "event_id": f"{position['signal_id']}:CLOSED",
                                "reason": "TRADE_CLOSED",
                                "trade": trade,
                            }
                        )
                except Exception as exc:
                    errors.append(
                        f"{position['symbol']} V17 initial replay: {type(exc).__name__}: {exc}"
                    )
                    position["status"] = "DATA_GAP"

            shadow_unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in shadow["active_positions"]
                if position["symbol"] in marks
            )
            shadow["equity"] = float(shadow["cash"]) + shadow_unrealized
            shadow["signals"] = shadow.get("signals", [])[-500:]
            shadow["closed_trades"] = shadow.get("closed_trades", [])[-500:]
            shadow["events"] = shadow.get("events", [])[-1000:]

            v20 = ensure_v20_state(state)
            v20_active: list[dict[str, Any]] = []
            for position in v20.get("active_positions", []):
                try:
                    position_source = daily_sources[position["symbol"]]
                    if (
                        self.config.require_futures_for_signals
                        and position_source != SOURCE_USDM_FUTURES
                    ):
                        raise RuntimeError(
                            f"untrusted daily data source: {position_source}"
                        )
                    events, trade = self._replay_position(
                        v20, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    notifications.extend(events)
                    if trade is None:
                        position["status"] = "OPEN"
                        v20_active.append(position)
                    else:
                        notifications.append(
                            {
                                "event_id": f"{position['signal_id']}:CLOSED",
                                "reason": "TRADE_CLOSED",
                                "trade": trade,
                            }
                        )
                except Exception as exc:
                    errors.append(
                        f"{position['symbol']} V20 replay: {type(exc).__name__}: {exc}"
                    )
                    position["status"] = "DATA_GAP"
                    v20_active.append(position)
            v20["active_positions"] = v20_active

            v22 = ensure_v22_state(state)
            v22_active: list[dict[str, Any]] = []
            for position in v22.get("active_positions", []):
                try:
                    position_source = daily_sources[position["symbol"]]
                    if (
                        self.config.require_futures_for_signals
                        and position_source != SOURCE_USDM_FUTURES
                    ):
                        raise RuntimeError(
                            f"untrusted daily data source: {position_source}"
                        )
                    _, trade = self._replay_position(
                        v22, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    if trade is None:
                        position["status"] = "OPEN"
                        v22_active.append(position)
                except Exception as exc:
                    v22_errors.append(
                        f"{position['symbol']} V22 replay: {type(exc).__name__}: {exc}"
                    )
                    position["status"] = "DATA_GAP"
                    v22_active.append(position)
            v22["active_positions"] = v22_active

            v20_active_keys = {
                (
                    str(position.get("sleeve", "CORE")),
                    str(position["symbol"]),
                )
                for position in v20["active_positions"]
            }
            v20_raw, v20_context = build_v20_candidates(
                daily_by_symbol,
                now_ms,
                symbols=self.symbols,
                active_keys=v20_active_keys,
            )
            v20["last_regime_context"] = v20_context
            v20_pending: list[dict[str, Any]] = []
            v20_existing_ids = {
                signal["signal_id"] for signal in v20.get("signals", [])
            }
            for candidate in v20_raw:
                key = (str(candidate.get("sleeve", "CORE")), candidate["plan"]["symbol"])
                if key in v20_active_keys or candidate["signal_id"] in v20_existing_ids:
                    continue
                symbol = candidate["plan"]["symbol"]
                _, current = completed_and_current(daily_by_symbol[symbol], now_ms)
                assert current is not None
                candidate["current_price"] = current.close
                candidate["detected_at_utc"] = utc_iso(now_ms)
                candidate["entry_window_seconds"] = self.config.max_entry_lag_seconds
                candidate["max_chase_bps"] = self.config.max_chase_bps
                candidate["data_sources"] = {
                    peer: daily_sources[peer]
                    for peer in set(
                        candidate.get("cross_market_symbols", [symbol])
                        + ["BTCUSDT"]
                    )
                }
                candidate["source_gate_passed"] = all(
                    source == SOURCE_USDM_FUTURES
                    for source in candidate["data_sources"].values()
                )
                lag_seconds = max(
                    0.0, (now_ms - candidate["entry_time_ms"]) / 1000.0
                )
                direction = 1.0 if candidate["plan"]["side"] == "LONG" else -1.0
                chase_bps = direction * (
                    current.close / candidate["plan"]["entry"] - 1.0
                ) * 10_000.0
                candidate["entry_lag_seconds"] = lag_seconds
                candidate["directional_chase_bps"] = chase_bps
                if (
                    self.config.require_futures_for_signals
                    and not candidate["source_gate_passed"]
                ):
                    candidate["status"] = "SUPPRESSED_SOURCE_MISMATCH"
                    v20["signals"].append(candidate)
                    notifications.append(candidate)
                elif errors:
                    candidate["status"] = "SUPPRESSED_DEGRADED"
                    v20["signals"].append(candidate)
                    notifications.append(candidate)
                elif lag_seconds > self.config.max_entry_lag_seconds:
                    candidate["status"] = "SUPPRESSED_STALE"
                    v20["signals"].append(candidate)
                    notifications.append(candidate)
                elif chase_bps > self.config.max_chase_bps:
                    candidate["status"] = "SUPPRESSED_CHASE"
                    v20["signals"].append(candidate)
                    notifications.append(candidate)
                else:
                    v20_pending.append(candidate)
                v20_existing_ids.add(candidate["signal_id"])

            v20_unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in v20["active_positions"]
                if position["symbol"] in marks
            )
            v20_equity = float(v20["cash"]) + v20_unrealized
            v20_existing_gross = sum(
                position["remaining_quantity"] * marks[position["symbol"]]
                for position in v20["active_positions"]
                if position["symbol"] in marks
            ) / v20_equity
            v20_requested = sum(
                signal["notional_fraction_requested"] for signal in v20_pending
            )
            v20_available = max(
                0.0, V20_MAX_INITIAL_GROSS - v20_existing_gross
            )
            v20_scale = (
                min(1.0, v20_available / v20_requested)
                if v20_requested > 0.0
                else 0.0
            )
            v22_pending: list[dict[str, Any]] = []
            for signal in v20_pending:
                allocated = signal["notional_fraction_requested"] * v20_scale
                signal["notional_fraction_allocated"] = allocated
                if allocated <= 0.0:
                    signal["status"] = "SUPPRESSED_CAPACITY"
                    v20["signals"].append(signal)
                    notifications.append(signal)
                    continue
                signal["status"] = "OPEN"
                position = new_position(signal, v20_equity, allocated)
                v20["cash"] -= position["entry_fee"]
                v20["signals"].append(signal)
                v20["active_positions"].append(position)
                notifications.append(signal)
                try:
                    quote, quote_source = self._book_ticker(position["symbol"])
                    if quote_source != SOURCE_USDM_FUTURES:
                        raise RuntimeError(f"untrusted book ticker source: {quote_source}")
                    execution_signal = build_v22_execution_signal(
                        signal, quote, quote_source, now_ms
                    )
                    v22_pending.append(execution_signal)
                    v22.setdefault("execution_samples", []).append(
                        {
                            "origin_v20_signal_id": signal["signal_id"],
                            "v22_signal_id": execution_signal["signal_id"],
                            "symbol": position["symbol"],
                            "side": position["side"],
                            **execution_signal["execution_truth"],
                        }
                    )
                    v22["last_quote_error"] = None
                except Exception as exc:
                    message = f"{position['symbol']} V22 quote: {type(exc).__name__}: {exc}"
                    v22_errors.append(message)
                    v22["last_quote_error"] = message
                    v22.setdefault("execution_samples", []).append(
                        {
                            "origin_v20_signal_id": signal["signal_id"],
                            "symbol": position["symbol"],
                            "side": position["side"],
                            "status": "QUOTE_ERROR",
                            "captured_at_ms": now_ms,
                            "captured_at_utc": utc_iso(now_ms),
                            "error": message,
                        }
                    )
                try:
                    events, trade = self._replay_position(
                        v20, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    notifications.extend(events)
                    if trade is not None:
                        v20["active_positions"].remove(position)
                        notifications.append(
                            {
                                "event_id": f"{position['signal_id']}:CLOSED",
                                "reason": "TRADE_CLOSED",
                                "trade": trade,
                            }
                        )
                except Exception as exc:
                    errors.append(
                        f"{position['symbol']} V20 initial replay: {type(exc).__name__}: {exc}"
                    )
                    position["status"] = "DATA_GAP"

            v22_unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in v22["active_positions"]
                if position["symbol"] in marks
            )
            v22_equity = float(v22["cash"]) + v22_unrealized
            v22_existing_gross = sum(
                position["remaining_quantity"] * marks[position["symbol"]]
                for position in v22["active_positions"]
                if position["symbol"] in marks
            ) / v22_equity
            v22_requested = sum(
                signal["notional_fraction_requested"] for signal in v22_pending
            )
            v22_available = max(
                0.0, V20_MAX_INITIAL_GROSS - v22_existing_gross
            )
            v22_scale = (
                min(1.0, v22_available / v22_requested)
                if v22_requested > 0.0
                else 0.0
            )
            for signal in v22_pending:
                allocated = signal["notional_fraction_requested"] * v22_scale
                signal["notional_fraction_allocated"] = allocated
                if allocated <= 0.0:
                    signal["status"] = "SUPPRESSED_CAPACITY"
                    v22["signals"].append(signal)
                    continue
                signal["status"] = "OPEN"
                position = new_position(signal, v22_equity, allocated)
                v22["cash"] -= position["entry_fee"]
                v22["signals"].append(signal)
                v22["active_positions"].append(position)
                try:
                    _, trade = self._replay_position(
                        v22, position, daily_by_symbol[position["symbol"]], now_ms
                    )
                    if trade is not None:
                        v22["active_positions"].remove(position)
                except Exception as exc:
                    v22_errors.append(
                        f"{position['symbol']} V22 initial replay: {type(exc).__name__}: {exc}"
                    )
                    position["status"] = "DATA_GAP"

            v20_unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in v20["active_positions"]
                if position["symbol"] in marks
            )
            v20["equity"] = float(v20["cash"]) + v20_unrealized
            v20["signals"] = v20.get("signals", [])[-1000:]
            v20["closed_trades"] = v20.get("closed_trades", [])[-1000:]
            v20["events"] = v20.get("events", [])[-2000:]

            v22_unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in v22["active_positions"]
                if position["symbol"] in marks
            )
            v22["equity"] = float(v22["cash"]) + v22_unrealized
            v22["signals"] = v22.get("signals", [])[-1000:]
            v22["closed_trades"] = v22.get("closed_trades", [])[-1000:]
            v22["events"] = v22.get("events", [])[-2000:]
            v22["execution_samples"] = v22.get("execution_samples", [])[-2000:]

            v24 = ensure_v24_state(state)
            v24_boundary = v24_due_boundary(now_ms)
            if (
                v24_boundary is not None
                and v24.get("last_evaluated_boundary_ms") != v24_boundary
            ):
                try:
                    four_hour_by_symbol: dict[str, list[Candle]] = {}
                    four_hour_sources: dict[str, str] = {}
                    for symbol in self.symbols:
                        candles, source = self._four_hour_candles(symbol)
                        four_hour_by_symbol[symbol] = candles
                        four_hour_sources[symbol] = source
                    if any(
                        source != SOURCE_USDM_FUTURES
                        for source in four_hour_sources.values()
                    ):
                        raise RuntimeError("untrusted four-hour data source")
                    items, new_keys = build_v24_watch_items(
                        daily_by_symbol,
                        four_hour_by_symbol,
                        now_ms,
                        set(v24.get("seen_keys", [])),
                    )
                    v24["last_evaluated_boundary_ms"] = v24_boundary
                    v24["last_evaluated_utc"] = utc_iso(now_ms)
                    v24["last_status"] = (
                        "ALERT" if items else "NO_EARLY_CROSS"
                    )
                    v24["last_error"] = None
                    if items:
                        event = {
                            "event_id": f"CORE4V24:WATCH:{v24_boundary}",
                            "reason": "V24_EARLY_WATCH",
                            "strategy_id": V24_STRATEGY_ID,
                            "status": "WATCH_ONLY",
                            "signal_close_utc": items[0]["signal_close_utc"],
                            "daily_confirmation_utc": items[0][
                                "daily_confirmation_utc"
                            ],
                            "lead_hours": items[0]["lead_hours"],
                            "items": items,
                            "paper_only": True,
                        }
                        v24.setdefault("alerts", []).append(event)
                        v24["seen_keys"] = sorted(
                            set(v24.get("seen_keys", [])) | new_keys
                        )[-2000:]
                        notifications.append(event)
                except Exception as exc:
                    v24["last_status"] = "DATA_ERROR"
                    v24["last_error"] = f"{type(exc).__name__}: {exc}"
            v24["alerts"] = v24.get("alerts", [])[-500:]

            unrealized = sum(
                _direction(position)
                * position["remaining_quantity"]
                * (marks[position["symbol"]] - position["entry"])
                for position in state["active_positions"]
                if position["symbol"] in marks
            )
            state["equity"] = float(state["cash"]) + unrealized
            state["last_scan_utc"] = utc_iso(now_ms)
            state["scan_count"] = int(state.get("scan_count", 0)) + 1
            state["signals"] = state.get("signals", [])[-500:]
            state["closed_trades"] = state.get("closed_trades", [])[-500:]
            state["events"] = state.get("events", [])[-1000:]
            if errors:
                state["errors"] = (state.get("errors", []) + [{"time_utc": now_iso(), "message": item} for item in errors])[-50:]
            source_mismatches = [
                group["symbol"]
                for group in groups
                if group.get("status") == "SOURCE_MISMATCH"
            ]
            status = "OK" if not errors and not source_mismatches else "DEGRADED"
            if status == "OK":
                readiness_event = update_readiness_state(state, groups, now_ms)
                if readiness_event is not None:
                    notifications.append(readiness_event)
            scan = {
                "status": status,
                "time_utc": utc_iso(now_ms),
                "groups": groups,
                "new_notifications": len(notifications),
                "active_positions": len(state["active_positions"]),
                "closed_trades": len(state["closed_trades"]),
                "equity": state["equity"],
                "v17_shadow_active": len(shadow["active_positions"]),
                "v17_shadow_closed": len(shadow["closed_trades"]),
                "v17_shadow_equity": shadow["equity"],
                "v20_shadow_active": len(v20["active_positions"]),
                "v20_shadow_closed": len(v20["closed_trades"]),
                "v20_shadow_equity": v20["equity"],
                "v20_regime_context": v20.get("last_regime_context", {}),
                "v22_execution_active": len(v22["active_positions"]),
                "v22_execution_closed": len(v22["closed_trades"]),
                "v22_execution_equity": v22["equity"],
                "v22_execution_samples": len(v22.get("execution_samples", [])),
                "v22_errors": v22_errors,
                "v24_watch_alerts": len(v24.get("alerts", [])),
                "v24_watch_status": v24.get("last_status"),
                "v24_watch_error": v24.get("last_error"),
                "errors": errors,
                "source_mismatches": source_mismatches,
                "require_futures_for_signals": self.config.require_futures_for_signals,
            }
            scan["v21_readiness"] = update_production_readiness(
                state, scan, scan["time_utc"]
            )
            state["last_scan"] = scan
            self.store.save(state)
            return {**scan, "notifications": notifications, "state": state}
