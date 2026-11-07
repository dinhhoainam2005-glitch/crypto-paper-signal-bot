"""Finite BTC-only Binance/Bybit public WebSocket capture."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from websockets.asyncio.client import connect

from . import SYMBOL

BINANCE_PUBLIC_STREAMS = (
    "btcusdt@depth@100ms",
)
BINANCE_MARKET_STREAMS = (
    "btcusdt@aggTrade",
    "btcusdt@markPrice@1s",
    "btcusdt@forceOrder",
)
BINANCE_PUBLIC_URL = "wss://fstream.binance.com/public/ws"
BINANCE_MARKET_URL = "wss://fstream.binance.com/market/ws"
BINANCE_DEPTH_SNAPSHOT_URL = "https://fapi.binance.com/fapi/v1/depth"

BYBIT_TOPICS = (
    f"publicTrade.{SYMBOL}",
    f"orderbook.50.{SYMBOL}",
    f"tickers.{SYMBOL}",
    f"allLiquidation.{SYMBOL}",
)
BYBIT_URL = "wss://stream.bybit.com/v5/public/linear"

REQUIRED = {
    "binance": {
        "binance_depth_snapshot",
        "btcusdt@aggTrade",
        "btcusdt@depth@100ms",
        "btcusdt@markPrice@1s",
    },
    "bybit": set(BYBIT_TOPICS[:3]),
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


@dataclass
class StreamAudit:
    venue: str
    counts: Counter[str] = field(default_factory=Counter)
    malformed: int = 0
    sequence_breaks: int = 0
    sequence_regressions: int = 0
    duplicate_updates: int = 0
    observed_lag_ms: list[float] = field(default_factory=list)
    previous_binance_u: int | None = None
    previous_bybit_u: int | None = None
    error: str | None = None

    def observe(self, payload: dict[str, Any], receipt_ms: int) -> str:
        if self.venue == "binance":
            data = payload.get("data", payload)
            if "stream" in payload:
                topic = str(payload["stream"])
            elif {"lastUpdateId", "bids", "asks"}.issubset(payload):
                topic = "binance_depth_snapshot"
            elif "result" in payload and "id" in payload:
                topic = "subscribe"
            else:
                event_type = data.get("e") if isinstance(data, dict) else None
                topic = {
                    "aggTrade": "btcusdt@aggTrade",
                    "depthUpdate": "btcusdt@depth@100ms",
                    "markPriceUpdate": "btcusdt@markPrice@1s",
                    "forceOrder": "btcusdt@forceOrder",
                }.get(event_type, "unknown")
            event_ms = data.get("E") if isinstance(data, dict) else None
            if topic.endswith("@depth@100ms") and isinstance(data, dict):
                previous = data.get("pu")
                current = data.get("u")
                if self.previous_binance_u is not None and previous != self.previous_binance_u:
                    self.sequence_breaks += 1
                if current is not None:
                    self.previous_binance_u = int(current)
        else:
            topic = str(payload.get("topic", payload.get("op", "control")))
            data = payload.get("data")
            event_ms = payload.get("ts")
            if topic.startswith("orderbook.") and isinstance(data, dict):
                current = int(data["u"])
                if self.previous_bybit_u is not None:
                    if current < self.previous_bybit_u and current != 1:
                        self.sequence_regressions += 1
                    elif current == self.previous_bybit_u:
                        self.duplicate_updates += 1
                self.previous_bybit_u = current

        self.counts[topic] += 1
        if event_ms is not None:
            self.observed_lag_ms.append(receipt_ms - int(event_ms))
        return topic

    def summary(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "counts": dict(sorted(self.counts.items())),
            "malformed": self.malformed,
            "sequence_breaks": self.sequence_breaks,
            "sequence_regressions": self.sequence_regressions,
            "duplicate_updates": self.duplicate_updates,
            "observed_lag_ms": {
                "count": len(self.observed_lag_ms),
                "median": statistics.median(self.observed_lag_ms) if self.observed_lag_ms else None,
                "p95": _percentile(self.observed_lag_ms, 0.95),
                "min": min(self.observed_lag_ms) if self.observed_lag_ms else None,
                "max": max(self.observed_lag_ms) if self.observed_lag_ms else None,
            },
            "error": self.error,
        }


def _write_payload(
    handle: Any,
    venue: str,
    receipt_ns: int,
    payload: dict[str, Any],
    transport: str,
    original_receipt_ns: int | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("market message must be a JSON object")
    record = {
        "venue": venue,
        "transport": transport,
        "receipt_time_ns": receipt_ns,
        "receipt_time_utc": datetime.fromtimestamp(receipt_ns / 1_000_000_000, UTC).isoformat(),
        "payload": payload,
    }
    if original_receipt_ns is not None and original_receipt_ns != receipt_ns:
        record["original_receipt_time_ns"] = original_receipt_ns
        record["receipt_time_adjustment"] = "causal_snapshot_bootstrap"
    handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
    handle.flush()
    return payload


def _write_line(handle: Any, venue: str, receipt_ns: int, raw_message: str) -> dict[str, Any]:
    payload = json.loads(raw_message)
    return _write_payload(handle, venue, receipt_ns, payload, "websocket")


def _download_binance_depth_snapshot(timeout: float = 10.0) -> tuple[dict[str, Any], int]:
    query = urlencode({"symbol": SYMBOL, "limit": 1000})
    request = Request(
        f"{BINANCE_DEPTH_SNAPSHOT_URL}?{query}",
        headers={"User-Agent": "btc-market-structure-lab/0.1"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    receipt_ns = time.time_ns()
    if not isinstance(payload, dict):
        raise ValueError("Binance depth snapshot must be a JSON object")
    if not {"lastUpdateId", "bids", "asks"}.issubset(payload):
        raise ValueError("Binance depth snapshot is missing required fields")
    return payload, receipt_ns


def _binance_depth_data(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data", payload)
    if isinstance(data, dict) and data.get("e") == "depthUpdate":
        return data
    return None


def _prepare_binance_bootstrap(
    snapshot: dict[str, Any],
    snapshot_receipt_ns: int,
    buffered: list[tuple[int, dict[str, Any]]],
) -> tuple[
    list[tuple[int, dict[str, Any]]],
    list[tuple[int, int, dict[str, Any]]],
    int,
]:
    """Place buffered deltas at the causal instant the snapshot became usable."""
    snapshot_id = int(snapshot["lastUpdateId"])
    before_snapshot: list[tuple[int, dict[str, Any]]] = []
    after_snapshot: list[tuple[int, int, dict[str, Any]]] = []
    stale_depth = 0
    logical_receipt_ns = snapshot_receipt_ns
    first_nonstale: dict[str, Any] | None = None

    for original_receipt_ns, payload in buffered:
        depth = _binance_depth_data(payload)
        if depth is None and original_receipt_ns < snapshot_receipt_ns:
            before_snapshot.append((original_receipt_ns, payload))
            continue
        if depth is not None and int(depth["u"]) <= snapshot_id:
            stale_depth += 1
            continue
        if depth is not None and first_nonstale is None:
            first_nonstale = depth
        logical_receipt_ns = max(logical_receipt_ns + 1, original_receipt_ns)
        after_snapshot.append((logical_receipt_ns, original_receipt_ns, payload))

    if first_nonstale is not None:
        first = int(first_nonstale["U"])
        last = int(first_nonstale["u"])
        if not (first <= snapshot_id + 1 <= last):
            raise ValueError("buffered Binance depth does not bridge REST snapshot")
    return before_snapshot, after_snapshot, stale_depth


async def _initialize_binance_depth(
    ws: Any,
    deadline: float,
    handle: Any,
    audit: StreamAudit,
) -> int:
    buffered: list[tuple[int, dict[str, Any]]] = []
    saw_depth = False
    loop = asyncio.get_running_loop()

    while not saw_depth and (remaining := deadline - loop.time()) > 0:
        message = await asyncio.wait_for(ws.recv(), timeout=remaining)
        original_receipt_ns = time.time_ns()
        try:
            payload = json.loads(message)
            if not isinstance(payload, dict):
                raise ValueError("market message must be a JSON object")
        except (TypeError, ValueError, json.JSONDecodeError):
            audit.malformed += 1
            continue
        buffered.append((original_receipt_ns, payload))
        saw_depth = _binance_depth_data(payload) is not None

    if not saw_depth:
        raise TimeoutError("Binance depth stream did not become active before snapshot")

    snapshot_attempts = 0
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("Binance depth bootstrap exceeded capture deadline")
        snapshot_attempts += 1
        snapshot_task = asyncio.create_task(
            asyncio.to_thread(
                _download_binance_depth_snapshot,
                max(1.0, min(10.0, remaining)),
            )
        )
        while not snapshot_task.done() and (remaining := deadline - loop.time()) > 0:
            try:
                message = await asyncio.wait_for(
                    ws.recv(), timeout=min(0.05, remaining)
                )
            except TimeoutError:
                continue
            original_receipt_ns = time.time_ns()
            try:
                payload = json.loads(message)
                if not isinstance(payload, dict):
                    raise ValueError("market message must be a JSON object")
            except (TypeError, ValueError, json.JSONDecodeError):
                audit.malformed += 1
                continue
            buffered.append((original_receipt_ns, payload))

        if not snapshot_task.done():
            snapshot_task.cancel()
            await asyncio.gather(snapshot_task, return_exceptions=True)
            raise TimeoutError("Binance depth snapshot exceeded capture deadline")
        snapshot, snapshot_receipt_ns = await snapshot_task
        snapshot_id = int(snapshot["lastUpdateId"])
        while not any(
            (depth := _binance_depth_data(payload)) is not None
            and int(depth["u"]) > snapshot_id
            for _, payload in buffered
        ):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("Binance depth stream produced no snapshot bridge")
            message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            original_receipt_ns = time.time_ns()
            try:
                payload = json.loads(message)
                if not isinstance(payload, dict):
                    raise ValueError("market message must be a JSON object")
            except (TypeError, ValueError, json.JSONDecodeError):
                audit.malformed += 1
                continue
            buffered.append((original_receipt_ns, payload))
        try:
            before, after, stale_depth = _prepare_binance_bootstrap(
                snapshot, snapshot_receipt_ns, buffered
            )
            break
        except ValueError as exc:
            if "does not bridge" not in str(exc):
                raise
            audit.counts["bootstrap_snapshot_retries"] += 1

    for receipt_ns, payload in before:
        _write_payload(handle, "binance", receipt_ns, payload, "websocket")
        audit.observe(payload, receipt_ns // 1_000_000)
    _write_payload(
        handle, "binance", snapshot_receipt_ns, snapshot, "rest_snapshot"
    )
    audit.observe(snapshot, snapshot_receipt_ns // 1_000_000)
    audit.counts["bootstrap_snapshot_attempts"] += snapshot_attempts
    audit.counts["bootstrap_stale_depth_dropped"] += stale_depth

    last_written_ns = snapshot_receipt_ns
    for receipt_ns, original_receipt_ns, payload in after:
        _write_payload(
            handle,
            "binance",
            receipt_ns,
            payload,
            "websocket",
            original_receipt_ns,
        )
        audit.observe(payload, receipt_ns // 1_000_000)
        last_written_ns = receipt_ns
    return last_written_ns


async def _collect_binance_route(
    deadline: float,
    path: Path,
    audit: StreamAudit,
    url: str,
    streams: tuple[str, ...],
    capture_depth_snapshot: bool = False,
) -> None:
    try:
        async with connect(url, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(
                json.dumps({"method": "SUBSCRIBE", "params": list(streams), "id": 1})
            )
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                last_written_ns: int | None = None
                if capture_depth_snapshot:
                    last_written_ns = await _initialize_binance_depth(
                        ws, deadline, handle, audit
                    )
                while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except TimeoutError:
                        break
                    original_receipt_ns = time.time_ns()
                    receipt_ns = (
                        original_receipt_ns
                        if last_written_ns is None
                        else max(original_receipt_ns, last_written_ns + 1)
                    )
                    try:
                        payload = json.loads(message)
                        _write_payload(
                            handle,
                            "binance",
                            receipt_ns,
                            payload,
                            "websocket",
                            original_receipt_ns,
                        )
                        audit.observe(payload, receipt_ns // 1_000_000)
                        last_written_ns = receipt_ns
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                        audit.malformed += 1
    except Exception as exc:
        audit.error = f"{type(exc).__name__}: {exc}"


async def _bybit_pinger(ws: Any, deadline: float) -> None:
    while deadline - asyncio.get_running_loop().time() > 20:
        await asyncio.sleep(20)
        await ws.send(json.dumps({"op": "ping"}))


async def _collect_bybit(deadline: float, path: Path, audit: StreamAudit) -> None:
    try:
        async with connect(BYBIT_URL, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": list(BYBIT_TOPICS)}))
            pinger = asyncio.create_task(_bybit_pinger(ws, deadline))
            try:
                with path.open("w", encoding="utf-8", newline="\n") as handle:
                    while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=remaining)
                        except TimeoutError:
                            break
                        receipt_ns = time.time_ns()
                        try:
                            payload = _write_line(handle, "bybit", receipt_ns, message)
                            audit.observe(payload, receipt_ns // 1_000_000)
                        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                            audit.malformed += 1
            finally:
                pinger.cancel()
                await asyncio.gather(pinger, return_exceptions=True)
    except Exception as exc:
        audit.error = f"{type(exc).__name__}: {exc}"


def _readiness(audits: dict[str, StreamAudit]) -> tuple[bool, dict[str, list[str]]]:
    missing: dict[str, list[str]] = {}
    for venue, required in REQUIRED.items():
        observed = set(audits[venue].counts)
        missing[venue] = sorted(required - observed)
    ready = all(not items for items in missing.values()) and all(
        audit.error is None and audit.malformed == 0 for audit in audits.values()
    )
    return ready, missing


async def capture(duration_seconds: float, output_root: Path) -> Path:
    run_dir = output_root / _stamp()
    run_dir.mkdir(parents=True, exist_ok=False)
    started_utc = _utc_now()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration_seconds
    audits = {venue: StreamAudit(venue) for venue in ("binance", "bybit")}

    await asyncio.gather(
        _collect_binance_route(
            deadline,
            run_dir / "binance_public.jsonl",
            audits["binance"],
            BINANCE_PUBLIC_URL,
            BINANCE_PUBLIC_STREAMS,
            True,
        ),
        _collect_binance_route(
            deadline,
            run_dir / "binance_market.jsonl",
            audits["binance"],
            BINANCE_MARKET_URL,
            BINANCE_MARKET_STREAMS,
        ),
        _collect_bybit(deadline, run_dir / "bybit.jsonl", audits["bybit"]),
    )

    ready, missing = _readiness(audits)
    manifest = {
        "schema_version": 1,
        "project": "btc-market-structure-lab",
        "symbol": SYMBOL,
        "started_at_utc": started_utc,
        "completed_at_utc": _utc_now(),
        "requested_duration_seconds": duration_seconds,
        "core_streams_ready": ready,
        "missing_required_streams": missing,
        "binance_routes": {
            "public": {"url": BINANCE_PUBLIC_URL, "streams": list(BINANCE_PUBLIC_STREAMS)},
            "market": {"url": BINANCE_MARKET_URL, "streams": list(BINANCE_MARKET_STREAMS)},
        },
        "binance_depth_snapshot_url": BINANCE_DEPTH_SNAPSHOT_URL,
        "bybit_url": BYBIT_URL,
        "bybit_topics": list(BYBIT_TOPICS),
        "audits": {venue: audit.summary() for venue, audit in audits.items()},
        "note": "Observed lag includes local clock offset and transport/processing delay.",
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/streams"))
    args = parser.parse_args()
    if args.duration <= 0 or args.duration > 900:
        parser.error("duration must be greater than 0 and at most 900 seconds")
    manifest_path = asyncio.run(capture(args.duration, args.output_root))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(json.dumps({"manifest": str(manifest_path), "core_streams_ready": manifest["core_streams_ready"]}))
    return 0 if manifest["core_streams_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
