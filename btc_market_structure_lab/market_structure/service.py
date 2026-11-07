"""Supervised HTTP service for the BTC-only forward recorder."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .forward_recorder import _write_heartbeat, record_forward, study_is_sealed
from .notifier import TelegramNotifier, format_operational_message


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def service_snapshot(data_root: Path, started_at_utc: str) -> dict[str, Any]:
    heartbeat_path = data_root / "heartbeat.json"
    heartbeat: dict[str, Any] | None = None
    if heartbeat_path.exists():
        try:
            heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            heartbeat = None
    return {
        "service": "btc-market-structure-forward",
        "instrument": "BTCUSDT_PERPETUAL",
        "mode": "RESEARCH_DATA_ONLY",
        "started_at_utc": started_at_utc,
        "server_time_utc": _utc_now(),
        "telegram_notifications_configured": TelegramNotifier.from_environment()
        is not None,
        "heartbeat": heartbeat,
    }


def _http_response(status: int, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    reason = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}[status]
    headers = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    return headers + body


async def _handle_http(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    data_root: Path,
    started_at_utc: str,
) -> None:
    try:
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
        first_line = request.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
        parts = first_line.split()
        method = parts[0] if parts else ""
        path = parts[1].split("?", 1)[0] if len(parts) > 1 else ""
        snapshot = service_snapshot(data_root, started_at_utc)
        heartbeat = snapshot["heartbeat"]

        if method != "GET":
            response = _http_response(404, {"error": "not_found"})
        elif path == "/health":
            response = _http_response(200, {"status": "alive", **snapshot})
        elif path == "/ready":
            ready = heartbeat is not None and heartbeat.get("status") == "OK"
            response = _http_response(
                200 if ready else 503,
                {"status": "ready" if ready else "not_ready", **snapshot},
            )
        elif path in {"/", "/status"}:
            response = _http_response(200, snapshot)
        else:
            response = _http_response(404, {"error": "not_found"})
        writer.write(response)
        await writer.drain()
    except (asyncio.IncompleteReadError, TimeoutError, ValueError):
        writer.write(_http_response(404, {"error": "bad_request"}))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _supervise_recorder(
    duration: float,
    overlap: float,
    data_root: Path,
    detector_every: int,
    raw_retention_segments: int,
) -> None:
    while True:
        if study_is_sealed(data_root):
            await asyncio.sleep(60)
            continue
        try:
            await record_forward(
                duration,
                overlap,
                0,
                data_root,
                detector_every,
                raw_retention_segments,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _write_heartbeat(
                data_root,
                {
                    "status": "DEGRADED",
                    "service_error": f"{type(exc).__name__}: {exc}",
                },
            )
            await asyncio.sleep(5)


async def _notification_loop(
    data_root: Path,
    notifier: TelegramNotifier,
    interval_seconds: float,
) -> None:
    heartbeat_path = data_root / "heartbeat.json"
    initial_sent = False
    last_status: str | None = None
    last_sent_at = 0.0
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(10)
        if not heartbeat_path.exists():
            continue
        try:
            heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        status = str(heartbeat.get("status", "STARTING"))
        now = loop.time()
        should_send = (
            not initial_sent
            or status != last_status
            or now - last_sent_at >= interval_seconds
        )
        if not should_send:
            continue
        try:
            await notifier.send_async(
                format_operational_message(heartbeat, startup=not initial_sent)
            )
        except Exception as exc:
            _write_heartbeat(
                data_root,
                {
                    "telegram": {
                        "status": "ERROR",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                },
            )
            await asyncio.sleep(min(interval_seconds, 300))
            continue
        initial_sent = True
        last_status = status
        last_sent_at = now
        _write_heartbeat(
            data_root,
            {"telegram": {"status": "OK", "last_sent_at_utc": _utc_now()}},
        )


async def serve(
    host: str,
    port: int,
    duration: float,
    overlap: float,
    data_root: Path,
    detector_every: int,
    raw_retention_segments: int,
    telegram_heartbeat_seconds: float,
) -> None:
    started_at_utc = _utc_now()
    data_root.mkdir(parents=True, exist_ok=True)
    server = await asyncio.start_server(
        lambda reader, writer: _handle_http(
            reader, writer, data_root, started_at_utc
        ),
        host,
        port,
    )
    recorder = asyncio.create_task(
        _supervise_recorder(
            duration,
            overlap,
            data_root,
            detector_every,
            raw_retention_segments,
        )
    )
    notifier = TelegramNotifier.from_environment()
    notification = (
        asyncio.create_task(
            _notification_loop(data_root, notifier, telegram_heartbeat_seconds)
        )
        if notifier is not None
        else None
    )
    try:
        async with server:
            await server.serve_forever()
    finally:
        recorder.cancel()
        tasks = [recorder]
        if notification is not None:
            notification.cancel()
            tasks.append(notification)
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "10000")))
    parser.add_argument(
        "--duration",
        type=float,
        default=float(os.getenv("SEGMENT_DURATION_SECONDS", "300")),
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=float(os.getenv("SEGMENT_OVERLAP_SECONDS", "30")),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(os.getenv("DATA_ROOT", "artifacts/forward_service")),
    )
    parser.add_argument(
        "--detector-every-segments",
        type=int,
        default=int(os.getenv("DETECTOR_EVERY_SEGMENTS", "320")),
    )
    parser.add_argument(
        "--raw-retention-segments",
        type=int,
        default=int(os.getenv("RAW_RETENTION_SEGMENTS", "2")),
    )
    parser.add_argument(
        "--telegram-heartbeat-seconds",
        type=float,
        default=float(os.getenv("TELEGRAM_HEARTBEAT_SECONDS", "3600")),
    )
    args = parser.parse_args()
    if args.duration <= 5 or args.duration > 3600:
        parser.error("duration must be greater than 5 and at most 3600 seconds")
    if args.overlap < 1 or args.overlap >= args.duration:
        parser.error("overlap must be at least 1 second and less than duration")
    if args.detector_every_segments < 0:
        parser.error("detector-every-segments cannot be negative")
    if args.raw_retention_segments < 0:
        parser.error("raw-retention-segments cannot be negative")
    if args.telegram_heartbeat_seconds < 60:
        parser.error("telegram-heartbeat-seconds must be at least 60")
    try:
        asyncio.run(
            serve(
                args.host,
                args.port,
                args.duration,
                args.overlap,
                args.data_root,
                args.detector_every_segments,
                args.raw_retention_segments,
                args.telegram_heartbeat_seconds,
            )
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
