from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .binance_client import BinanceFuturesClient
from .storage import JsonStore
from .strategy import PORTFOLIO_ID, PORTFOLIO_METRICS, PORTFOLIO_NAME, R24A_CONTEXT_MARKETS, STRATEGY_ID, candidate_groups, evaluate_latest
from .telegram import (
    TelegramSender,
    env_enabled,
    format_heartbeat_message,
    format_signal_message,
    format_startup_message,
    telegram_configured,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


NOTIFY_LOCK = threading.Lock()
LAST_HEARTBEAT_SENT = 0.0
DEFAULT_SCAN_INTERVAL_SECONDS = 60
DEFAULT_MAX_SIGNAL_ENTRY_LAG_SECONDS = 600
DEFAULT_MAX_SIGNAL_CHASE_BPS = 40.0


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
        "time_utc": now_iso(),
        "strategy_id": STRATEGY_ID,
        "paper_only": True,
        "new_signal_count": scan.get("new_signal_count", 0),
        "suppressed_signal_count": scan.get("suppressed_signal_count", 0),
        "active_position_count": len(state.get("active_positions", [])),
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
        self.store = JsonStore()
        self.lock = threading.Lock()
        self.groups = candidate_groups()
        self.context_markets = R24A_CONTEXT_MARKETS

    @staticmethod
    def active_assets_from_state(state: dict[str, Any]) -> set[str]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        active = state.get("active_positions", [])
        return {
            str(item.get("asset"))
            for item in active
            if int(item.get("planned_exit_time_ms", 0)) > now_ms
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
            scan_started_utc = iso_from_ms(scan_started_ms)
            max_entry_lag_seconds = env_int("MAX_SIGNAL_ENTRY_LAG_SECONDS", DEFAULT_MAX_SIGNAL_ENTRY_LAG_SECONDS, 0)
            max_chase_bps = env_float("MAX_SIGNAL_CHASE_BPS", DEFAULT_MAX_SIGNAL_CHASE_BPS, 0.0)
            results: list[dict[str, Any]] = []
            signals: list[dict[str, Any]] = []
            suppressed_signals: list[dict[str, Any]] = []
            state_before = self.store.load()
            active_assets = self.active_assets_from_state(state_before)
            existing_signal_ids = self.existing_signal_ids_from_state(state_before)
            klines_cache: dict[tuple[str, str], list[list[Any]]] = {}
            premium_cache: dict[tuple[str, str], list[list[Any]]] = {}
            fetch_errors: dict[tuple[str, str], str] = {}
            for symbol, timeframe in self.context_markets:
                try:
                    klines_cache[(symbol, timeframe)] = self.client.klines(symbol, timeframe, limit=220)
                    premium_error = None
                    try:
                        premium_cache[(symbol, timeframe)] = self.client.premium_index_klines(symbol, timeframe, limit=100)
                    except Exception as exc:
                        premium_error = str(exc)
                        premium_cache[(symbol, timeframe)] = []
                    if premium_error is not None:
                        fetch_errors[(symbol, timeframe)] = f"premium: {premium_error}"
                except Exception as exc:
                    fetch_errors[(symbol, timeframe)] = str(exc)
                    self.store.record_error(f"{symbol} {timeframe}: {exc}")
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
                    try:
                        derivatives_ok = self.client.derivatives_state_available(symbol)
                    except Exception as exc:
                        derivatives_ok = False
                        derivatives_error = str(exc)
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
                        signal["notify_time_utc"] = scan_started_utc
                        entry_lag_seconds = max((scan_started_ms - int(signal.get("entry_time_ms", scan_started_ms))) / 1000.0, 0.0)
                        signal["entry_lag_seconds"] = entry_lag_seconds
                        signal["max_entry_lag_seconds"] = max_entry_lag_seconds
                        signal["max_chase_bps"] = max_chase_bps
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
                        signal["status"] = "PAPER_OPEN_PLANNED"
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
            scan = {
                "strategy_id": STRATEGY_ID,
                "paper_only": True,
                "scanned_utc": scan_started_utc,
                "groups": results,
                "new_signals": signals,
                "new_signal_count": len(signals),
                "suppressed_signals": suppressed_signals,
                "suppressed_signal_count": len(suppressed_signals),
            }
            state = self.store.record_scan(scan, signals)
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
            self.send_json({"status": "ok", "strategy_id": STRATEGY_ID, "paper_only": True})
            return
        if parsed.path == "/":
            state = SERVICE.store.load()
            self.send_json(
                {
                    "service": "crypto-paper-signal-bot",
                    "strategy_id": STRATEGY_ID,
                    "paper_only": True,
                    "last_scan_utc": state.get("last_scan_utc"),
                    "routes": ["/health", "/status", "/signals/latest", "/scan", "/spec"],
                }
            )
            return
        if parsed.path == "/status":
            self.send_json(SERVICE.store.load())
            return
        if parsed.path == "/signals/latest":
            state = SERVICE.store.load()
            limit = int(parse_qs(parsed.query).get("limit", ["20"])[0])
            self.send_json({"signals": state.get("signals", [])[-limit:]})
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
            from .strategy import CANDIDATES, R24A_CONTEXT_MARKETS, R24A_SCAN_MARKETS

            self.send_json(
                {
                    "strategy_id": STRATEGY_ID,
                    "portfolio_id": PORTFOLIO_ID,
                    "portfolio_name": PORTFOLIO_NAME,
                    "paper_only": True,
                    "scan_markets": [f"{symbol} {timeframe}" for symbol, timeframe in R24A_SCAN_MARKETS],
                    "context_markets": [f"{symbol} {timeframe}" for symbol, timeframe in R24A_CONTEXT_MARKETS],
                    "sleeves": ["r24a_bnb_quality_long", "r24a_btc_1h_pullback_observation", "r24a_taker_flow_quality"],
                    "entry_model": "NEXT_OPEN",
                    "risk_fraction": 0.25,
                    "max_positions_per_sleeve": 4,
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


def scan_notify_once(*, heartbeat_interval_seconds: int, force_heartbeat: bool = False) -> dict[str, Any]:
    global LAST_HEARTBEAT_SENT
    telegram = TelegramSender()
    heartbeat_enabled = env_enabled("TELEGRAM_HEARTBEAT_ENABLED")
    scan_result = SERVICE.scan_once()
    scan_summary = compact_scan(scan_result)
    print(json.dumps(scan_summary, sort_keys=True), flush=True)

    with NOTIFY_LOCK:
        heartbeat_due = heartbeat_enabled and (force_heartbeat or time.monotonic() - LAST_HEARTBEAT_SENT >= heartbeat_interval_seconds)
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

    for item in notifiable_signals(scan_result):
        print(json.dumps({"event": "paper_signal", **item}, sort_keys=True), flush=True)
        try:
            telegram_result = telegram.send_message(format_signal_message(item))
            print(
                json.dumps(
                    {
                        "event": "telegram_send",
                        "time_utc": now_iso(),
                        "ok": telegram_result.get("ok", False),
                        "skipped": telegram_result.get("skipped", False),
                        "reason": telegram_result.get("reason"),
                        "signal_id": item.get("signal_id"),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "telegram_error",
                        "time_utc": now_iso(),
                        "error": str(exc),
                        "signal_id": item.get("signal_id"),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    return scan_result


def background_loop(interval_seconds: int, heartbeat_interval_seconds: int) -> None:
    while True:
        try:
            scan_notify_once(heartbeat_interval_seconds=heartbeat_interval_seconds)
        except Exception as exc:
            SERVICE.store.record_error(f"background scan: {exc}")
        time.sleep(interval_seconds)


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
                "telegram_configured": telegram_configured(),
                "telegram_startup_enabled": env_enabled("TELEGRAM_STARTUP_ENABLED"),
                "telegram_heartbeat_enabled": env_enabled("TELEGRAM_HEARTBEAT_ENABLED"),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    send_startup_message(interval, heartbeat_interval)
    if not disable_background:
        thread = threading.Thread(target=background_loop, args=(interval, heartbeat_interval), daemon=True)
        thread.start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Listening on http://{host}:{port} strategy={STRATEGY_ID} paper_only=true", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
