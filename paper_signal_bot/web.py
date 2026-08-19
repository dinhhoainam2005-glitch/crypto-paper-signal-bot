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
from .strategy import STRATEGY_ID, candidate_groups, evaluate_latest


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SignalService:
    def __init__(self) -> None:
        self.client = BinanceFuturesClient()
        self.store = JsonStore()
        self.lock = threading.Lock()
        self.groups = candidate_groups()

    def active_assets(self) -> set[str]:
        state = self.store.load()
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        active = state.get("active_positions", [])
        return {
            str(item.get("asset"))
            for item in active
            if int(item.get("planned_exit_time_ms", 0)) > now_ms
        }

    def scan_once(self) -> dict[str, Any]:
        with self.lock:
            results: list[dict[str, Any]] = []
            signals: list[dict[str, Any]] = []
            active_assets = self.active_assets()
            for (symbol, timeframe), _candidates in self.groups.items():
                try:
                    klines = self.client.klines(symbol, timeframe, limit=220)
                    premium_error = None
                    derivatives_error = None
                    try:
                        premium = self.client.premium_index_klines(symbol, timeframe, limit=100)
                    except Exception as exc:
                        premium = []
                        premium_error = str(exc)
                    try:
                        derivatives_ok = self.client.derivatives_state_available(symbol)
                    except Exception as exc:
                        derivatives_ok = False
                        derivatives_error = str(exc)
                    result = evaluate_latest(
                        symbol=symbol,
                        timeframe=timeframe,
                        klines=klines,
                        premium_klines=premium,
                        derivatives_state_available=derivatives_ok,
                    )
                    if premium_error is not None:
                        result["premium_error"] = premium_error
                    if derivatives_error is not None:
                        result["derivatives_error"] = derivatives_error
                    for signal in result.get("signals", []):
                        if signal["asset"] in active_assets:
                            signal["suppressed_reason"] = "ACTIVE_POSITION"
                            continue
                        signal_id = "{strategy}:{symbol}:{timeframe}:{candidate}:{time}".format(
                            strategy=STRATEGY_ID,
                            symbol=signal["symbol"],
                            timeframe=signal["timeframe"],
                            candidate=signal["candidate"]["candidate_id"],
                            time=signal["signal_time_ms"],
                        )
                        signal["signal_id"] = signal_id
                        signal["created_utc"] = now_iso()
                        signal["status"] = "PAPER_OPEN_PLANNED"
                        signals.append(signal)
                        active_assets.add(signal["asset"])
                    results.append(result)
                except Exception as exc:
                    results.append({"symbol": symbol, "timeframe": timeframe, "status": "ERROR", "error": str(exc)})
                    self.store.record_error(f"{symbol} {timeframe}: {exc}")
            scan = {
                "strategy_id": STRATEGY_ID,
                "paper_only": True,
                "scanned_utc": now_iso(),
                "groups": results,
                "new_signal_count": len(signals),
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
            self.send_json(SERVICE.scan_once())
            return
        if parsed.path == "/spec":
            from .strategy import CANDIDATES, PREMIUM_Z_MAX, REALIZED_VOL_24_MIN, VOLUME_Z_MIN

            self.send_json(
                {
                    "strategy_id": STRATEGY_ID,
                    "paper_only": True,
                    "premium_close_prior_z_24_max": PREMIUM_Z_MAX,
                    "quote_volume_prior_z_20_min": VOLUME_Z_MIN,
                    "realized_vol_24_min": REALIZED_VOL_24_MIN,
                    "full_derivatives_state_available_required": False,
                    "candidates": [candidate.__dict__ for candidate in CANDIDATES],
                }
            )
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/scan":
            self.do_GET()
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        print("%s - %s" % (self.log_date_time_string(), format % args), flush=True)


def background_loop(interval_seconds: int) -> None:
    while True:
        try:
            SERVICE.scan_once()
        except Exception as exc:
            SERVICE.store.record_error(f"background scan: {exc}")
        time.sleep(interval_seconds)


def main() -> None:
    port = int(os.getenv("PORT", "10000"))
    host = os.getenv("HOST", "0.0.0.0")
    interval = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
    disable_background = os.getenv("DISABLE_BACKGROUND_SCAN", "").lower() in {"1", "true", "yes"}
    if not disable_background:
        thread = threading.Thread(target=background_loop, args=(interval,), daemon=True)
        thread.start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Listening on http://{host}:{port} strategy={STRATEGY_ID} paper_only=true", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
