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

from .config import RuntimeConfig, spec_sha256
from .engine import ForwardEngine
from .telegram import (
    TelegramSender,
    format_heartbeat,
    format_position_event,
    format_signal,
    format_startup,
    format_trade_closed,
)


CONFIG = RuntimeConfig.from_env()
ENGINE = ForwardEngine(config=CONFIG)
TELEGRAM = TelegramSender(CONFIG.telegram_enabled)
NOTIFY_LOCK = threading.RLock()
LAST_HEARTBEAT_MONOTONIC = 0.0
PROCESS_START_ID = int(time.time() * 1000)
PROCESS_START_MONOTONIC = time.monotonic()
BACKGROUND_SCAN_ENABLED = os.getenv("CORE4_DISABLE_BACKGROUND_SCAN", "false").lower() not in {
    "1",
    "true",
    "yes",
}
BACKGROUND_THREAD: threading.Thread | None = None


def send_once(state: dict[str, Any], item_id: str, text: str) -> dict[str, Any]:
    delivered = set(state.get("delivered_ids", []))
    if item_id in delivered:
        return {"ok": True, "skipped": True, "reason": "already_delivered"}
    result = TELEGRAM.send(text)
    if result.get("ok"):
        state.setdefault("delivered_ids", []).append(item_id)
        state["delivered_ids"] = state["delivered_ids"][-2000:]
        ENGINE.store.save(state)
    return result


def scan_and_notify(startup: bool = False, force_heartbeat: bool = False) -> dict[str, Any]:
    global LAST_HEARTBEAT_MONOTONIC
    with NOTIFY_LOCK:
        result = ENGINE.scan()
        state = result["state"]
        deliveries: list[dict[str, Any]] = []
        if startup and CONFIG.startup_enabled:
            deliveries.append(
                send_once(state, f"CORE4V7:STARTUP:{PROCESS_START_ID}", format_startup(result))
            )
            LAST_HEARTBEAT_MONOTONIC = time.monotonic()
        notify_suppressed = os.getenv("CORE4_NOTIFY_SUPPRESSED", "false").lower() in {"1", "true", "yes"}
        closed_signal_ids = {
            item["trade"]["signal_id"]
            for item in result.get("notifications", [])
            if item.get("reason") == "TRADE_CLOSED"
        }
        for item in result.get("notifications", []):
            item_id = item.get("event_id") or item.get("signal_id")
            if not item_id:
                continue
            if item.get("reason") == "TRADE_CLOSED":
                text = format_trade_closed(item["trade"])
            elif item.get("reason"):
                if item.get("signal_id") in closed_signal_ids and item.get("remaining_fraction", 1.0) <= 1e-12:
                    continue
                text = format_position_event(item)
            elif item.get("status") == "OPEN":
                text = format_signal(item)
            elif notify_suppressed:
                text = f"⚠️ <b>CORE4 V7 BỎ QUA {item['plan']['symbol']}</b>\n• Lý do: <code>{item['status']}</code>\n🔒 PAPER ONLY"
            else:
                continue
            deliveries.append(send_once(state, item_id, text))
        heartbeat_due = force_heartbeat or (
            time.monotonic() - LAST_HEARTBEAT_MONOTONIC >= CONFIG.heartbeat_interval_seconds
        )
        if CONFIG.heartbeat_enabled and heartbeat_due and not startup:
            heartbeat_id = f"CORE4V7:HEARTBEAT:{int(time.time() // CONFIG.heartbeat_interval_seconds)}"
            deliveries.append(send_once(state, heartbeat_id, format_heartbeat(result, state)))
            LAST_HEARTBEAT_MONOTONIC = time.monotonic()
        return {**result, "deliveries": deliveries}


def background_loop() -> None:
    first = True
    while True:
        started = time.monotonic()
        try:
            scan_and_notify(startup=first)
        except BaseException as exc:
            try:
                ENGINE.store.record_error(f"background scan: {type(exc).__name__}: {exc}")
            except Exception as store_exc:
                print(
                    json.dumps(
                        {
                            "event": "core4_error_record_failed",
                            "error": str(store_exc),
                        }
                    ),
                    flush=True,
                )
            print(json.dumps({"event": "core4_scan_error", "error": str(exc)}), flush=True)
        first = False
        time.sleep(max(1.0, CONFIG.scan_interval_seconds - (time.monotonic() - started)))


