from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "forward_data" / "R63A_multivenue_flow"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
BINANCE_WS = "wss://fstream.binance.com/stream?streams="
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
HYPERLIQUID_WS = "wss://api.hyperliquid.xyz/ws"
VERSION = "R63A_6"
MINUTE_MS = 60_000


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


def finite_positive(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("expected finite positive number")
    return result


@dataclass(frozen=True)
class TradeEvent:
    venue: str
    symbol: str
    event_ms: int
    observed_ms: int
    side: str
    price: float
    size: float
    trade_id: str


@dataclass(frozen=True)
class QuoteEvent:
    venue: str
    symbol: str
    event_ms: int
    observed_ms: int
    bid: float
    bid_size: float
    ask: float
    ask_size: float


NormalizedEvent = TradeEvent | QuoteEvent


def is_binance_trade_placeholder(message: dict[str, Any]) -> bool:
    payload = message.get("data", message)
    if not isinstance(payload, dict) or payload.get("e") != "trade" or payload.get("X") != "NA":
        return False
    try:
        return float(payload.get("p")) == 0.0 and float(payload.get("q")) == 0.0
    except (TypeError, ValueError):
        return False


def parse_binance(message: dict[str, Any], observed_ms: int) -> list[NormalizedEvent]:
    payload = message.get("data", message)
    if not isinstance(payload, dict):
        return []
    if is_binance_trade_placeholder(message):
        return []
    event_type = payload.get("e")
    symbol = str(payload.get("s", "")).upper()
    if event_type in {"aggTrade", "trade"} and symbol:
        trade_id = payload.get("a") if event_type == "aggTrade" else payload.get("t")
        return [TradeEvent(
            "binance-futures", symbol, int(payload.get("T") or payload.get("E") or observed_ms),
            observed_ms, "SELL" if bool(payload.get("m")) else "BUY",
            finite_positive(payload["p"]), finite_positive(payload["q"]),
            str(trade_id if trade_id is not None else ""),
        )]
    if event_type == "bookTicker" and symbol:
        return [QuoteEvent(
            "binance-futures", symbol, int(payload.get("T") or payload.get("E") or observed_ms),
            observed_ms, finite_positive(payload["b"]), finite_positive(payload["B"]),
            finite_positive(payload["a"]), finite_positive(payload["A"]),
        )]
    return []


class BybitBook:
    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    @staticmethod
    def update_side(target: dict[float, float], levels: Iterable[Iterable[Any]]) -> None:
        for raw_price, raw_size in levels:
            price = finite_positive(raw_price)
            size = float(raw_size)
            if not math.isfinite(size) or size < 0:
                raise ValueError("invalid Bybit order-book size")
            if size == 0:
                target.pop(price, None)
            else:
                target[price] = size

    def apply(self, kind: str, bids: list, asks: list) -> tuple[float, float, float, float] | None:
        if kind == "snapshot":
            self.bids.clear()
            self.asks.clear()
        self.update_side(self.bids, bids)
        self.update_side(self.asks, asks)
        if not self.bids or not self.asks:
            return None
        bid = max(self.bids)
        ask = min(self.asks)
        if bid > ask:
            raise ValueError("crossed Bybit book")
        return bid, self.bids[bid], ask, self.asks[ask]


def parse_bybit(
    message: dict[str, Any], observed_ms: int, books: dict[str, BybitBook]
) -> list[NormalizedEvent]:
    topic = str(message.get("topic", ""))
    if topic.startswith("publicTrade."):
        output: list[NormalizedEvent] = []
        for trade in message.get("data", []):
            symbol = str(trade.get("s", "")).upper()
            side = str(trade.get("S", "")).upper()
            if symbol and side in {"BUY", "SELL"}:
                output.append(TradeEvent(
                    "bybit", symbol, int(trade.get("T") or message.get("ts") or observed_ms),
                    observed_ms, side, finite_positive(trade["p"]),
                    finite_positive(trade["v"]), str(trade.get("i", "")),
                ))
        return sorted(output, key=lambda event: (event.event_ms, event.trade_id))
    if topic.startswith("orderbook.1."):
        data = message.get("data", {})
        symbol = str(data.get("s") or topic.rsplit(".", 1)[-1]).upper()
        book = books.setdefault(symbol, BybitBook())
        top = book.apply(str(message.get("type", "delta")), data.get("b", []), data.get("a", []))
        if top is None:
            return []
        bid, bid_size, ask, ask_size = top
        return [QuoteEvent(
            "bybit", symbol, int(message.get("cts") or message.get("ts") or observed_ms),
            observed_ms, bid, bid_size, ask, ask_size,
        )]
    return []


def parse_hyperliquid(message: dict[str, Any], observed_ms: int) -> list[NormalizedEvent]:
    channel = message.get("channel")
    data = message.get("data")
    if channel == "trades" and isinstance(data, list):
        output: list[NormalizedEvent] = []
        for trade in data:
            coin = str(trade.get("coin", "")).upper()
            side = {"B": "BUY", "A": "SELL"}.get(str(trade.get("side", "")).upper())
            if coin and side:
                trade_id = f"{trade.get('time', '')}:{coin}:{trade.get('tid', trade.get('hash', ''))}"
                output.append(TradeEvent(
                    "hyperliquid", f"{coin}USDT", int(trade.get("time") or observed_ms),
                    observed_ms, side, finite_positive(trade["px"]),
                    finite_positive(trade["sz"]), trade_id,
                ))
        return sorted(output, key=lambda event: (event.event_ms, event.trade_id))
    if channel == "l2Book" and isinstance(data, dict):
        coin = str(data.get("coin", "")).upper()
        levels = data.get("levels", [[], []])
        if not coin or len(levels) != 2 or not levels[0] or not levels[1]:
            return []
        bid_level, ask_level = levels[0][0], levels[1][0]
        return [QuoteEvent(
            "hyperliquid", f"{coin}USDT", int(data.get("time") or observed_ms),
            observed_ms, finite_positive(bid_level["px"]), finite_positive(bid_level["sz"]),
            finite_positive(ask_level["px"]), finite_positive(ask_level["sz"]),
        )]
    return []


class TradeDeduplicator:
    def __init__(self, maximum: int = 100_000) -> None:
        self.maximum = maximum
        self.queue: deque[tuple[str, str, str]] = deque()
        self.seen: set[tuple[str, str, str]] = set()
        self.lock = threading.Lock()

    def accept(self, event: TradeEvent) -> bool:
        if not event.trade_id:
            return True
        key = (event.venue, event.symbol, event.trade_id)
        with self.lock:
            if key in self.seen:
                return False
            self.queue.append(key)
            self.seen.add(key)
            while len(self.queue) > self.maximum:
                self.seen.discard(self.queue.popleft())
        return True


@dataclass
class MinuteAccumulator:
    venue: str
    symbol: str
    minute_start_ms: int
    generation: int
    trade_count: int = 0
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    quote_count: int = 0
    midpoint_open: float | None = None
    midpoint_high: float | None = None
    midpoint_low: float | None = None
    midpoint_close: float | None = None
    spread_bps_sum: float = 0.0
    spread_bps_close: float | None = None
    book_imbalance_sum: float = 0.0
    book_imbalance_close: float | None = None
    first_event_ms: int | None = None
    last_event_ms: int | None = None
    first_observed_ms: int | None = None
    last_observed_ms: int | None = None
    max_latency_ms: int = 0

    def observe_time(self, event_ms: int, observed_ms: int) -> None:
        self.first_event_ms = event_ms if self.first_event_ms is None else min(self.first_event_ms, event_ms)
        self.last_event_ms = event_ms if self.last_event_ms is None else max(self.last_event_ms, event_ms)
        self.first_observed_ms = observed_ms if self.first_observed_ms is None else min(self.first_observed_ms, observed_ms)
        self.last_observed_ms = observed_ms if self.last_observed_ms is None else max(self.last_observed_ms, observed_ms)
        self.max_latency_ms = max(self.max_latency_ms, observed_ms - event_ms)

    def add_trade(self, event: TradeEvent) -> None:
        self.observe_time(event.event_ms, event.observed_ms)
        self.trade_count += 1
        notional = event.price * event.size
        if event.side == "BUY":
            self.buy_notional += notional
        else:
            self.sell_notional += notional

    def add_quote(self, event: QuoteEvent) -> None:
        if event.bid > event.ask:
            raise ValueError("crossed quote")
        self.observe_time(event.event_ms, event.observed_ms)
        midpoint = (event.bid + event.ask) / 2.0
        spread = (event.ask - event.bid) / midpoint * 10_000.0
        imbalance = (event.bid_size - event.ask_size) / (event.bid_size + event.ask_size)
        self.quote_count += 1
        self.midpoint_open = midpoint if self.midpoint_open is None else self.midpoint_open
        self.midpoint_high = midpoint if self.midpoint_high is None else max(self.midpoint_high, midpoint)
        self.midpoint_low = midpoint if self.midpoint_low is None else min(self.midpoint_low, midpoint)
        self.midpoint_close = midpoint
        self.spread_bps_sum += spread
        self.spread_bps_close = spread
        self.book_imbalance_sum += imbalance
        self.book_imbalance_close = imbalance

    def row(self) -> dict[str, Any]:
        return {
            "venue": self.venue, "symbol": self.symbol,
            "minute_start_ms": self.minute_start_ms,
            "minute_utc": utc_iso(self.minute_start_ms),
            "generation": self.generation,
            "trade_count": self.trade_count,
            "buy_notional": self.buy_notional,
            "sell_notional": self.sell_notional,
            "quote_count": self.quote_count,
            "midpoint_open": self.midpoint_open,
            "midpoint_high": self.midpoint_high,
            "midpoint_low": self.midpoint_low,
            "midpoint_close": self.midpoint_close,
            "spread_bps_mean": self.spread_bps_sum / self.quote_count if self.quote_count else None,
            "spread_bps_close": self.spread_bps_close,
            "book_imbalance_mean": self.book_imbalance_sum / self.quote_count if self.quote_count else None,
            "book_imbalance_close": self.book_imbalance_close,
            "first_event_ms": self.first_event_ms,
            "last_event_ms": self.last_event_ms,
            "first_observed_ms": self.first_observed_ms,
            "last_observed_ms": self.last_observed_ms,
            "max_latency_ms": self.max_latency_ms,
            "complete": int(self.quote_count > 0),
        }


class MinuteAggregator:
    def __init__(self, symbols: tuple[str, ...], grace_seconds: float = 10.0) -> None:
        self.symbols = set(symbols)
        self.grace_ms = int(max(grace_seconds, 1.0) * 1000)
        self.lock = threading.Lock()
        self.cells: dict[tuple[str, str, int], MinuteAccumulator] = {}
        self.flushed_through: dict[tuple[str, str], int] = {}
        self.generation: dict[str, int] = {venue: 0 for venue in ("binance-futures", "bybit", "hyperliquid")}
        self.generation_started_ms: dict[str, int] = {}
        self.late_events = 0
        self.replay_events = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.out_of_order_by_stream: dict[str, int] = {}
        self.last_event_ms: dict[tuple[str, str, str], int] = {}
        self.deduplicator = TradeDeduplicator()

    def new_generation(self, venue: str, started_ms: int | None = None) -> int:
        with self.lock:
            self.generation[venue] = self.generation.get(venue, 0) + 1
            self.generation_started_ms[venue] = started_ms if started_ms is not None else now_ms()
            self.last_event_ms = {
                key: value for key, value in self.last_event_ms.items() if key[0] != venue
            }
            return self.generation[venue]

    def seed_flushed_through(self, latest_minutes: dict[tuple[str, str], int]) -> None:
        with self.lock:
            for key, minute in latest_minutes.items():
                self.flushed_through[key] = max(self.flushed_through.get(key, -1), minute)

    def add(self, event: NormalizedEvent) -> bool:
        if event.symbol not in self.symbols:
            return False
        if isinstance(event, TradeEvent) and not self.deduplicator.accept(event):
            with self.lock:
                self.duplicates += 1
            return False
        minute = event.event_ms - event.event_ms % MINUTE_MS
        cell_key = (event.venue, event.symbol)
        event_kind = "trade" if isinstance(event, TradeEvent) else "quote"
        order_key = (event.venue, event.symbol, event_kind)
        with self.lock:
            if minute <= self.flushed_through.get(cell_key, -1):
                if event.event_ms < self.generation_started_ms.get(event.venue, 0):
                    self.replay_events += 1
                else:
                    self.late_events += 1
                return False
            prior = self.last_event_ms.get(order_key)
            if prior is not None and event.event_ms < prior:
                self.out_of_order += 1
                stream_key = ":".join(order_key)
                self.out_of_order_by_stream[stream_key] = self.out_of_order_by_stream.get(stream_key, 0) + 1
            self.last_event_ms[order_key] = max(prior or event.event_ms, event.event_ms)
            key = (event.venue, event.symbol, minute)
            cell = self.cells.setdefault(
                key, MinuteAccumulator(event.venue, event.symbol, minute, self.generation.get(event.venue, 0))
            )
            if isinstance(event, TradeEvent):
                cell.add_trade(event)
            else:
                cell.add_quote(event)
        return True

    def pop_closed(self, current_ms: int) -> list[dict[str, Any]]:
        cutoff = current_ms - self.grace_ms
        with self.lock:
            keys = [key for key in self.cells if key[2] + MINUTE_MS <= cutoff]
            rows = []
            for key in sorted(keys, key=lambda value: (value[2], value[0], value[1])):
                cell = self.cells.pop(key)
                rows.append(cell.row())
                self.flushed_through[(cell.venue, cell.symbol)] = max(
                    self.flushed_through.get((cell.venue, cell.symbol), -1), cell.minute_start_ms
                )
            return rows


class SQLiteMinuteStore:
    COLUMNS = (
        "venue", "symbol", "minute_start_ms", "minute_utc", "generation",
        "trade_count", "buy_notional", "sell_notional", "quote_count",
        "midpoint_open", "midpoint_high", "midpoint_low", "midpoint_close",
        "spread_bps_mean", "spread_bps_close", "book_imbalance_mean",
        "book_imbalance_close", "first_event_ms", "last_event_ms",
        "first_observed_ms", "last_observed_ms", "max_latency_ms", "complete",
    )

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS minute_flow ("
            "venue TEXT NOT NULL, symbol TEXT NOT NULL, minute_start_ms INTEGER NOT NULL, "
            "minute_utc TEXT NOT NULL, generation INTEGER NOT NULL, trade_count INTEGER NOT NULL, "
            "buy_notional REAL NOT NULL, sell_notional REAL NOT NULL, quote_count INTEGER NOT NULL, "
            "midpoint_open REAL, midpoint_high REAL, midpoint_low REAL, midpoint_close REAL, "
            "spread_bps_mean REAL, spread_bps_close REAL, book_imbalance_mean REAL, "
            "book_imbalance_close REAL, first_event_ms INTEGER, last_event_ms INTEGER, "
            "first_observed_ms INTEGER, last_observed_ms INTEGER, max_latency_ms INTEGER NOT NULL, "
            "complete INTEGER NOT NULL, PRIMARY KEY(venue, symbol, minute_start_ms))"
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
        placeholders = ",".join("?" for _ in self.COLUMNS)
        columns = ",".join(self.COLUMNS)
        sql = f"INSERT OR IGNORE INTO minute_flow({columns}) VALUES({placeholders})"
        values = [tuple(row[column] for column in self.COLUMNS) for row in rows]
        with self.lock, self.connection:
            self.connection.executemany(sql, values)
        return len(rows)

    def count(self) -> int:
        with self.lock:
            return int(self.connection.execute("SELECT COUNT(*) FROM minute_flow").fetchone()[0])

    def latest_complete_minutes(self) -> dict[tuple[str, str], int]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT venue,symbol,MAX(minute_start_ms) FROM minute_flow "
                "WHERE complete=1 GROUP BY venue,symbol"
            ).fetchall()
        return {(str(venue), str(symbol)): int(minute) for venue, symbol, minute in rows}

    def close(self) -> None:
        with self.lock:
            self.connection.close()


class AtomicStatus:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def save(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        temporary = self.path.with_suffix(f".json.{os.getpid()}.tmp")
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(self.path)


class ContinuousCollector:
    def __init__(self, output_root: Path, symbols: tuple[str, ...], grace_seconds: float = 10.0) -> None:
        self.output_root = output_root
        self.symbols = symbols
        self.store = SQLiteMinuteStore(output_root / "R63A_FLOW.sqlite3")
        self.aggregator = MinuteAggregator(symbols, grace_seconds)
        self.aggregator.seed_flushed_through(self.store.latest_complete_minutes())
        self.status_store = AtomicStatus(output_root / "R63A_STATUS.json")
        self.stop_event = threading.Event()
        self.status_lock = threading.Lock()
        self.bybit_books: dict[str, BybitBook] = {}
        self.incomplete_minutes_dropped = 0
        self.status: dict[str, Any] = {
            "stage": "R63A_CONTINUOUS_MULTIVENUE_FLOW",
            "version": VERSION,
            "paper_only": True,
            "auto_trade": False,
            "symbols": list(symbols),
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "streams": {venue: "STARTING" for venue in ("binance-futures", "bybit", "hyperliquid")},
            "events": {}, "ignored_events": {}, "minutes_written": self.store.count(), "errors": [],
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
            self.status["replay_events_ignored"] = self.aggregator.replay_events
            self.status["duplicate_trades"] = self.aggregator.duplicates
            self.status["out_of_order_events"] = self.aggregator.out_of_order
            self.status["out_of_order_by_stream"] = dict(self.aggregator.out_of_order_by_stream)
            self.status["incomplete_minutes_dropped"] = self.incomplete_minutes_dropped
            snapshot = json.loads(json.dumps(self.status))
        self.status_store.save(snapshot)

    def ingest(self, events: list[NormalizedEvent]) -> None:
        for event in events:
            if self.aggregator.add(event):
                key = f"{event.venue}:{event.symbol}:{'trade' if isinstance(event, TradeEvent) else 'quote'}"
                with self.status_lock:
                    self.status["events"][key] = int(self.status["events"].get(key, 0)) + 1

    def persist_closed(self, rows: list[dict[str, Any]]) -> int:
        complete = [row for row in rows if int(row["complete"]) == 1]
        self.incomplete_minutes_dropped += len(rows) - len(complete)
        return self.store.write(complete)

    def websocket_loop(self, venue: str, url: str, subscribe, parser, heartbeat=None) -> None:
        try:
            import websocket  # type: ignore[import-not-found]
        except ImportError:
            self.update_status(venue, "DEPENDENCY_MISSING", "websocket-client is required")
            return
        backoff = 1.0
        while not self.stop_event.is_set():
            connection = None
            try:
                connection = websocket.create_connection(url, timeout=5, enable_multithread=True)
                self.aggregator.new_generation(venue)
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
                    if not raw:
                        continue
                    observed = now_ms()
                    message = json.loads(raw)
                    self.ingest(parser(message, observed))
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

    def binance_loop(self) -> None:
        trade_stream = os.getenv("R63A_BINANCE_TRADE_STREAM", "trade").strip()
        if trade_stream not in {"trade", "aggTrade"}:
            self.update_status(
                "binance-futures", "INVALID_CONFIGURATION",
                f"binance-futures: unsupported trade stream {trade_stream!r}",
            )
            return
        streams = [
            f"{symbol.lower()}@{kind}"
            for symbol in self.symbols
            for kind in (trade_stream, "bookTicker")
        ]
        url = os.getenv("R63A_BINANCE_WS", BINANCE_WS + "/".join(streams))

        def parser(message, observed):
            if is_binance_trade_placeholder(message):
                with self.status_lock:
                    key = "binance-futures:zero_trade_placeholder"
                    ignored = self.status["ignored_events"]
                    ignored[key] = int(ignored.get(key, 0)) + 1
            return parse_binance(message, observed)

        self.websocket_loop("binance-futures", url, lambda connection: None, parser)

    def bybit_loop(self) -> None:
        def subscribe(connection) -> None:
            args = [topic for symbol in self.symbols for topic in (f"publicTrade.{symbol}", f"orderbook.1.{symbol}")]
            connection.send(json.dumps({"op": "subscribe", "args": args}))

        def parser(message, observed):
            return parse_bybit(message, observed, self.bybit_books)

        def heartbeat(connection) -> None:
            connection.send(json.dumps({"op": "ping"}))

        self.websocket_loop("bybit", os.getenv("R63A_BYBIT_WS", BYBIT_WS), subscribe, parser, heartbeat)

    def hyperliquid_loop(self) -> None:
        def subscribe(connection) -> None:
            for symbol in self.symbols:
                coin = symbol.removesuffix("USDT")
                for kind in ("trades", "l2Book"):
                    connection.send(json.dumps({"method": "subscribe", "subscription": {"type": kind, "coin": coin}}))

        def heartbeat(connection) -> None:
            connection.send(json.dumps({"method": "ping"}))

        self.websocket_loop(
            "hyperliquid", os.getenv("R63A_HYPERLIQUID_WS", HYPERLIQUID_WS),
            subscribe, parse_hyperliquid, heartbeat,
        )

    def flush_loop(self) -> None:
        while not self.stop_event.wait(1.0):
            rows = self.aggregator.pop_closed(now_ms())
            if rows:
                self.persist_closed(rows)
                self.update_status()

    def run(self, duration_seconds: float = 0.0) -> None:
        threads = [
            threading.Thread(target=self.binance_loop, name="r63a-binance", daemon=True),
            threading.Thread(target=self.bybit_loop, name="r63a-bybit", daemon=True),
            threading.Thread(target=self.hyperliquid_loop, name="r63a-hyperliquid", daemon=True),
            threading.Thread(target=self.flush_loop, name="r63a-flush", daemon=True),
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
            thread.join(timeout=10.0)
        rows = self.aggregator.pop_closed(now_ms())
        self.persist_closed(rows)
        self.update_status()
        self.store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect R63A multi-venue CORE4 flow into local SQLite.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--grace-seconds", type=float, default=10.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = tuple(dict.fromkeys(item.strip().upper() for item in args.symbols.split(",") if item.strip()))
    if not symbols:
        raise SystemExit("no symbols")
    collector = ContinuousCollector(args.output_root, symbols, args.grace_seconds)

    def stop_handler(_signum, _frame) -> None:
        collector.stop_event.set()

    signal.signal(signal.SIGINT, stop_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_handler)
    collector.run(args.duration_seconds)


if __name__ == "__main__":
    main()
