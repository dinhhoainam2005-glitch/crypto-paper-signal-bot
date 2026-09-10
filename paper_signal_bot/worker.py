from __future__ import annotations

import json
import os
import time

from .strategy import STRATEGY_ID
from .web import (
    effective_scan_interval_seconds, notifiable_signals, now_iso, scan_notify_once,
)


def main() -> None:
    interval = effective_scan_interval_seconds()
    heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "3600"))
    run_once = os.getenv("WORKER_RUN_ONCE", "").lower() in {"1", "true", "yes"}
    print(json.dumps({"event": "worker_started", "time_utc": now_iso(),
                      "strategy_id": STRATEGY_ID, "paper_only": True,
                      "scan_interval_seconds": interval}), flush=True)
    first_scan = True
    while True:
        started = time.monotonic()
        try:
            scan_notify_once(
                heartbeat_interval_seconds=heartbeat_interval,
                force_heartbeat=run_once,
                startup=first_scan,
            )
        except Exception as exc:
            print(json.dumps({"event": "worker_error", "error": type(exc).__name__}), flush=True)
        first_scan = False
        if run_once:
            break
        time.sleep(max(1.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