def json_body(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def health_payload(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    age_seconds: float | None = None
    last_scan = state.get("last_scan_utc")
    if last_scan:
        try:
            scanned_at = datetime.fromisoformat(str(last_scan).replace("Z", "+00:00"))
            age_seconds = max(
                0.0,
                (datetime.now(timezone.utc) - scanned_at.astimezone(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError):
            age_seconds = None
    stale_after = max(300, CONFIG.scan_interval_seconds * 5)
    thread_alive = bool(BACKGROUND_THREAD and BACKGROUND_THREAD.is_alive())
    startup_grace = time.monotonic() - PROCESS_START_MONOTONIC <= stale_after
    scan_fresh = age_seconds is not None and age_seconds <= stale_after
    healthy = not BACKGROUND_SCAN_ENABLED or (thread_alive and (scan_fresh or startup_grace))
    payload = {
        "status": "ok" if healthy else "degraded",
        "service": "core4-v7-paper-forward",
        "strategy_id": "CORE4_V7_BETA_REGIME_DONCHIAN",
        "paper_only": True,
        "live_trading": False,
        "spec_sha256": spec_sha256(),
        "last_scan_utc": last_scan,
        "last_scan_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "scan_stale_after_seconds": stale_after,
        "background_scan_enabled": BACKGROUND_SCAN_ENABLED,
        "background_thread_alive": thread_alive,
    }
    return payload, healthy


class Handler(BaseHTTPRequestHandler):
    server_version = "Core4V7PaperForward/1.0"

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json_body(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        state = ENGINE.store.load()
        if parsed.path in {"/", "/health"}:
            payload, healthy = health_payload(state)
            status = HTTPStatus.OK
            if parsed.path == "/health" and not healthy:
                status = HTTPStatus.SERVICE_UNAVAILABLE
            self.send_json(payload, status)
            return
        if parsed.path == "/status":
            self.send_json(state)
            return
        if parsed.path == "/signals":
            self.send_json({"signals": state.get("signals", [])[-50:]})
            return
        if parsed.path == "/positions":
            self.send_json({"active_positions": state.get("active_positions", [])})
            return
        if parsed.path == "/performance":
            self.send_json(
                {
                    "equity": state.get("equity", 1.0),
                    "cash": state.get("cash", 1.0),
                    "closed_trades": state.get("closed_trades", []),
                }
            )
            return
        if parsed.path == "/spec":
            self.send_json(ENGINE.spec)
            return
        if parsed.path == "/scan":
            expected = os.getenv("CORE4_SCAN_TOKEN", "")
            supplied = parse_qs(parsed.query).get("token", [self.headers.get("X-Scan-Token", "")])[0]
            if expected and supplied != expected:
                self.send_json({"error": "scan token required"}, HTTPStatus.UNAUTHORIZED)
                return
            self.send_json(scan_and_notify())
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        if urlparse(self.path).path in {"/", "/health"}:
            self.send_response(HTTPStatus.OK)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/scan":
            self.do_GET()
        else:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.log_date_time_string()} {format % args}", flush=True)


def main() -> None:
    global BACKGROUND_THREAD
    port = int(os.getenv("PORT", "10001"))
    print(
        json.dumps(
            {
                "event": "core4_web_started",
                "paper_only": True,
                "live_trading": False,
                "spec_sha256": spec_sha256(),
                "scan_interval_seconds": CONFIG.scan_interval_seconds,
                "telegram_configured": TELEGRAM.configured,
                "background_scan": BACKGROUND_SCAN_ENABLED,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if BACKGROUND_SCAN_ENABLED:
        BACKGROUND_THREAD = threading.Thread(
            target=background_loop, name="core4-background-scan", daemon=True
        )
        BACKGROUND_THREAD.start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
