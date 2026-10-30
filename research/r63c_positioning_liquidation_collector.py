from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from research.r63a_continuous_multivenue_collector import (
    AtomicStatus,
    DEFAULT_SYMBOLS,
    MINUTE_MS,
    finite_positive,
    now_ms,
    utc_iso,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "forward_data" / "R63C_positioning_liquidation"
BINANCE_WS = "wss://fstream.binance.com/stream?streams="
BINANCE_PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
BINANCE_OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
HYPERLIQUID_WS = "wss://api.hyperliquid.xyz/ws"
VERSION = "R63C_1"


def finite_number(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("expected finite number")
    return result


@dataclass(frozen=True)
class ContextEvent:
    venue: str
    symbol: str
    event_ms: int
    observed_ms: int
    mark_price: float
    open_interest: float
    funding_rate: float


@dataclass(frozen=True)
class LiquidationEvent:
    venue: str
    symbol: str
    event_ms: int
    observed_ms: int
    liquidated_side: str
    price: float
    size: float
    event_id: str


PositioningEvent = ContextEvent | LiquidationEvent


def parse_binance_context(
    premium: dict[str, Any], open_interest: dict[str, Any], observed_ms: int
) -> ContextEvent:
    premium_symbol = str(premium.get("symbol", "")).upper()
    oi_symbol = str(open_interest.get("symbol", "")).upper()
    if not premium_symbol or premium_symbol != oi_symbol:
        raise ValueError("Binance context symbol mismatch")
    return ContextEvent(
        "binance-futures",
        premium_symbol,
        int(max(premium.get("time") or 0, open_interest.get("time") or 0, observed_ms)),
        observed_ms,
        finite_positive(premium["markPrice"]),
        finite_positive(open_interest["openInterest"]),
        finite_number(premium["lastFundingRate"]),
    )


def parse_binance_liquidation(message: dict[str, Any], observed_ms: int) -> list[LiquidationEvent]:
    payload = message.get("data", message)
    if not isinstance(payload, dict) or payload.get("e") != "forceOrder":
        return []
    order = payload.get("o", {})
    symbol = str(order.get("s", "")).upper()
    order_side = str(order.get("S", "")).upper()
    if not symbol or order_side not in {"BUY", "SELL"}:
        return []
    raw_price = order.get("ap") or order.get("p")
    raw_size = order.get("z") or order.get("q")
    price = finite_number(raw_price)
    size = finite_number(raw_size)
    if price <= 0 or size <= 0:
        return []
    event_ms = int(order.get("T") or payload.get("E") or observed_ms)
    event_id = f"{symbol}:{order.get('i', '')}:{event_ms}:{order_side}:{price}:{size}"
    return [LiquidationEvent(
        "binance-futures", symbol, event_ms, observed_ms,
        "LONG" if order_side == "SELL" else "SHORT", price, size, event_id,
    )]


class BybitTickerState:
    REQUIRED = ("markPrice", "openInterest", "fundingRate")

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def apply(self, data: dict[str, Any], observed_ms: int, event_ms: int) -> ContextEvent | None:
        self.values.update({key: value for key, value in data.items() if value not in {None, ""}})
        if not all(key in self.values for key in self.REQUIRED):
            return None
        symbol = str(self.values.get("symbol", "")).upper()
        if not symbol:
            return None
        return ContextEvent(
            "bybit", symbol, event_ms, observed_ms,
            finite_positive(self.values["markPrice"]),
            finite_positive(self.values["openInterest"]),
            finite_number(self.values["fundingRate"]),
        )


def parse_bybit(
    message: dict[str, Any], observed_ms: int, states: dict[str, BybitTickerState]
) -> list[PositioningEvent]:
    topic = str(message.get("topic", ""))
    if topic.startswith("tickers."):
        data = message.get("data", {})
        if not isinstance(data, dict):
            return []
        symbol = str(data.get("symbol") or topic.rsplit(".", 1)[-1]).upper()
        state = states.setdefault(symbol, BybitTickerState())
        context = state.apply({**data, "symbol": symbol}, observed_ms, int(message.get("ts") or observed_ms))
        return [context] if context is not None else []
    if topic.startswith("allLiquidation."):
        output: list[PositioningEvent] = []
        for item in message.get("data", []):
            symbol = str(item.get("s", "")).upper()
            side = str(item.get("S", "")).upper()
            if not symbol or side not in {"BUY", "SELL"}:
                continue
            event_ms = int(item.get("T") or message.get("ts") or observed_ms)
            price = finite_positive(item["p"])
            size = finite_positive(item["v"])
            event_id = f"{symbol}:{event_ms}:{side}:{price}:{size}"
            output.append(LiquidationEvent(
                "bybit", symbol, event_ms, observed_ms,
                "LONG" if side == "BUY" else "SHORT", price, size, event_id,
            ))
        return sorted(output, key=lambda event: (event.event_ms, event.event_id))
    return []


def parse_hyperliquid(message: dict[str, Any], observed_ms: int) -> list[ContextEvent]:
    if message.get("channel") != "activeAssetCtx":
        return []
    data = message.get("data", {})
    context = data.get("ctx", {}) if isinstance(data, dict) else {}
    coin = str(data.get("coin", "")).upper() if isinstance(data, dict) else ""
    if not coin or not isinstance(context, dict):
        return []
    mark = context.get("markPx") or context.get("midPx")
    return [ContextEvent(
        "hyperliquid", f"{coin}USDT", int(message.get("time") or observed_ms), observed_ms,
        finite_positive(mark), finite_positive(context["openInterest"]),
        finite_number(context["funding"]),
    )]


class EventDeduplicator:
    def __init__(self, maximum: int = 50_000) -> None:
        self.maximum = maximum
        self.queue: deque[tuple[str, str]] = deque()
        self.seen: set[tuple[str, str]] = set()

    def accept(self, event: LiquidationEvent) -> bool:
        key = (event.venue, event.event_id)
        if key in self.seen:
            return False
        self.seen.add(key)
        self.queue.append(key)
        while len(self.queue) > self.maximum:
            self.seen.discard(self.queue.popleft())
        return True


@dataclass
class MinutePositioning:
    venue: str
    symbol: str
    minute_start_ms: int
    context_count: int = 0
    mark_open: float | None = None
    mark_close: float | None = None
    oi_open: float | None = None
    oi_close: float | None = None
    funding_close: float | None = None
    long_liq_count: int = 0
    short_liq_count: int = 0
    long_liq_notional: float = 0.0
    short_liq_notional: float = 0.0
    first_event_ms: int | None = None
    last_event_ms: int | None = None
    max_latency_ms: int = 0

    def observe(self, event_ms: int, observed_ms: int) -> None:
        self.first_event_ms = event_ms if self.first_event_ms is None else min(self.first_event_ms, event_ms)
        self.last_event_ms = event_ms if self.last_event_ms is None else max(self.last_event_ms, event_ms)
        self.max_latency_ms = max(self.max_latency_ms, observed_ms - event_ms)

    def add(self, event: PositioningEvent) -> None:
        self.observe(event.event_ms, event.observed_ms)
        if isinstance(event, ContextEvent):
            self.context_count += 1
            self.mark_open = event.mark_price if self.mark_open is None else self.mark_open
            self.mark_close = event.mark_price
            self.oi_open = event.open_interest if self.oi_open is None else self.oi_open
            self.oi_close = event.open_interest
            self.funding_close = event.funding_rate
            return
        notional = event.price * event.size
        if event.liquidated_side == "LONG":
            self.long_liq_count += 1
            self.long_liq_notional += notional
        else:
            self.short_liq_count += 1
            self.short_liq_notional += notional

    def row(self) -> dict[str, Any]:
        complete = all(value is not None for value in (self.mark_close, self.oi_close, self.funding_close))
        return {
            "venue": self.venue,
            "symbol": self.symbol,
            "minute_start_ms": self.minute_start_ms,
            "minute_utc": utc_iso(self.minute_start_ms),
            "context_count": self.context_count,
            "mark_open": self.mark_open,
            "mark_close": self.mark_close,
            "oi_open": self.oi_open,
            "oi_close": self.oi_close,
            "funding_close": self.funding_close,
            "long_liq_count": self.long_liq_count,
            "short_liq_count": self.short_liq_count,
            "long_liq_notional": self.long_liq_notional,
            "short_liq_notional": self.short_liq_notional,
            "first_event_ms": self.first_event_ms,
            "last_event_ms": self.last_event_ms,
            "max_latency_ms": self.max_latency_ms,
            "complete": int(complete),
        }


class PositioningAggregator:
    def __init__(self, symbols: tuple[str, ...], grace_seconds: float = 10.0) -> None:
        self.symbols = set(symbols)
        self.grace_ms = int(max(grace_seconds, 1.0) * 1000)
        self.lock = threading.Lock()
        self.cells: dict[tuple[str, str, int], MinutePositioning] = {}
        self.flushed_through: dict[tuple[str, str], int] = {}
        self.late_events = 0
        self.duplicate_liquidations = 0
        self.deduplicator = EventDeduplicator()

    def seed_flushed_through(self, latest: dict[tuple[str, str], int]) -> None:
        self.flushed_through.update(latest)

    def add(self, event: PositioningEvent) -> bool:
        if event.symbol not in self.symbols:
            return False
        with self.lock:
            if isinstance(event, LiquidationEvent) and not self.deduplicator.accept(event):
                self.duplicate_liquidations += 1
                return False
            minute = event.event_ms - event.event_ms % MINUTE_MS
            cell_key = (event.venue, event.symbol)
            if minute <= self.flushed_through.get(cell_key, -1):
                self.late_events += 1
                return False
            key = (event.venue, event.symbol, minute)
            cell = self.cells.setdefault(key, MinutePositioning(event.venue, event.symbol, minute))
            cell.add(event)
        return True

    def pop_closed(self, current_ms: int) -> list[dict[str, Any]]:
        cutoff = current_ms - self.grace_ms
        with self.lock:
            keys = [key for key in self.cells if key[2] + MINUTE_MS <= cutoff]
            rows = []
            for key in sorted(keys):
                cell = self.cells.pop(key)
                rows.append(cell.row())
                cell_key = (cell.venue, cell.symbol)
                self.flushed_through[cell_key] = max(
                    self.flushed_through.get(cell_key, -1), cell.minute_start_ms
                )
        return rows


class PositioningStore:
    COLUMNS = (
        "venue", "symbol", "minute_start_ms", "minute_utc", "context_count",
        "mark_open", "mark_close", "oi_open", "oi_close", "funding_close",
        "long_liq_count", "short_liq_count", "long_liq_notional", "short_liq_notional",
        "first_event_ms", "last_event_ms", "max_latency_ms", "complete",
    )

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS minute_positioning ("
            "venue TEXT NOT NULL, symbol TEXT NOT NULL, minute_start_ms INTEGER NOT NULL, "
            "minute_utc TEXT NOT NULL, context_count INTEGER NOT NULL, "
            "mark_open REAL, mark_close REAL, oi_open REAL, oi_close REAL, funding_close REAL, "
            "long_liq_count INTEGER NOT NULL, short_liq_count INTEGER NOT NULL, "
            "long_liq_notional REAL NOT NULL, short_liq_notional REAL NOT NULL, "
            "first_event_ms INTEGER, last_event_ms INTEGER, max_latency_ms INTEGER NOT NULL, "
            "complete INTEGER NOT NULL, PRIMARY KEY(venue,symbol,minute_start_ms))"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('collector_version', ?)", (VERSION,)
        )
        self.connection.commit()

    def write(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        columns = ",".join(self.COLUMNS)
        placeholders = ",".join("?" for _ in self.COLUMNS)
        values = [tuple(row[column] for column in self.COLUMNS) for row in rows]
        with self.lock, self.connection:
            self.connection.executemany(
                f"INSERT OR IGNORE INTO minute_positioning({columns}) VALUES({placeholders})", values
            )
        return len(rows)

    def count(self) -> int:
        with self.lock:
            return int(self.connection.execute("SELECT COUNT(*) FROM minute_positioning").fetchone()[0])

    def latest_complete_minutes(self) -> dict[tuple[str, str], int]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT venue,symbol,MAX(minute_start_ms) FROM minute_positioning "
                "WHERE complete=1 GROUP BY venue,symbol"
            ).fetchall()
        return {(str(venue), str(symbol)): int(minute) for venue, symbol, minute in rows}

    def close(self) -> None:
        with self.lock:
            self.connection.close()


class PositioningCollector:
    def __init__(
        self, output_root: Path, symbols: tuple[str, ...], grace_seconds: float = 10.0,
        binance_poll_seconds: float = 30.0,
    ) -> None:
        self.output_root = output_root
        self.symbols = symbols
        self.binance_poll_seconds = max(binance_poll_seconds, 5.0)
        self.store = PositioningStore(output_root / "R63C_POSITIONING.sqlite3")
        self.aggregator = PositioningAggregator(symbols, grace_seconds)
        self.aggregator.seed_flushed_through(self.store.latest_complete_minutes())
        self.status_store = AtomicStatus(output_root / "R63C_STATUS.json")
        self.stop_event = threading.Event()
        self.status_lock = threading.Lock()
        self.bybit_states: dict[str, BybitTickerState] = {}
        self.incomplete_minutes_dropped = 0
        self.status: dict[str, Any] = {
            "stage": "R63C_POSITIONING_LIQUIDATION",
            "version": VERSION,
            "paper_only": True,
            "auto_trade": False,
            "symbols": list(symbols),
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "streams": {
                venue: "STARTING" for venue in
                ("binance-context", "binance-liquidation", "bybit", "hyperliquid")
            },
            "events": {}, "errors": [], "minutes_written": self.store.count(),
        }
        self.status_store.save(self.status)

    def update_status(self, venue: str | None = None, state: str | None = None, error: str | None = None) -> None:
        with self.status_lock:
            self.status["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            if venue and state:
                self.status["streams"][venue] = state
            if error:
                self.status["errors"].append({"at": self.status["updated_at_utc"], "message": error[:500]})
                self.status["errors"] = self.status["errors"][-50:]
            self.status["minutes_written"] = self.store.count()
            self.status["late_events"] = self.aggregator.late_events
            self.status["duplicate_liquidations"] = self.aggregator.duplicate_liquidations
            self.status["incomplete_minutes_dropped"] = self.incomplete_minutes_dropped
            snapshot = json.loads(json.dumps(self.status))
        self.status_store.save(snapshot)

    def ingest(self, events: list[PositioningEvent]) -> None:
        for event in events:
            if self.aggregator.add(event):
                kind = "context" if isinstance(event, ContextEvent) else "liquidation"
                key = f"{event.venue}:{event.symbol}:{kind}"
                with self.status_lock:
                    self.status["events"][key] = int(self.status["events"].get(key, 0)) + 1

    def persist_closed(self, rows: list[dict[str, Any]]) -> int:
        complete = [row for row in rows if int(row["complete"]) == 1]
        self.incomplete_minutes_dropped += len(rows) - len(complete)
        return self.store.write(complete)

    @staticmethod
    def get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
        request = Request(f"{url}?{urlencode(params)}", headers={"User-Agent": "R63C-research/1"})
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("expected JSON object")
        return payload

    def binance_context_loop(self) -> None:
        self.update_status("binance-context", "CONNECTED")
        while not self.stop_event.is_set():
            started = time.monotonic()
            for symbol in self.symbols:
                if self.stop_event.is_set():
                    break
                try:
                    premium = self.get_json(BINANCE_PREMIUM_URL, {"symbol": symbol})
                    oi = self.get_json(BINANCE_OI_URL, {"symbol": symbol})
                    self.ingest([parse_binance_context(premium, oi, now_ms())])
                except Exception as exc:
                    self.update_status(
                        "binance-context", "DEGRADED",
                        f"binance-context:{symbol}: {type(exc).__name__}: {exc}",
                    )
            if self.status["streams"].get("binance-context") == "DEGRADED":
                self.update_status("binance-context", "CONNECTED")
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1.0, self.binance_poll_seconds - elapsed))
        self.update_status("binance-context", "STOPPED")

    def websocket_loop(self, venue: str, url: str, subscribe, parser, heartbeat=None) -> None:
        try:
            import websocket  # type: ignore[import-not-found]
        except ImportError:
            self.update_status(venue, "DEPENDENCY_MISSING", f"{venue}: websocket-client is required")
            return
        backoff = 1.0
        while not self.stop_event.is_set():
            connection = None
            try:
                connection = websocket.create_connection(url, timeout=5, enable_multithread=True)
                subscribe(connection)
                self.update_status(venue, "CONNECTED")
                backoff = 1.0
                last_heartbeat = time.monotonic()
                while not self.stop_event.is_set():
                    try:
                        raw = connection.recv()
                    except websocket.WebSocketTimeoutException:
                        if heartbeat and time.monotonic() - last_heartbeat >= 20:
                            heartbeat(connection)
                            last_heartbeat = time.monotonic()
                        continue
                    if raw:
                        observed = now_ms()
                        self.ingest(parser(json.loads(raw), observed))
                    if heartbeat and time.monotonic() - last_heartbeat >= 20:
                        heartbeat(connection)
                        last_heartbeat = time.monotonic()
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.update_status(venue, "RECONNECTING", f"{venue}: {type(exc).__name__}: {exc}")
                    self.stop_event.wait(backoff)
                    backoff = min(30.0, backoff * 2.0)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
        self.update_status(venue, "STOPPED")

    def binance_liquidation_loop(self) -> None:
        streams = [f"{symbol.lower()}@forceOrder" for symbol in self.symbols]
        url = os.getenv("R63C_BINANCE_WS", BINANCE_WS + "/".join(streams))
        self.websocket_loop("binance-liquidation", url, lambda connection: None, parse_binance_liquidation)

    def bybit_loop(self) -> None:
        def subscribe(connection) -> None:
            args = [
                topic for symbol in self.symbols
                for topic in (f"tickers.{symbol}", f"allLiquidation.{symbol}")
            ]
            connection.send(json.dumps({"op": "subscribe", "args": args}))

        def parser(message, observed):
            return parse_bybit(message, observed, self.bybit_states)

        self.websocket_loop(
            "bybit", os.getenv("R63C_BYBIT_WS", BYBIT_WS), subscribe, parser,
            lambda connection: connection.send(json.dumps({"op": "ping"})),
        )

    def hyperliquid_loop(self) -> None:
        def subscribe(connection) -> None:
            for symbol in self.symbols:
                connection.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "activeAssetCtx", "coin": symbol.removesuffix("USDT")},
                }))

        self.websocket_loop(
            "hyperliquid", os.getenv("R63C_HYPERLIQUID_WS", HYPERLIQUID_WS),
            subscribe, parse_hyperliquid,
            lambda connection: connection.send(json.dumps({"method": "ping"})),
        )

    def flush_loop(self) -> None:
        while not self.stop_event.wait(1.0):
            rows = self.aggregator.pop_closed(now_ms())
            if rows:
                self.persist_closed(rows)
                self.update_status()

    def run(self, duration_seconds: float = 0.0) -> None:
        threads = [
            threading.Thread(target=self.binance_context_loop, name="r63c-binance-context", daemon=True),
            threading.Thread(target=self.binance_liquidation_loop, name="r63c-binance-liq", daemon=True),
            threading.Thread(target=self.bybit_loop, name="r63c-bybit", daemon=True),
            threading.Thread(target=self.hyperliquid_loop, name="r63c-hyperliquid", daemon=True),
            threading.Thread(target=self.flush_loop, name="r63c-flush", daemon=True),
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + duration_seconds if duration_seconds > 0 else None
        while not self.stop_event.wait(1.0):
            self.update_status()
            if deadline is not None and time.monotonic() >= deadline:
                break
        self.stop_event.set()
        for thread in threads:
            thread.join(timeout=15.0)
        self.persist_closed(self.aggregator.pop_closed(now_ms()))
        self.update_status()
        self.store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect R63C public positioning and liquidation data.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--grace-seconds", type=float, default=10.0)
    parser.add_argument("--binance-poll-seconds", type=float, default=30.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = tuple(dict.fromkeys(item.strip().upper() for item in args.symbols.split(",") if item.strip()))
    if not symbols:
        raise SystemExit("no symbols")
    collector = PositioningCollector(
        args.output_root, symbols, args.grace_seconds, args.binance_poll_seconds
    )

    def stop_handler(_signum, _frame) -> None:
        collector.stop_event.set()

    signal.signal(signal.SIGINT, stop_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_handler)
    collector.run(args.duration_seconds)


if __name__ == "__main__":
    main()
