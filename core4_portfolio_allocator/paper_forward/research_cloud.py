from __future__ import annotations

import html
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
EXPECTED_FLOW_STREAMS = ("binance-futures", "bybit", "hyperliquid")
EXPECTED_POSITIONING_STREAMS = (
    "binance-context",
    "binance-liquidation",
    "bybit",
    "hyperliquid",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def age_seconds(iso_value: Any) -> float | None:
    if not iso_value:
        return None
    try:
        stamp = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return max(0.0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())


def connected_count(status: dict[str, Any], expected: tuple[str, ...]) -> tuple[int, int]:
    streams = status.get("streams", {})
    connected = sum(streams.get(name) == "CONNECTED" for name in expected)
    return connected, len(expected)


def _metric(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _percent(value: Any) -> str:
    return _metric(float(value or 0.0) * 100.0, 1)


def format_research_status(payload: dict[str, Any], title: str = "BÁO CÁO") -> str:
    flow = payload.get("flow_collector", {})
    positioning = payload.get("positioning_collector", {})
    pipeline = payload.get("pipeline", {})
    flow_connected, flow_total = connected_count(flow, EXPECTED_FLOW_STREAMS)
    pos_connected, pos_total = connected_count(positioning, EXPECTED_POSITIONING_STREAMS)
    flow_audit = pipeline.get("flow_audit", {})
    pos_audit = pipeline.get("positioning_audit", {})
    dataset = pipeline.get("dataset", {})
    screen = pipeline.get("screen", {})
    metrics = screen.get("observational_metrics_not_validated", {})
    samples_by_tf = dataset.get("samples_by_timeframe", {})
    labels = dataset.get("labels", {})
    health = str(payload.get("status", "STARTING"))
    health_label = {
        "OK": "ỔN",
        "STARTING": "ĐANG KHỞI ĐỘNG",
        "DEGRADED": "SUY GIẢM",
        "DISABLED": "TẮT",
    }.get(health, health)
    return (
        f"🧪📡 <b>R64 CLOUD RESEARCH — {html.escape(title)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'✅' if health == 'OK' else '⚠️'} Hệ thống: <b>{html.escape(health_label)}</b> | "
        f"Thu thập 24/7 trên Render\n"
        f"🌊 Flow R63A: <b>{flow_connected}/{flow_total}</b> feed | "
        f"tail <code>{flow_audit.get('common_contiguous_tail_minutes', 0)}</code> phút\n"
        f"🧭 Positioning R63C: <b>{pos_connected}/{pos_total}</b> feed | "
        f"tail <code>{pos_audit.get('common_contiguous_tail_minutes', 0)}</code> phút\n"
        f"🏷 Nhãn causal: <b>{int(dataset.get('sample_rows', 0))}</b> | "
        f"1h <code>{int(samples_by_tf.get('1h', 0))}</code> | "
        f"4h <code>{int(samples_by_tf.get('4h', 0))}</code>\n"
        f"↕️ Kết quả nhãn: LONG <code>{int(labels.get('LONG', 0))}</code> | "
        f"SHORT <code>{int(labels.get('SHORT', 0))}</code> | "
        f"NO_TRADE <code>{int(labels.get('NO_TRADE', 0))}</code>\n"
        f"🔬 Ứng viên độc lập: <b>{int(screen.get('independent_candidates', 0))}</b> / "
        f"{int(screen.get('development_minimum_clusters', 30))} | "
        f"ngày <b>{int(screen.get('independent_days', 0))}</b> / "
        f"{int(screen.get('development_minimum_days', 5))}\n"
        f"📐 Quan sát: Win <code>{_percent(metrics.get('win_rate'))}%</code> | "
        f"PF <code>{_metric(metrics.get('profit_factor'))}</code> | "
        f"Mean <code>{_metric(metrics.get('mean_r'), 3)}R</code>\n"
        "🔒 <b>NGHIÊN CỨU PAPER / KHÔNG PHẢI TÍN HIỆU VÀO LỆNH</b>"
    )


class ResearchCloudSupervisor:
    def __init__(self, telegram: Any, enabled: bool | None = None) -> None:
        auto_enable = Path("/var/data").is_dir()
        self.enabled = (
            env_bool("CORE4_RESEARCH_CLOUD_ENABLED", auto_enable)
            if enabled is None
            else enabled
        )
        self.telegram = telegram
        default_root = Path("/var/data/r64_research")
        self.root = Path(os.getenv("CORE4_RESEARCH_ROOT", str(default_root)))
        self.flow_root = self.root / "r63a"
        self.positioning_root = self.root / "r63c"
        self.dataset_root = self.root / "r64a"
        self.screen_root = self.root / "r64b"
        self.status_path = self.root / "R64_CLOUD_STATUS.json"
        self.pipeline_interval = max(
            60, int(os.getenv("CORE4_RESEARCH_PIPELINE_SECONDS", "300"))
        )
        self.heartbeat_interval = max(
            3600, int(os.getenv("CORE4_RESEARCH_HEARTBEAT_SECONDS", "21600"))
        )
        self.stale_after_seconds = max(
            180, int(os.getenv("CORE4_RESEARCH_STALE_SECONDS", "300"))
        )
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.log_handles: dict[str, Any] = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.started_monotonic = time.monotonic()
        previous = read_json(self.status_path) if self.enabled else {}
        self.state: dict[str, Any] = {
            "stage": "R64_CLOUD_RESEARCH",
            "version": "R64_CLOUD_1",
            "enabled": self.enabled,
            "paper_only": True,
            "real_money_authorized": False,
            "started_at_utc": utc_now_iso(),
            "updated_at_utc": utc_now_iso(),
            "status": "STARTING" if self.enabled else "DISABLED",
            "flow_collector": {},
            "positioning_collector": {},
            "pipeline": previous.get("pipeline", {}),
            "worker_restarts": previous.get("worker_restarts", {}),
            "notification_state": previous.get("notification_state", {}),
            "errors": previous.get("errors", [])[-20:],
        }

    def start(self) -> None:
        if not self.enabled or (self.thread and self.thread.is_alive()):
            return
        for path in (self.flow_root, self.positioning_root, self.dataset_root, self.screen_root):
            path.mkdir(parents=True, exist_ok=True)
        self._save()
        self.thread = threading.Thread(
            target=self._run, name="r64-cloud-supervisor", daemon=True
        )
        self.thread.start()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def healthy(self) -> bool:
        return self.snapshot().get("status") in {"OK", "STARTING", "DISABLED"}

    def _save(self) -> None:
        if not self.enabled:
            return
        with self.lock:
            self.state["updated_at_utc"] = utc_now_iso()
            encoded = json.dumps(self.state, indent=2, sort_keys=True, allow_nan=False) + "\n"
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".json.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(self.status_path)

    def _record_error(self, message: str) -> None:
        with self.lock:
            self.state.setdefault("errors", []).append(
                {"at": utc_now_iso(), "message": message[:1000]}
            )
            self.state["errors"] = self.state["errors"][-20:]
        self._save()

    def _send(self, text: str) -> bool:
        try:
            result = self.telegram.send(text)
        except Exception as exc:  # Telegram must never stop data collection.
            self._record_error(f"telegram: {type(exc).__name__}: {exc}")
            return False
        return bool(result.get("ok"))

    def _open_log(self, name: str) -> Any:
        path = self.root / f"{name}.log"
        if path.exists() and path.stat().st_size > 5_000_000:
            path.replace(path.with_suffix(".log.previous"))
        handle = path.open("ab", buffering=0)
        self.log_handles[name] = handle
        return handle

    def _spawn(self, name: str) -> None:
        output_root = self.flow_root if name == "r63a" else self.positioning_root
        module = (
            "research.r63a_continuous_multivenue_collector"
            if name == "r63a"
            else "research.r63c_positioning_liquidation_collector"
        )
        command = [
            sys.executable,
            "-m",
            module,
            "--output-root",
            str(output_root),
            "--symbols",
            ",".join(SYMBOLS),
        ]
        if name == "r63c":
            command.extend(["--binance-poll-seconds", "30"])
        handle = self._open_log(name)
        environment = dict(os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        self.processes[name] = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            stdout=handle,
            stderr=handle,
        )
        with self.lock:
            restarts = self.state.setdefault("worker_restarts", {})
            restarts[name] = int(restarts.get(name, 0)) + 1
        self._save()

    def _ensure_workers(self) -> None:
        for name in ("r63a", "r63c"):
            process = self.processes.get(name)
            if process is not None and process.poll() is None:
                continue
            if process is not None:
                code = process.returncode
                self._record_error(f"{name} exited with code {code}; restarting")
                handle = self.log_handles.pop(name, None)
                if handle:
                    handle.close()
                self.stop_event.wait(5.0)
            if not self.stop_event.is_set():
                self._spawn(name)

    def _refresh_collectors(self) -> None:
        flow = read_json(self.flow_root / "R63A_STATUS.json")
        positioning = read_json(self.positioning_root / "R63C_STATUS.json")
        with self.lock:
            self.state["flow_collector"] = flow
            self.state["positioning_collector"] = positioning

    def _collectors_ok(self) -> bool:
        with self.lock:
            flow = self.state.get("flow_collector", {})
            positioning = self.state.get("positioning_collector", {})
        flow_connected, flow_total = connected_count(flow, EXPECTED_FLOW_STREAMS)
        pos_connected, pos_total = connected_count(positioning, EXPECTED_POSITIONING_STREAMS)
        flow_age = age_seconds(flow.get("updated_at_utc"))
        positioning_age = age_seconds(positioning.get("updated_at_utc"))
        flow_fresh = flow_age is not None and flow_age <= self.stale_after_seconds
        pos_fresh = (
            positioning_age is not None
            and positioning_age <= self.stale_after_seconds
        )
        workers_alive = all(
            process.poll() is None for process in self.processes.values()
        ) and len(self.processes) == 2
        return (
            workers_alive
            and flow_connected == flow_total
            and pos_connected == pos_total
            and flow_fresh
            and pos_fresh
        )

    def _run_pipeline(self) -> None:
        from research.r63b_multivenue_collector_audit import audit as audit_flow
        from research.r63d_positioning_collector_audit import audit as audit_positioning
        from research.r64a_positioning_flow_dataset import run as build_dataset
        from research.r64b_frozen_mechanism_screen import run as run_screen

        started = time.monotonic()
        previous_dataset = self.state.get("pipeline", {}).get("dataset", {})
        flow_audit = audit_flow(self.flow_root, minimum_common_minutes=2)
        pos_audit = audit_positioning(self.positioning_root, minimum_common_minutes=2)
        (self.flow_root / "R63B_AUDIT.json").write_text(
            json.dumps(flow_audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (self.positioning_root / "R63D_AUDIT.json").write_text(
            json.dumps(pos_audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        dataset: dict[str, Any] = previous_dataset
        screen: dict[str, Any] = self.state.get("pipeline", {}).get("screen", {})
        pipeline_status = "WAITING_FOR_CONTIGUOUS_DATA"
        if flow_audit.get("pass") and pos_audit.get("pass"):
            dataset = build_dataset(
                self.flow_root / "R63A_FLOW.sqlite3",
                self.positioning_root / "R63C_POSITIONING.sqlite3",
                self.dataset_root,
            )
            screen = run_screen(
                self.dataset_root / "R64A_CAUSAL_ROWS.csv", self.screen_root
            )
            pipeline_status = "OK"
        pipeline = {
            "status": pipeline_status,
            "last_run_utc": utc_now_iso(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "flow_audit": flow_audit,
            "positioning_audit": pos_audit,
            "dataset": dataset,
            "screen": screen,
        }
        with self.lock:
            self.state["pipeline"] = pipeline
        self._save()
        self._notify_label_milestones(previous_dataset, dataset)

    def _notify_label_milestones(
        self, previous: dict[str, Any], current: dict[str, Any]
    ) -> None:
        previous_by_tf = previous.get("samples_by_timeframe", {})
        current_by_tf = current.get("samples_by_timeframe", {})
        notification_state = self.state.setdefault("notification_state", {})
        first_timeframes = [
            timeframe
            for timeframe in ("1h", "4h")
            if int(previous_by_tf.get(timeframe, 0)) == 0
            and int(current_by_tf.get(timeframe, 0)) > 0
            and not notification_state.get(f"first_{timeframe}_sent")
        ]
        if not first_timeframes:
            return
        payload = self.snapshot()
        if self._send(format_research_status(payload, "ĐÃ CÓ NHÃN ĐẦU TIÊN")):
            with self.lock:
                for timeframe in first_timeframes:
                    notification_state[f"first_{timeframe}_sent"] = utc_now_iso()
            self._save()

    def _update_health_and_notify(self) -> None:
        startup_grace = time.monotonic() - self.started_monotonic < self.stale_after_seconds
        current = "OK" if self._collectors_ok() else ("STARTING" if startup_grace else "DEGRADED")
        with self.lock:
            previous = self.state.get("status", "STARTING")
            self.state["status"] = current
            notification_state = self.state.setdefault("notification_state", {})
        if current == "OK" and not notification_state.get("ready_sent"):
            if self._send(format_research_status(self.snapshot(), "THU THẬP 24/7 ĐÃ SẴN SÀNG")):
                with self.lock:
                    notification_state["ready_sent"] = utc_now_iso()
                    notification_state["last_heartbeat_utc"] = utc_now_iso()
        elif current == "DEGRADED" and previous != "DEGRADED":
            self._send(format_research_status(self.snapshot(), "CẢNH BÁO GIÁN ĐOẠN"))
        elif current == "OK" and previous == "DEGRADED":
            self._send(format_research_status(self.snapshot(), "ĐÃ PHỤC HỒI"))
        self._save()

    def _maybe_heartbeat(self) -> None:
        notification_state = self.state.setdefault("notification_state", {})
        last = age_seconds(notification_state.get("last_heartbeat_utc"))
        if last is not None and last < self.heartbeat_interval:
            return
        if self._send(format_research_status(self.snapshot(), "BÁO CÁO ĐỊNH KỲ")):
            with self.lock:
                notification_state["last_heartbeat_utc"] = utc_now_iso()
            self._save()

    def _run(self) -> None:
        next_pipeline = time.monotonic() + 180.0
        try:
            while not self.stop_event.is_set():
                try:
                    self._ensure_workers()
                    self._refresh_collectors()
                    if time.monotonic() >= next_pipeline:
                        self._run_pipeline()
                        next_pipeline = time.monotonic() + self.pipeline_interval
                    self._update_health_and_notify()
                    if self.state.get("status") == "OK":
                        self._maybe_heartbeat()
                except Exception as exc:
                    self._record_error(f"supervisor: {type(exc).__name__}: {exc}")
                self.stop_event.wait(10.0)
        finally:
            for process in self.processes.values():
                if process.poll() is None:
                    process.terminate()
            for handle in self.log_handles.values():
                handle.close()

