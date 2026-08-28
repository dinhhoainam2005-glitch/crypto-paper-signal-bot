from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from .strategy import STRATEGY_ID
from .telegram import (
    TelegramSender,
    env_enabled,
    format_heartbeat_message,
    format_signal_message,
    format_startup_message,
    telegram_configured,
)
from .web import SERVICE


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_scan(scan_result: dict[str, Any]) -> dict[str, Any]:
    scan = scan_result.get("scan", {})
    groups = []
    for group in scan.get("groups", []):
        features = group.get("features", {})
        groups.append(
            {
                "symbol": group.get("symbol"),
                "timeframe": group.get("timeframe"),
                "status": group.get("status"),
                "latest_closed_bar_utc": group.get("latest_closed_bar_utc"),
                "raw_signal_count": group.get("raw_signal_count", 0),
                "candidate_count": group.get("candidate_count", 0),
                "quote_volume_prior_z_20": features.get("quote_volume_prior_z_20"),
                "market_breadth_count": features.get("market_breadth_count"),
                "market_breadth_assets": features.get("market_breadth_assets"),
                "market_directional_mean": features.get("market_directional_mean"),
                "breadth_n": features.get("breadth_n"),
                "breadth_min": features.get("breadth_min"),
                "premium_close_prior_z_24": features.get("premium_close_prior_z_24"),
                "realized_vol_24": features.get("realized_vol_24"),
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


def main() -> None:
    interval = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
    heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "3600"))
    run_once = os.getenv("WORKER_RUN_ONCE", "").lower() in {"1", "true", "yes"}
    telegram = TelegramSender()
    startup_enabled = env_enabled("TELEGRAM_STARTUP_ENABLED")
    heartbeat_enabled = env_enabled("TELEGRAM_HEARTBEAT_ENABLED")
    last_heartbeat_sent = 0.0
    print(
        json.dumps(
            {
                "event": "worker_started",
                "time_utc": now_iso(),
                "strategy_id": STRATEGY_ID,
                "paper_only": True,
                "scan_interval_seconds": interval,
                "heartbeat_interval_seconds": heartbeat_interval,
                "run_once": run_once,
                "telegram_configured": telegram_configured(),
                "telegram_startup_enabled": startup_enabled,
                "telegram_heartbeat_enabled": heartbeat_enabled,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if startup_enabled:
        try:
            startup_result = telegram.send_message(
                format_startup_message(
                    strategy_id=STRATEGY_ID,
                    scan_interval_seconds=interval,
                    heartbeat_interval_seconds=heartbeat_interval,
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
    while True:
        try:
            scan_result = SERVICE.scan_once()
            scan_summary = compact_scan(scan_result)
            print(json.dumps(scan_summary, sort_keys=True), flush=True)
            if heartbeat_enabled and (time.monotonic() - last_heartbeat_sent >= heartbeat_interval or run_once):
                try:
                    heartbeat_result = telegram.send_message(format_heartbeat_message(scan_summary))
                    last_heartbeat_sent = time.monotonic()
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
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "worker_error",
                        "time_utc": now_iso(),
                        "strategy_id": STRATEGY_ID,
                        "error": str(exc),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        if run_once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
