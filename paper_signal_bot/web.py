from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .binance_client import BinanceFuturesClient
from .crowding_confirmation import (
    ENGINE_ID as CROWDING_ID,
    crowding_forward_summary,
    evaluate_crowding_confirmation,
    required_history_start_ms,
)
from .hyperliquid_client import HyperliquidClient
from .liquidity_intel import LIQUIDITY_ID, LIQUIDITY_INTERVALS, LIQUIDITY_MARKETS, LIQUIDITY_SYMBOLS, evaluate_liquidity_intel, max_liquidity_event_lag_seconds
from .macro_events import MACRO_ID, evaluate_macro_calendar, fetch_macro_calendar
from .storage import JsonStore
from .market_pulse import MAX_PULSE_LAG_SECONDS, PULSE_ID, PULSE_MARKETS, THRESHOLDS, evaluate_market_pulses
from .quality_gate import DEFAULT_POLICY, GATE_ID, classify_signal, evaluate_trade_readiness
from .strategy import PORTFOLIO_ID, PORTFOLIO_METRICS, PORTFOLIO_NAME, R26A_CONTEXT_MARKETS, R26A_SCAN_MARKETS, STRATEGY_ID, candidate_groups, evaluate_latest
from .telegram import (
    TelegramSender,
    env_enabled,
    format_heartbeat_message,
    format_macro_digest_message,
    format_signal_message,
    format_startup_message,
    format_market_pulse_message,
    format_liquidity_event_message,
    format_macro_event_message,
    telegram_configured,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


NOTIFY_LOCK = threading.RLock()
LAST_HEARTBEAT_SENT = 0.0
DEFAULT_SCAN_INTERVAL_SECONDS = 60
DEFAULT_MAX_SIGNAL_ENTRY_LAG_SECONDS = 600
DEFAULT_MAX_SIGNAL_CHASE_BPS = 40.0
DEFAULT_MAX_MARKET_PULSE_EVENTS_PER_SCAN = 8
DEFAULT_MAX_LIQUIDITY_EVENTS_PER_SCAN = 4
DEFAULT_MAX_MACRO_EVENTS_PER_SCAN = 6
DEFAULT_MACRO_CALENDAR_CACHE_SECONDS = 3600


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(value, minimum)
    return value


def env_float(name: str, default: float, minimum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(value, minimum)
    return value


def effective_scan_interval_seconds() -> int:
    configured = env_int("SCAN_INTERVAL_SECONDS", DEFAULT_SCAN_INTERVAL_SECONDS, 15)
    max_interval = env_int("MAX_INTERNAL_SCAN_INTERVAL_SECONDS", DEFAULT_SCAN_INTERVAL_SECONDS, 15)
    return min(configured, max_interval)


def effective_market_pulse_events_per_scan() -> int:
    return env_int(
        "MAX_MARKET_PULSE_EVENTS_PER_SCAN",
        DEFAULT_MAX_MARKET_PULSE_EVENTS_PER_SCAN,
        1,
    )


def effective_liquidity_events_per_scan() -> int:
    return env_int(
        "MAX_LIQUIDITY_EVENTS_PER_SCAN",
        DEFAULT_MAX_LIQUIDITY_EVENTS_PER_SCAN,
        1,
    )


def effective_macro_events_per_scan() -> int:
    return env_int(
        "MAX_MACRO_EVENTS_PER_SCAN",
        DEFAULT_MAX_MACRO_EVENTS_PER_SCAN,
        1,
    )


def settle_due_positions(
    active_positions: list[dict[str, Any]],
    klines_cache: dict[tuple[str, str], list[list[Any]]],
    *,
    at_ms: int,
) -> list[dict[str, Any]]:
    closed: list[dict[str, Any]] = []
    for position in active_positions:
        exit_time_ms = int(position.get("planned_exit_time_ms", 0))
        if not exit_time_ms or exit_time_ms > at_ms:
            continue
        rows = klines_cache.get((str(position.get("symbol")), str(position.get("timeframe"))), [])
        exit_row = next((row for row in rows if int(row[0]) == exit_time_ms), None)
        if exit_row is None:
            continue
        entry_price = float(position.get("entry_price") or 0.0)
        exit_price = float(exit_row[1])
        if not math.isfinite(entry_price) or not math.isfinite(exit_price) or entry_price <= 0.0 or exit_price <= 0.0:
            continue
        side_multiplier = -1.0 if str(position.get("side", "")).upper() == "SHORT" else 1.0
        gross_return = side_multiplier * (exit_price / entry_price - 1.0)
        update = dict(position)
        update.update(
            {
                "status": "PAPER_CLOSED",
                "actual_exit_time_ms": exit_time_ms,
                "actual_exit_time_utc": iso_from_ms(exit_time_ms),
                "actual_exit_price": exit_price,
                "exit_price_source": "PLANNED_EXIT_CANDLE_OPEN",
                "settled_utc": iso_from_ms(at_ms),
                "settlement_lag_seconds": max((at_ms - exit_time_ms) / 1000.0, 0.0),
                "gross_return": gross_return,
                "net_return_12bps": gross_return - 0.0012,
                "net_return_20bps": gross_return - 0.0020,
                "paper_win_12bps": gross_return - 0.0012 > 0.0,
            }
        )
        closed.append(update)
    return closed


def state_with_closed_signals(
    state: dict[str, Any],
    closed_signals: list[dict[str, Any]],
    *,
    increment_scan_count: bool = False,
) -> dict[str, Any]:
    closed_by_id = {item.get("signal_id"): item for item in closed_signals}
    merged = dict(state)
    merged["signals"] = [
        closed_by_id.get(item.get("signal_id"), item)
        for item in state.get("signals", [])
    ]
    merged["scan_count"] = int(state.get("scan_count", 0)) + (1 if increment_scan_count else 0)
    return merged


def compact_scan(scan_result: dict[str, Any]) -> dict[str, Any]:
    scan = scan_result.get("scan", {})
    groups = []
    for group in scan.get("groups", []):
        features = group.get("features", {})
        group_signals = group.get("signals", [])
        suppressed_reasons = [
            str(signal.get("suppressed_reason"))
            for signal in group_signals
            if signal.get("suppressed_reason")
        ]
        live_signal_count = sum(1 for signal in group_signals if signal.get("signal_id") and not signal.get("suppressed_reason"))
        status = group.get("status")
        if str(status).upper() == "SIGNAL" and live_signal_count == 0 and suppressed_reasons:
            status = "SUPPRESSED"
        groups.append(
            {
                "symbol": group.get("symbol"),
                "timeframe": group.get("timeframe"),
                "status": status,
                "latest_closed_bar_utc": group.get("latest_closed_bar_utc"),
                "latest_candle_close_utc": group.get("latest_candle_close_utc"),
                "data_state": group.get("data_state", "UNKNOWN"),
                "raw_signal_count": group.get("raw_signal_count", 0),
                "suppressed_signal_count": len(suppressed_reasons),
                "suppressed_reasons": sorted(set(suppressed_reasons)),
                "candidate_count": group.get("candidate_count", 0),
                "quote_volume_prior_z_20": features.get("quote_volume_prior_z_20"),
                "market_breadth_count": features.get("market_breadth_count"),
                "market_breadth_assets": features.get("market_breadth_assets"),
                "market_directional_mean": features.get("market_directional_mean"),
                "breadth_n": features.get("breadth_n"),
                "breadth_min": features.get("breadth_min"),
                "premium_close_prior_z_24": features.get("premium_close_prior_z_24"),
                "realized_vol_24": features.get("realized_vol_24"),
                "quote_imbalance": features.get("quote_imbalance"),
                "taker_buy_quote_ratio": features.get("taker_buy_quote_ratio"),
                "flow_directional": features.get("flow_directional"),
                "flow_thr": features.get("flow_thr"),
                "quality_realized_vol_24_min": features.get("quality_realized_vol_24_min"),
                "full_derivatives_state_available": features.get("full_derivatives_state_available"),
                "premium_error": group.get("premium_error"),
                "derivatives_error": group.get("derivatives_error"),
                "error": group.get("error"),
            }
        )
    state = scan_result.get("state", {})
    return {
        "event": "paper_scan",
        "time_utc": scan.get("completed_utc", scan.get("scanned_utc", now_iso())),
        "strategy_id": STRATEGY_ID,
        "paper_only": True,
        "new_signal_count": scan.get("new_signal_count", 0),
        "new_watch_signal_count": scan.get("new_watch_signal_count", 0),
        "new_trade_a_plus_count": scan.get("new_trade_a_plus_count", 0),
        "closed_signal_count": scan.get("closed_signal_count", 0),
        "suppressed_signal_count": scan.get("suppressed_signal_count", 0),
        "active_position_count": len(state.get("active_positions", [])),
        "new_market_event_count": scan.get("new_market_event_count", 0),
        "new_pulse_event_count": scan.get("new_pulse_event_count", 0),
        "new_liquidity_event_count": scan.get("new_liquidity_event_count", 0),
        "new_macro_event_count": scan.get("new_macro_event_count", 0),
        "pulse_groups": scan.get("pulse_groups", []),
        "liquidity_groups": scan.get("liquidity_groups", []),
        "macro_groups": scan.get("macro_groups", []),
        "macro_calendar": scan.get("macro_calendar", []),
        "scan_duration_seconds": scan.get("scan_duration_seconds"),
        "scan_gap_seconds": scan.get("scan_gap_seconds"),
        "trade_readiness": scan.get("trade_readiness", {}),
        "crowding_forward": scan.get("crowding_forward", {}),
        "report_metrics": PORTFOLIO_METRICS,
        "groups": groups,
    }


def notifiable_signals(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    scan = scan_result.get("scan", {})
    new_signals = scan.get("new_signals")
    if isinstance(new_signals, list):
        return new_signals

    items: list[dict[str, Any]] = []
    for group in scan.get("groups", []):
        for item in group.get("signals", []):
            if item.get("signal_id") and not item.get("suppressed_reason"):
                items.append(item)
    return items


class SignalService:
    def __init__(self) -> None:
        self.client = BinanceFuturesClient()
        self.hyperliquid_client = HyperliquidClient()
        self.store = JsonStore()
        self.lock = threading.Lock()
        self.groups = candidate_groups()
        self.context_markets = tuple(
            dict.fromkeys((*R26A_CONTEXT_MARKETS, *PULSE_MARKETS, *LIQUIDITY_MARKETS))
        )
        self.session_floor_ms: int | None = None
        self.liquidity_enabled = env_enabled("LIQUIDITY_INTEL_ENABLED", "true")
        self.macro_enabled = env_enabled("MACRO_EVENT_WATCH_ENABLED", "true")
        self.crowding_enabled = env_enabled("CROWDING_CONFIRMATION_ENABLED", "true")
        self._macro_calendar_cache: dict[str, Any] | None = None
        self._macro_calendar_cache_until_ms = 0

    def durable_state_configured(self) -> bool:
        path = self.store.path
        if os.getenv("RENDER"):
            return path.is_absolute() and path.as_posix().startswith("/var/data/")
        return path.is_absolute()

    def current_trade_readiness(
        self,
        *,
        at_ms: int,
        closed_signals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        source = state_with_closed_signals(self.store.load(), closed_signals or [])
        source["durable_state_configured"] = self.durable_state_configured()
        return evaluate_trade_readiness(source, at_ms=at_ms)

    def macro_calendar(self, at_ms: int) -> dict[str, Any]:
        ttl_seconds = env_int("MACRO_CALENDAR_CACHE_SECONDS", DEFAULT_MACRO_CALENDAR_CACHE_SECONDS, 300)
        if self._macro_calendar_cache is not None and at_ms < self._macro_calendar_cache_until_ms:
            return self._macro_calendar_cache
        calendar = fetch_macro_calendar(at_ms)
        self._macro_calendar_cache = calendar
        self._macro_calendar_cache_until_ms = at_ms + ttl_seconds * 1000
        return calendar

    def crowding_confirmation(self, signal: dict[str, Any]) -> dict[str, Any]:
        entry_time_ms = int(signal["entry_time_ms"])
        start_time_ms = required_history_start_ms(entry_time_ms)
        premium = self.client.premium_index_klines_range(
            str(signal["symbol"]),
            "1h",
            start_time_ms,
            entry_time_ms - 1,
        )
        funding = self.client.funding_rate_history(
            str(signal["symbol"]),
            start_time_ms,
            entry_time_ms,
        )
        return evaluate_crowding_confirmation(
            symbol=str(signal["symbol"]),
            timeframe=str(signal["timeframe"]),
            side=str(signal["side"]),
            entry_time_ms=entry_time_ms,
            funding_rows=funding,
            premium_rows=premium,
        )

    @staticmethod
    def active_assets_from_state(state: dict[str, Any], at_ms: int | None = None) -> set[str]:
        active = state.get("active_positions", [])
        return {
            str(item.get("asset"))
            for item in active
            if item.get("asset") and item.get("status") != "PAPER_CLOSED"
        }

    @staticmethod
    def existing_signal_ids_from_state(state: dict[str, Any]) -> set[str]:
        return {
            str(item.get("signal_id"))
            for item in state.get("signals", [])
            if item.get("signal_id")
        }

    def active_assets(self) -> set[str]:
        return self.active_assets_from_state(self.store.load())

    def scan_once(self, now_ms_override: int | None = None) -> dict[str, Any]:
        with self.lock:
            scan_started_ms = now_ms_override if now_ms_override is not None else now_ms()
            started_monotonic = time.monotonic()
            scan_started_utc = iso_from_ms(scan_started_ms)
            max_entry_lag_seconds = env_int("MAX_SIGNAL_ENTRY_LAG_SECONDS", DEFAULT_MAX_SIGNAL_ENTRY_LAG_SECONDS, 0)
            max_chase_bps = env_float("MAX_SIGNAL_CHASE_BPS", DEFAULT_MAX_SIGNAL_CHASE_BPS, 0.0)
            results: list[dict[str, Any]] = []
            signals: list[dict[str, Any]] = []
            suppressed_signals: list[dict[str, Any]] = []
            state_before = self.store.load()
            if self.session_floor_ms is None:
                self.session_floor_ms = scan_started_ms if now_ms_override is None and not state_before.get("scan_count") else 0
            existing_signal_ids = self.existing_signal_ids_from_state(state_before)
            klines_cache: dict[tuple[str, str], list[list[Any]]] = {}
            premium_cache: dict[tuple[str, str], list[list[Any]]] = {}
            fetch_errors: dict[tuple[str, str], str] = {}
            # Only klines affect R26A decisions. Diagnostic endpoints must not delay alerts.
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs = {pool.submit(self.client.klines, symbol, tf, 220): (symbol, tf) for symbol, tf in self.context_markets}
                for future in as_completed(jobs):
                    key = jobs[future]
                    try:
                        klines_cache[key] = future.result()
                    except Exception as exc:
                        fetch_errors[key] = str(exc)
                        self.store.record_error(f"{key[0]} {key[1]}: {exc}")
            evaluated_ms = scan_started_ms if now_ms_override is not None else now_ms()
            closed_signals = settle_due_positions(
                state_before.get("active_positions", []),
                klines_cache,
                at_ms=evaluated_ms,
            )
            closed_ids = {item.get("signal_id") for item in closed_signals}
            active_state = dict(state_before)
            active_state["active_positions"] = [
                item
                for item in state_before.get("active_positions", [])
                if item.get("signal_id") not in closed_ids
            ]
            active_assets = self.active_assets_from_state(active_state, evaluated_ms)
            readiness_source = state_with_closed_signals(
                state_before,
                closed_signals,
                increment_scan_count=True,
            )
            readiness_source["durable_state_configured"] = self.durable_state_configured()
            trade_readiness = evaluate_trade_readiness(readiness_source, at_ms=evaluated_ms)
            pulse = evaluate_market_pulses(klines_cache, scan_started_ms)
            liquidity = {"engine_id": LIQUIDITY_ID, "events": [], "groups": []}
            if self.liquidity_enabled:
                depth_cache: dict[str, dict[str, Any]] = {}
                open_interest_cache: dict[str, list[dict[str, Any]]] = {}
                hyperliquid_cache: dict[str, dict[str, Any]] = {}
                liquidity_errors: dict[tuple[str, str], str] = {}
                with ThreadPoolExecutor(max_workers=6) as pool:
                    liquidity_jobs = {}
                    for symbol in LIQUIDITY_SYMBOLS:
                        asset = symbol.replace("USDT", "")
                        liquidity_jobs[pool.submit(self.client.depth, symbol, 100)] = ("depth", symbol)
                        liquidity_jobs[pool.submit(self.client.open_interest_hist, symbol, "5m", 30)] = ("open_interest", symbol)
                        liquidity_jobs[pool.submit(self.hyperliquid_client.l2_book, asset, 5)] = ("hyperliquid", symbol)
                    for future in as_completed(liquidity_jobs):
                        kind, symbol = liquidity_jobs[future]
                        try:
                            value = future.result()
                            if kind == "depth":
                                depth_cache[symbol] = value
                            elif kind == "open_interest":
                                open_interest_cache[symbol] = value
                            else:
                                hyperliquid_cache[symbol] = value
                        except Exception as exc:
                            liquidity_errors[("liquidity", symbol)] = f"{kind}: {exc}"
                            self.store.record_error(f"liquidity {symbol} {kind}: {exc}")
                liquidity = evaluate_liquidity_intel(
                    klines_cache=klines_cache,
                    depth_cache=depth_cache,
                    open_interest_cache=open_interest_cache,
                    hyperliquid_cache=hyperliquid_cache,
                    now_ms=scan_started_ms,
                    errors=liquidity_errors,
                )
            macro = {"engine_id": MACRO_ID, "events": [], "groups": [], "calendar": [], "source_states": []}
            if self.macro_enabled:
                try:
                    macro = evaluate_macro_calendar(self.macro_calendar(scan_started_ms), scan_started_ms)
                except Exception as exc:
                    self.store.record_error(f"macro watch: {exc}")
                    macro = {
                        "engine_id": MACRO_ID,
                        "events": [],
                        "groups": [
                            {
                                "status": "ERROR",
                                "data_state": "DEGRADED",
                                "candidate_count": 0,
                                "upcoming_count": 0,
                                "alert_count": 0,
                                "error": type(exc).__name__,
                            }
                        ],
                        "calendar": [],
                        "source_states": [],
                    }
            snapshots = {(s["symbol"], s["timeframe"]): s for s in pulse["groups"]}
            for (symbol, timeframe), _candidates in self.groups.items():
                try:
                    if (symbol, timeframe) not in klines_cache:
                        raise RuntimeError(fetch_errors.get((symbol, timeframe), "kline_fetch_failed"))
                    klines = klines_cache[(symbol, timeframe)]
                    premium = premium_cache.get((symbol, timeframe), [])
                    premium_error = None
                    existing_fetch_error = fetch_errors.get((symbol, timeframe))
                    if existing_fetch_error and existing_fetch_error.startswith("premium: "):
                        premium_error = existing_fetch_error.removeprefix("premium: ")
                    derivatives_error = None
                    derivatives_ok = False
                    market_klines_by_symbol = {
                        market_symbol: rows
                        for (market_symbol, market_timeframe), rows in klines_cache.items()
                        if market_timeframe == timeframe
                    }
                    result = evaluate_latest(
                        symbol=symbol,
                        timeframe=timeframe,
                        klines=klines,
                        premium_klines=premium,
                        derivatives_state_available=derivatives_ok,
                        market_klines_by_symbol=market_klines_by_symbol,
                        now_ms=scan_started_ms,
                    )
                    snapshot = snapshots[(symbol, timeframe)]
                    result["data_state"] = snapshot["data_state"]
                    result["diagnostics_status"] = "NOT_REQUESTED"
                    if snapshot["status"] in {"STALE_DATA", "DATA_GAP", "INVALID_DATA", "INSUFFICIENT_HISTORY"}:
                        result["status"] = snapshot["status"]
                        result["signals"] = []
                    if premium_error is not None:
                        result["premium_error"] = premium_error
                    if derivatives_error is not None:
                        result["derivatives_error"] = derivatives_error
                    for signal in result.get("signals", []):
                        signal_id = "{strategy}:{symbol}:{timeframe}:{candidate}:{time}".format(
                            strategy=STRATEGY_ID,
                            symbol=signal["symbol"],
                            timeframe=signal["timeframe"],
                            candidate=signal["candidate"]["candidate_id"],
                            time=signal["signal_time_ms"],
                        )
                        signal["signal_id"] = signal_id
                        if signal_id in existing_signal_ids:
                            signal["suppressed_reason"] = "DUPLICATE_SIGNAL_ID"
                            suppressed_signals.append(signal)
                            continue
                        signal["created_utc"] = scan_started_utc
                        signal["notify_time_utc"] = iso_from_ms(evaluated_ms)
                        entry_lag_seconds = max((evaluated_ms - int(signal.get("entry_time_ms", scan_started_ms))) / 1000.0, 0.0)
                        signal["entry_lag_seconds"] = entry_lag_seconds
                        signal["max_entry_lag_seconds"] = max_entry_lag_seconds
                        signal["max_chase_bps"] = max_chase_bps
                        signal["signal_tier"] = classify_signal(trade_readiness, signal)
                        signal["trade_readiness_status"] = trade_readiness["status"]
                        signal["trade_readiness_gate_id"] = trade_readiness["gate_id"]
                        signal["real_money_authorized"] = False
                        if signal["entry_time_ms"] < self.session_floor_ms:
                            signal["suppressed_reason"] = "STARTUP_HISTORICAL_SIGNAL"
                            suppressed_signals.append(signal)
                            continue
                        if not signal.get("entry_price"):
                            signal["suppressed_reason"] = "MISSING_ENTRY_PRICE"
                            suppressed_signals.append(signal)
                            continue
                        if entry_lag_seconds > max_entry_lag_seconds:
                            signal["suppressed_reason"] = "STALE_ENTRY"
                            signal["status"] = "SUPPRESSED_STALE_ENTRY"
                            suppressed_signals.append(signal)
                            continue
                        chase_bps = signal.get("entry_price_move_bps")
                        if chase_bps is not None and float(chase_bps) > max_chase_bps:
                            signal["suppressed_reason"] = "CHASE_PRICE_TOO_FAR"
                            signal["status"] = "SUPPRESSED_CHASE_PRICE_TOO_FAR"
                            suppressed_signals.append(signal)
                            continue
                        if signal["asset"] in active_assets:
                            signal["suppressed_reason"] = "ACTIVE_POSITION"
                            suppressed_signals.append(signal)
                            continue
                        if self.crowding_enabled:
                            try:
                                signal["crowding_confirmation"] = self.crowding_confirmation(signal)
                            except Exception as exc:
                                signal["crowding_confirmation"] = {
                                    "engine_id": CROWDING_ID,
                                    "mode": "SHADOW_CONFIRMATION_ONLY",
                                    "status": "UNAVAILABLE",
                                    "data_state": "DEGRADED",
                                    "error": type(exc).__name__,
                                    "real_money_authorized": False,
                                }
                                self.store.record_error(
                                    f"crowding {signal['symbol']} {signal['timeframe']}: {type(exc).__name__}"
                                )
                        signal["status"] = "PAPER_OPEN_PLANNED"
                        signal["delivery_status"] = "PENDING"
                        signals.append(signal)
                        active_assets.add(signal["asset"])
                        existing_signal_ids.add(signal_id)
                    group_suppressed_count = sum(1 for signal in result.get("signals", []) if signal.get("suppressed_reason"))
                    live_signal_count = sum(1 for signal in result.get("signals", []) if signal.get("signal_id") and not signal.get("suppressed_reason"))
                    result["suppressed_signal_count"] = group_suppressed_count
                    if str(result.get("status", "")).upper() == "SIGNAL" and group_suppressed_count and live_signal_count == 0:
                        result["status"] = "SUPPRESSED"
                    results.append(result)
                except Exception as exc:
                    results.append({"symbol": symbol, "timeframe": timeframe, "status": "ERROR", "error": str(exc)})
                    self.store.record_error(f"{symbol} {timeframe}: {exc}")
            existing_events = {event.get("event_id") for event in state_before.get("market_events", [])}
            fresh_pulse_events = [
                e
                for e in pulse["events"]
                if e["event_id"] not in existing_events
                and e["expires_at_ms"] >= evaluated_ms
                and e["candle_close_time_ms"] >= self.session_floor_ms
            ]
            fresh_liquidity_events = [
                e
                for e in liquidity["events"]
                if e["event_id"] not in existing_events
                and e["expires_at_ms"] >= evaluated_ms
                and e["candle_close_time_ms"] >= self.session_floor_ms
            ]
            fresh_macro_events = [
                e
                for e in macro["events"]
                if e["event_id"] not in existing_events
                and e["expires_at_ms"] >= evaluated_ms
            ]
            pulse_events = fresh_pulse_events[:effective_market_pulse_events_per_scan()]
            liquidity_events = fresh_liquidity_events[:effective_liquidity_events_per_scan()]
            macro_events = fresh_macro_events[:effective_macro_events_per_scan()]
            events = [*pulse_events, *liquidity_events, *macro_events]
            crowding_forward = crowding_forward_summary(
                [*readiness_source.get("signals", []), *signals]
            )
            previous_ms = state_before.get("last_scan", {}).get("scan_started_ms")
            scan = {
                "strategy_id": STRATEGY_ID,
                "paper_only": True,
                "scanned_utc": scan_started_utc,
                "scan_started_ms": scan_started_ms,
                "session_floor_utc": iso_from_ms(self.session_floor_ms) if self.session_floor_ms else None,
                "completed_utc": iso_from_ms(evaluated_ms),
                "scan_duration_seconds": round(time.monotonic() - started_monotonic, 3),
                "scan_gap_seconds": (scan_started_ms - previous_ms) / 1000 if previous_ms else None,
                "new_market_events": events,
                "new_market_event_count": len(events),
                "new_pulse_event_count": len(pulse_events),
                "new_liquidity_event_count": len(liquidity_events),
                "new_macro_event_count": len(macro_events),
                "pulse_groups": pulse["groups"],
                "liquidity_groups": liquidity["groups"],
                "macro_groups": macro["groups"],
                "macro_calendar": macro["calendar"],
                "groups": results,
                "new_signals": signals,
                "new_signal_count": len(signals),
                "new_watch_signal_count": sum(item.get("signal_tier") == "WATCH" for item in signals),
                "new_trade_a_plus_count": sum(item.get("signal_tier") == "TRADE_A_PLUS" for item in signals),
                "closed_signals": closed_signals,
                "closed_signal_count": len(closed_signals),
                "trade_readiness": trade_readiness,
                "crowding_forward": crowding_forward,
                "suppressed_signals": suppressed_signals,
                "suppressed_signal_count": len(suppressed_signals),
            }
            state = self.store.record_scan(
                scan,
                signals,
                events,
                closed_signals,
                now_ms=evaluated_ms,
            )
            return {"scan": scan, "state": state}


SERVICE = SignalService()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "CryptoPaperSignalBot/0.1"

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            readiness = SERVICE.store.load().get("trade_readiness", {})
            self.send_json(
                {
                    "status": "ok",
                    "strategy_id": STRATEGY_ID,
                    "paper_only": True,
                    "trade_readiness_status": readiness.get("status", "WATCH_ONLY"),
                }
            )
            return
        if parsed.path == "/":
            state = SERVICE.store.load()
            self.send_json(
                {
                    "service": "crypto-paper-signal-bot",
                    "strategy_id": STRATEGY_ID,
                    "paper_only": True,
                    "last_scan_utc": state.get("last_scan_utc"),
                    "routes": ["/health", "/status", "/readiness", "/signals/latest", "/events/latest", "/liquidity/latest", "/macro/latest", "/macro/calendar", "/scan", "/spec"],
                }
            )
            return
        if parsed.path == "/status":
            self.send_json(SERVICE.store.load())
            return
        if parsed.path == "/readiness":
            self.send_json(SERVICE.current_trade_readiness(at_ms=now_ms()))
            return
        if parsed.path in {"/signals/latest", "/events/latest", "/liquidity/latest", "/macro/latest"}:
            state = SERVICE.store.load()
            try:
                limit = min(max(int(parse_qs(parsed.query).get("limit", ["20"])[0]), 1), 500)
            except ValueError:
                self.send_json({"error": "limit must be an integer"}, HTTPStatus.BAD_REQUEST)
                return
            if parsed.path == "/signals/latest":
                self.send_json({"signals": state.get("signals", [])[-limit:]})
                return
            events = state.get("market_events", [])
            if parsed.path == "/liquidity/latest":
                events = [event for event in events if event.get("event_type") == "LIQUIDITY_MAP"]
                self.send_json({"liquidity_events": events[-limit:]})
                return
            if parsed.path == "/macro/latest":
                events = [event for event in events if event.get("event_type") == "MACRO_EVENT"]
                self.send_json({"macro_events": events[-limit:]})
                return
            self.send_json({"market_events": events[-limit:]})
            return
        if parsed.path == "/macro/calendar":
            state = SERVICE.store.load()
            scan = state.get("last_scan", {})
            self.send_json(
                {
                    "engine_id": MACRO_ID,
                    "macro_calendar": scan.get("macro_calendar", []),
                    "macro_groups": scan.get("macro_groups", []),
                }
            )
            return
        if parsed.path == "/scan":
            token = os.getenv("SCAN_TOKEN")
            provided = parse_qs(parsed.query).get("token", [self.headers.get("X-Scan-Token", "")])[0]
            if token and provided != token:
                self.send_json({"error": "scan token required"}, HTTPStatus.UNAUTHORIZED)
                return
            heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "3600"))
            self.send_json(scan_notify_once(heartbeat_interval_seconds=heartbeat_interval))
            return
        if parsed.path == "/spec":
            from .strategy import CANDIDATES

            self.send_json(
                {
                    "strategy_id": STRATEGY_ID,
                    "portfolio_id": PORTFOLIO_ID,
                    "portfolio_name": PORTFOLIO_NAME,
                    "paper_only": True,
                    "signal_tiers": {
                        "WATCH": "Forward evidence is incomplete; observation only.",
                        "TRADE_A_PLUS": "All locked forward gates passed; still paper-only until a separate live release.",
                    },
                    "trade_a_plus_gate": {
                        "gate_id": GATE_ID,
                        "research_promotion_approved": False,
                        "policy": DEFAULT_POLICY,
                        "current": SERVICE.current_trade_readiness(at_ms=now_ms()),
                    },
                    "release_id": PULSE_ID,
                    "telegram_language": "vi",
                    "data_source_fallback": {
                        "primary": "Binance USD-M Futures REST",
                        "futures_mirrors": list(SERVICE.client.base_urls),
                        "spot_market_fallback": SERVICE.client.spot_market_base_url,
                        "applies_to": ["klines", "depth", "ticker_price"],
                        "note": "Used only when the futures gateway returns a retryable ban/rate/network error; futures-only OI remains degraded if unavailable.",
                    },
                    "market_pulse": {
                        "engine_id": PULSE_ID, "watch_only": True,
                        "markets": [f"{s} {tf}" for s, tf in PULSE_MARKETS],
                        "directions": ["LONG", "SHORT"],
                        "max_close_to_notify_seconds": MAX_PULSE_LAG_SECONDS,
                        "max_events_per_scan": effective_market_pulse_events_per_scan(),
                        "thresholds_fraction": THRESHOLDS,
                        "performance_metrics": None,
                    },
                    "liquidity_intel": {
                        "engine_id": LIQUIDITY_ID,
                        "enabled": SERVICE.liquidity_enabled,
                        "watch_only": True,
                        "sources": [
                            "Binance USD-M order book depth",
                            "Binance USD-M open-interest history",
                            "Binance 1h/4h/1d volume and taker flow",
                            "Hyperliquid L2 book cross-check",
                        ],
                        "markets": list(LIQUIDITY_SYMBOLS),
                        "timeframes": list(LIQUIDITY_INTERVALS),
                        "max_close_to_notify_seconds": {
                            timeframe: max_liquidity_event_lag_seconds(timeframe)
                            for timeframe in LIQUIDITY_INTERVALS
                        },
                        "max_events_per_scan": effective_liquidity_events_per_scan(),
                        "performance_metrics": None,
                    },
                    "crowding_confirmation": {
                        "engine_id": CROWDING_ID,
                        "enabled": SERVICE.crowding_enabled,
                        "mode": "shadow_confirmation_only",
                        "blocks_base_signals": False,
                        "markets": list(R26A_SCAN_MARKETS),
                        "inputs": ["Binance funding-rate history", "Binance 1h premium-index history"],
                        "candidate_votes": 52,
                        "consensus_min": 0.50,
                    },
                    "macro_event_watch": {
                        "engine_id": MACRO_ID,
                        "enabled": SERVICE.macro_enabled,
                        "watch_only": True,
                        "sources": [
                            "Federal Reserve FOMC calendar",
                            "BLS economic release calendar",
                            "BEA release schedule",
                            "Census economic indicator calendar",
                            "Cleveland Fed inflation nowcasting",
                            "Trading Economics consensus forecast when TRADING_ECONOMICS_API_KEY is configured",
                        ],
                        "alert_phases": ["T-7D", "T-24H", "T-6H", "T-1H", "T-15M", "LIVE"],
                        "max_events_per_scan": effective_macro_events_per_scan(),
                        "performance_metrics": None,
                    },
                    "scan_markets": [f"{symbol} {timeframe}" for symbol, timeframe in R26A_SCAN_MARKETS],
                    "context_markets": [f"{symbol} {timeframe}" for symbol, timeframe in R26A_CONTEXT_MARKETS],
                    "sleeves": sorted({candidate.sleeve_id for candidate in CANDIDATES}),
                    "entry_model": "NEXT_OPEN",
                    "risk_fraction": 0.25,
                    "max_positions_per_sleeve": 4,
                    "freshness_guard": {
                        "effective_scan_interval_seconds": effective_scan_interval_seconds(),
                        "max_signal_entry_lag_seconds": env_int("MAX_SIGNAL_ENTRY_LAG_SECONDS", DEFAULT_MAX_SIGNAL_ENTRY_LAG_SECONDS, 0),
                        "max_signal_chase_bps": env_float("MAX_SIGNAL_CHASE_BPS", DEFAULT_MAX_SIGNAL_CHASE_BPS, 0.0),
                    },
                    "report_metrics": PORTFOLIO_METRICS,
                    "full_derivatives_state_available_required": False,
                    "candidates": [candidate.__dict__ for candidate in CANDIDATES],
                }
            )
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/health"}:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/scan":
            self.do_GET()
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        print("%s - %s" % (self.log_date_time_string(), format % args), flush=True)


def send_startup_message(scan_interval_seconds: int, heartbeat_interval_seconds: int) -> None:
    if not env_enabled("TELEGRAM_STARTUP_ENABLED"):
        return
    telegram = TelegramSender()
    try:
        startup_result = telegram.send_message(
            format_startup_message(
                strategy_id=STRATEGY_ID,
                scan_interval_seconds=scan_interval_seconds,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
            )
        )
        print(
            json.dumps(
                {
                    "event": "telegram_startup",
                    "time_utc": now_iso(),
                    "ok": startup_result.get("ok", False),
                    "skipped": startup_result.get("skipped", False),
                    "reason": startup_result.get("reason"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    except Exception as exc:
        print(
            json.dumps({"event": "telegram_startup_error", "time_utc": now_iso(), "error": str(exc)}, sort_keys=True),
            flush=True,
        )


def deliver_pending(
    service: SignalService,
    telegram: TelegramSender,
    *,
    defer_macro: bool = False,
) -> list[dict[str, Any]]:
    macro_batch: list[dict[str, Any]] = []
    with service.lock:
        state = service.store.load()
        for collection, key, formatter in (
            ("signals", "signal_id", format_signal_message),
            ("market_events", "event_id", format_event_message),
        ):
            for item in state.get(collection, []):
                if item.get("delivery_status") != "PENDING":
                    continue
                item_id = item[key]
                at_ms = now_ms()
                expiry = item.get("expires_at_ms") if collection == "market_events" else int(item["entry_time_ms"]) + int(item["max_entry_lag_seconds"]) * 1000
                if at_ms > expiry:
                    service.store.record_delivery(collection, item_id, "EXPIRED")
                    continue
                if collection == "market_events" and item.get("event_type") == "MACRO_EVENT":
                    macro_batch.append(item)
                    continue
                if not telegram.configured:
                    continue
                try:
                    if collection == "signals":
                        ticker = service.client.ticker_price(item["symbol"])
                        at_ms = now_ms()
                        price = float(ticker["price"])
                        quote_ms = int(ticker["time"])
                        if not math.isfinite(price) or price <= 0 or not -5000 <= at_ms - quote_ms <= 30000:
                            raise ValueError("invalid_or_stale_quote")
                        move = (price / float(item["entry_price"]) - 1) * 10000
                        if item["side"] == "SHORT":
                            move = (float(item["entry_price"]) / price - 1) * 10000
                        if at_ms > expiry or move > float(item["max_chase_bps"]):
                            service.store.record_delivery(collection, item_id, "EXPIRED" if at_ms > expiry else "SUPPRESSED_CHASE_AT_SEND")
                            continue
                        item.update(market_price_at_scan=price, entry_price_move_bps=move,
                                    quote_time_utc=iso_from_ms(quote_ms), entry_lag_seconds=(at_ms-int(item["entry_time_ms"]))/1000)
                    else:
                        if item.get("event_type") == "MARKET_PULSE":
                            item["freshness_lag_seconds"] = (at_ms - item["candle_close_time_ms"]) / 1000
                    item["notify_time_utc"] = iso_from_ms(at_ms)
                    response = telegram.send_message(formatter(item))
                    if not response.get("ok"):
                        raise RuntimeError("telegram_not_acknowledged")
                    service.store.record_delivery(collection, item_id, "SENT", updates=item)
                    print(json.dumps({"event": "telegram_delivery", "id": item_id, "ok": True}), flush=True)
                except Exception as exc:
                    # Store the error class; HTTP exceptions can contain bot-token URLs.
                    service.store.record_delivery(collection, item_id, "PENDING", error=type(exc).__name__)
                    print(json.dumps({"event": "telegram_delivery", "id": item_id, "ok": False, "error": type(exc).__name__}), flush=True)

        if macro_batch and not defer_macro and telegram.configured:
            notified_ms = now_ms()
            for item in macro_batch:
                item["notify_time_utc"] = iso_from_ms(notified_ms)
            try:
                response = telegram.send_message(format_macro_digest_message(macro_batch))
                if not response.get("ok"):
                    raise RuntimeError("telegram_not_acknowledged")
                for item in macro_batch:
                    service.store.record_delivery("market_events", item["event_id"], "SENT", updates=item)
                print(
                    json.dumps({"event": "telegram_macro_digest", "count": len(macro_batch), "ok": True}),
                    flush=True,
                )
            except Exception as exc:
                for item in macro_batch:
                    service.store.record_delivery(
                        "market_events",
                        item["event_id"],
                        "PENDING",
                        error=type(exc).__name__,
                    )
                print(
                    json.dumps(
                        {
                            "event": "telegram_macro_digest",
                            "count": len(macro_batch),
                            "ok": False,
                            "error": type(exc).__name__,
                        }
                    ),
                    flush=True,
                )
    return macro_batch


def format_event_message(item: dict[str, Any]) -> str:
    if item.get("event_type") == "LIQUIDITY_MAP":
        return format_liquidity_event_message(item)
    if item.get("event_type") == "MACRO_EVENT":
        return format_macro_event_message(item)
    return format_market_pulse_message(item)


def scan_notify_once(
    *,
    heartbeat_interval_seconds: int,
    force_heartbeat: bool = False,
    startup: bool = False,
) -> dict[str, Any]:
    with NOTIFY_LOCK:
        return _scan_notify_once(
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            force_heartbeat=force_heartbeat,
            startup=startup,
        )


def _scan_notify_once(
    *,
    heartbeat_interval_seconds: int,
    force_heartbeat: bool = False,
    startup: bool = False,
) -> dict[str, Any]:
    global LAST_HEARTBEAT_SENT
    telegram = TelegramSender()
    heartbeat_enabled = env_enabled("TELEGRAM_HEARTBEAT_ENABLED")
    startup_enabled = startup and env_enabled("TELEGRAM_STARTUP_ENABLED")
    scan_result = SERVICE.scan_once()
    macro_batch = deliver_pending(SERVICE, telegram, defer_macro=startup_enabled)
    scan_result["state"] = SERVICE.store.load()
    scan_summary = compact_scan(scan_result)
    print(json.dumps(scan_summary, sort_keys=True), flush=True)

    with NOTIFY_LOCK:
        if startup_enabled:
            try:
                startup_result = telegram.send_message(
                    format_startup_message(
                        strategy_id=STRATEGY_ID,
                        scan_interval_seconds=effective_scan_interval_seconds(),
                        heartbeat_interval_seconds=heartbeat_interval_seconds,
                        scan_summary=scan_summary,
                        macro_events=macro_batch,
                        historical_metrics=PORTFOLIO_METRICS,
                    )
                )
                if startup_result.get("ok"):
                    for item in macro_batch:
                        SERVICE.store.record_delivery("market_events", item["event_id"], "SENT", updates=item)
                    LAST_HEARTBEAT_SENT = time.monotonic()
                print(
                    json.dumps(
                        {
                            "event": "telegram_startup_digest",
                            "time_utc": now_iso(),
                            "ok": startup_result.get("ok", False),
                            "macro_count": len(macro_batch),
                            "skipped": startup_result.get("skipped", False),
                            "reason": startup_result.get("reason"),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                print(
                    json.dumps({"event": "telegram_startup_error", "time_utc": now_iso(), "error": str(exc)}, sort_keys=True),
                    flush=True,
                )
        else:
            heartbeat_due = heartbeat_enabled and (
                force_heartbeat or time.monotonic() - LAST_HEARTBEAT_SENT >= heartbeat_interval_seconds
            )
            if heartbeat_due:
                try:
                    heartbeat_result = telegram.send_message(format_heartbeat_message(scan_summary))
                    LAST_HEARTBEAT_SENT = time.monotonic()
                    print(
                        json.dumps(
                            {
                                "event": "telegram_heartbeat",
                                "time_utc": now_iso(),
                                "ok": heartbeat_result.get("ok", False),
                                "skipped": heartbeat_result.get("skipped", False),
                                "reason": heartbeat_result.get("reason"),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        json.dumps({"event": "telegram_heartbeat_error", "time_utc": now_iso(), "error": str(exc)}, sort_keys=True),
                        flush=True,
                    )

    return scan_result


def background_loop(interval_seconds: int, heartbeat_interval_seconds: int) -> None:
    first_scan = True
    while True:
        started = time.monotonic()
        try:
            scan_notify_once(heartbeat_interval_seconds=heartbeat_interval_seconds, startup=first_scan)
        except Exception as exc:
            SERVICE.store.record_error(f"background scan: {exc}")
        first_scan = False
        time.sleep(max(1.0, interval_seconds - (time.monotonic() - started)))


def main() -> None:
    port = int(os.getenv("PORT", "10000"))
    host = os.getenv("HOST", "0.0.0.0")
    interval = effective_scan_interval_seconds()
    heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "3600"))
    disable_background = os.getenv("DISABLE_BACKGROUND_SCAN", "").lower() in {"1", "true", "yes"}
    print(
        json.dumps(
            {
                "event": "web_service_started",
                "time_utc": now_iso(),
                "strategy_id": STRATEGY_ID,
                "paper_only": True,
                "scan_interval_seconds": interval,
                "heartbeat_interval_seconds": heartbeat_interval,
                "background_scan_enabled": not disable_background,
                "macro_event_watch_enabled": SERVICE.macro_enabled,
                "telegram_configured": telegram_configured(),
                "telegram_startup_enabled": env_enabled("TELEGRAM_STARTUP_ENABLED"),
                "telegram_heartbeat_enabled": env_enabled("TELEGRAM_HEARTBEAT_ENABLED"),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if not disable_background:
        thread = threading.Thread(target=background_loop, args=(interval, heartbeat_interval), daemon=True)
        thread.start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Listening on http://{host}:{port} strategy={STRATEGY_ID} paper_only=true", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
