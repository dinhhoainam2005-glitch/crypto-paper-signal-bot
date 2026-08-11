from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from .strategy import STRATEGY_ID
from .telegram import TelegramSender, format_signal_message, telegram_configured
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
                "quote_volume_prior_z_20": features.get("quote_volume_prior_z_20"),
                "mkt_taker_quote_imbalance_derived": features.get("mkt_taker_quote_imbalance_derived"),
                "premium_close_prior_z_24": features.get("premium_close_prior_z_24"),
                "full_derivatives_state_available": features.get("full_derivatives_state_available"),
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


def main() -> None:
    interval = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
    run_once = os.getenv("WORKER_RUN_ONCE", "").lower() in {"1", "true", "yes"}
    telegram = TelegramSender()
    print(
        json.dumps(
            {
                "event": "worker_started",
                "time_utc": now_iso(),
                "strategy_id": STRATEGY_ID,
                "paper_only": True,
                "scan_interval_seconds": interval,
                "run_once": run_once,
                "telegram_configured": telegram_configured(),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    while True:
        try:
            scan_result = SERVICE.scan_once()
            print(json.dumps(compact_scan(scan_result), sort_keys=True), flush=True)
            for signal in scan_result.get("scan", {}).get("groups", []):
                for item in signal.get("signals", []):
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
