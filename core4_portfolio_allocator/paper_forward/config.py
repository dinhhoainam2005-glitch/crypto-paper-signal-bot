from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "CANDIDATE_SPEC.json"
EXPECTED_SPEC_SHA256 = "7d8e356d97a644ac321547394210b12f5fa6575e9583f0e003db03a896c41b1a"


def env_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


def spec_sha256(path: Path = SPEC_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_locked_spec(path: Path = SPEC_PATH, enforce_hash: bool = True) -> dict[str, Any]:
    digest = spec_sha256(path)
    if enforce_hash and digest != EXPECTED_SPEC_SHA256:
        raise RuntimeError(f"frozen candidate spec hash mismatch: {digest}")
    spec = json.loads(path.read_text(encoding="utf-8"))
    controls = spec.get("controls", {})
    if (
        spec.get("strategy_id") != "CORE4_V7_BETA_REGIME_DONCHIAN"
        or spec.get("status") != "PAPER_FORWARD_CANDIDATE"
        or spec.get("frozen") is not True
        or controls.get("paper_only") is not True
        or controls.get("live_trading_enabled") is not False
        or controls.get("real_money_authorized") is not False
    ):
        raise RuntimeError("candidate spec is not an authorized paper-forward configuration")
    return spec


@dataclass(frozen=True)
class RuntimeConfig:
    state_path: Path
    scan_interval_seconds: int
    heartbeat_interval_seconds: int
    max_entry_lag_seconds: int
    max_chase_bps: float
    max_replay_minutes: int
    telegram_enabled: bool
    startup_enabled: bool
    heartbeat_enabled: bool

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            state_path=Path(os.getenv("CORE4_STATE_PATH", "data/core4_v7_forward_state.json")),
            scan_interval_seconds=max(30, int(os.getenv("CORE4_SCAN_INTERVAL_SECONDS", "60"))),
            heartbeat_interval_seconds=max(300, int(os.getenv("CORE4_HEARTBEAT_INTERVAL_SECONDS", "3600"))),
            max_entry_lag_seconds=max(0, int(os.getenv("CORE4_MAX_ENTRY_LAG_SECONDS", "600"))),
            max_chase_bps=max(0.0, float(os.getenv("CORE4_MAX_CHASE_BPS", "40"))),
            max_replay_minutes=max(60, int(os.getenv("CORE4_MAX_REPLAY_MINUTES", "1500"))),
            telegram_enabled=env_bool("CORE4_TELEGRAM_ENABLED", False),
            startup_enabled=env_bool("CORE4_TELEGRAM_STARTUP_ENABLED", True),
            heartbeat_enabled=env_bool("CORE4_TELEGRAM_HEARTBEAT_ENABLED", True),
        )
