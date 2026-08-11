from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonStore:
    def __init__(self, path: str | Path | None = None, max_signals: int | None = None) -> None:
        self.path = Path(path or os.getenv("STATE_PATH") or "data/paper_state.json")
        self.max_signals = int(max_signals or os.getenv("MAX_SIGNALS_RETAINED") or "500")

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "created_utc": now_iso(),
                "last_scan_utc": None,
                "scan_count": 0,
                "signals": [],
                "active_positions": [],
                "errors": [],
            }
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {
                "created_utc": now_iso(),
                "last_scan_utc": None,
                "scan_count": 0,
                "signals": [],
                "active_positions": [],
                "errors": [{"time_utc": now_iso(), "message": "State file was invalid JSON and has been reset."}],
            }

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def record_scan(self, scan: dict[str, Any], new_signals: list[dict[str, Any]]) -> dict[str, Any]:
        state = self.load()
        state["last_scan_utc"] = now_iso()
        state["scan_count"] = int(state.get("scan_count", 0)) + 1
        state["last_scan"] = scan

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        active = [
            item
            for item in state.get("active_positions", [])
            if int(item.get("planned_exit_time_ms", 0)) > now_ms
        ]
        existing_ids = {item.get("signal_id") for item in state.get("signals", [])}
        for signal in new_signals:
            if signal.get("signal_id") in existing_ids:
                continue
            active.append(signal)
            state.setdefault("signals", []).append(signal)
        state["active_positions"] = active
        state["signals"] = state.get("signals", [])[-self.max_signals :]
        self.save(state)
        return state

    def record_error(self, message: str) -> dict[str, Any]:
        state = self.load()
        state.setdefault("errors", []).append({"time_utc": now_iso(), "message": message})
        state["errors"] = state["errors"][-50:]
        self.save(state)
        return state
