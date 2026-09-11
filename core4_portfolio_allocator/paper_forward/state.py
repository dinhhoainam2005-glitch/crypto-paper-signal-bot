from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ForwardStore:
    def __init__(self, path: Path, spec_sha256: str) -> None:
        self.path = path
        self.spec_sha256 = spec_sha256
        self.lock = threading.RLock()

    def empty(self) -> dict[str, Any]:
        return {
            "version": 1,
            "strategy_id": "CORE4_V7_BETA_REGIME_DONCHIAN",
            "spec_sha256": self.spec_sha256,
            "created_utc": now_iso(),
            "last_scan_utc": None,
            "scan_count": 0,
            "cash": 1.0,
            "equity": 1.0,
            "signals": [],
            "active_positions": [],
            "closed_trades": [],
            "events": [],
            "delivered_ids": [],
            "errors": [],
        }

    def load(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.is_file():
                return self.empty()
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if state.get("spec_sha256") != self.spec_sha256:
                raise RuntimeError("state was created by a different strategy specification")
            return state

    def save(self, state: dict[str, Any]) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(state, indent=2, sort_keys=True, ensure_ascii=True), encoding="utf-8"
            )
            temporary.replace(self.path)

    def record_error(self, message: str) -> None:
        state = self.load()
        state.setdefault("errors", []).append({"time_utc": now_iso(), "message": message})
        state["errors"] = state["errors"][-50:]
        self.save(state)
