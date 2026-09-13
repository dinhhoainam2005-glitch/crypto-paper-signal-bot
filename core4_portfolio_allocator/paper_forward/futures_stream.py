from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .clients import Candle, SOURCE_USDM_FUTURES


DAY_MS = 86_400_000
MINUTE_MS = 60_000
ARCHIVE_BASE = "https://data.binance.vision/data/futures/um"
STREAM_BASE = "wss://fstream.binance.com/stream?streams="
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")


def normalize_epoch_ms(value: Any) -> int:
    timestamp = int(value)
    return timestamp // 1000 if timestamp >= 100_000_000_000_000 else timestamp


def candle_from_row(row: list[Any]) -> Candle:
    return Candle(
        open_time_ms=normalize_epoch_ms(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        close_time_ms=normalize_epoch_ms(row[6]),
    )


def parse_archive_zip(payload: bytes) -> list[Candle]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError("unexpected Binance archive layout")
        with archive.open(members[0]) as raw:
            rows = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            candles = [candle_from_row(row) for row in rows if row and row[0].isdigit()]
    if not candles:
        raise RuntimeError("Binance archive contains no candles")
    return candles


def shift_month(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


class BinanceVisionDailyArchive:
    def __init__(self, cache_root: Path, timeout_seconds: float = 20.0) -> None:
        self.cache_root = cache_root
        self.timeout_seconds = timeout_seconds
        self.lock = threading.RLock()
        self.memory: dict[str, list[Candle]] = {}

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_root / f"{symbol.lower()}_daily_verified.json"

    def _read_cache(self, symbol: str) -> tuple[dict[int, Candle], set[str]]:
        path = self._cache_path(symbol)
        if not path.exists():
            return {}, set()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            candles = {
                int(item["open_time_ms"]): Candle(**item)
                for item in payload.get("candles", [])
            }
            return candles, set(payload.get("verified_urls", []))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}, set()

    def _save_cache(
        self, symbol: str, candles: dict[int, Candle], verified_urls: set[str]
    ) -> None:
        path = self._cache_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": "BINANCE_VISION_USDM_CHECKSUM_VERIFIED",
            "symbol": symbol,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "verified_urls": sorted(verified_urls),
            "candles": [
                candle.__dict__ for _, candle in sorted(candles.items())[-500:]
            ],
        }
        temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _get(self, url: str) -> bytes:
        request = urllib.request.Request(
            url, headers={"User-Agent": "core4-v7-futures-stream/1.0"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read()

    def _verified_zip(self, url: str) -> bytes:
        payload = self._get(url)
        checksum = self._get(f"{url}.CHECKSUM").decode("ascii", errors="strict")
        expected = checksum.strip().split()[0].lower()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise RuntimeError(f"checksum mismatch for {url}")
        return payload

    def _merge_url(
        self,
        url: str,
        candles: dict[int, Candle],
        verified_urls: set[str],
    ) -> None:
        if url in verified_urls:
            return
        try:
            payload = self._verified_zip(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return
            raise
        for candle in parse_archive_zip(payload):
            candles[candle.open_time_ms] = candle
        verified_urls.add(url)

    def load(self, symbol: str) -> list[Candle]:
        with self.lock:
            if symbol in self.memory:
                return list(self.memory[symbol])
            candles, verified_urls = self._read_cache(symbol)
            month_start = datetime.now(timezone.utc).date().replace(day=1)
            for offset in range(14, 0, -1):
                month = shift_month(month_start, -offset)
                url = (
                    f"{ARCHIVE_BASE}/monthly/klines/{symbol}/1d/"
                    f"{symbol}-1d-{month:%Y-%m}.zip"
                )
                self._merge_url(url, candles, verified_urls)

            today = datetime.now(timezone.utc).date()
            for offset in range(45, 0, -1):
                day = today - timedelta(days=offset)
                open_ms = int(
                    datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
                    * 1000
                )
                if open_ms in candles:
                    continue
                url = (
                    f"{ARCHIVE_BASE}/daily/klines/{symbol}/1d/"
                    f"{symbol}-1d-{day:%Y-%m-%d}.zip"
                )
                self._merge_url(url, candles, verified_urls)

            ordered = [candle for _, candle in sorted(candles.items())][-500:]
            if len(ordered) < 259:
                raise RuntimeError(
                    f"insufficient verified futures archive for {symbol}: {len(ordered)}"
                )
            self._save_cache(symbol, candles, verified_urls)
            self.memory[symbol] = ordered
            return list(ordered)


class BinanceFuturesStreamClient:
    data_transport = "BINANCE_VISION_ARCHIVE_PLUS_FSTREAM_WEBSOCKET"

    def __init__(
        self,
        cache_path: Path,
        archive_root: Path,
        symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
        start_stream: bool = True,
    ) -> None:
        self.symbols = tuple(symbol.upper() for symbol in symbols)
        self.cache_path = cache_path
        self.archive = BinanceVisionDailyArchive(archive_root)
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.daily_stream: dict[str, dict[int, Candle]] = {
            symbol: {} for symbol in self.symbols
        }
        self.minute_stream: dict[str, dict[int, Candle]] = {
            symbol: {} for symbol in self.symbols
        }
        self.funding_stream: dict[str, dict[int, dict[str, Any]]] = {
            symbol: {} for symbol in self.symbols
        }
        self.last_mark: dict[str, dict[str, Any]] = {}
        self.last_event_ms: int | None = None
        self.last_persist_monotonic = 0.0
        self.stream_state = "STARTING" if start_stream else "DISABLED_FOR_TEST"
        self.stream_error: str | None = None
        self._load_stream_cache()
        if start_stream:
            threading.Thread(
                target=self._stream_loop,
                name="core4-binance-futures-stream",
                daemon=True,
            ).start()

    def _load_stream_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            with self.lock:
                for symbol, items in payload.get("daily", {}).items():
                    if symbol in self.daily_stream:
                        self.daily_stream[symbol] = {
                            int(item["open_time_ms"]): Candle(**item) for item in items
                        }
                for symbol, items in payload.get("minutes", {}).items():
                    if symbol in self.minute_stream:
                        self.minute_stream[symbol] = {
                            int(item["open_time_ms"]): Candle(**item) for item in items
                        }
                for symbol, items in payload.get("funding", {}).items():
                    if symbol in self.funding_stream:
                        self.funding_stream[symbol] = {
                            int(item["fundingTime"]): item for item in items
                        }
                self.last_mark = dict(payload.get("last_mark", {}))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return

    def _save_stream_cache(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_persist_monotonic < 30.0:
            return
        with self.lock:
            payload = {
                "source": SOURCE_USDM_FUTURES,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "daily": {
                    symbol: [
                        candle.__dict__
                        for _, candle in sorted(items.items())[-5:]
                    ]
                    for symbol, items in self.daily_stream.items()
                },
                "minutes": {
                    symbol: [
                        candle.__dict__
                        for _, candle in sorted(items.items())[-3000:]
                    ]
                    for symbol, items in self.minute_stream.items()
                },
                "funding": {
                    symbol: [item for _, item in sorted(items.items())[-300:]]
                    for symbol, items in self.funding_stream.items()
                },
                "last_mark": self.last_mark,
            }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(
            f"{self.cache_path.suffix}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.cache_path)
        self.last_persist_monotonic = now

    def _stream_url(self) -> str:
        streams: list[str] = []
        for symbol in self.symbols:
            lowered = symbol.lower()
            streams.extend(
                (
                    f"{lowered}@kline_1d",
                    f"{lowered}@kline_1m",
                    f"{lowered}@markPrice@1s",
                )
            )
        return STREAM_BASE + "/".join(streams)

    @staticmethod
    def _candle_from_stream(kline: dict[str, Any]) -> Candle:
        return Candle(
            open_time_ms=normalize_epoch_ms(kline["t"]),
            open=float(kline["o"]),
            high=float(kline["h"]),
            low=float(kline["l"]),
            close=float(kline["c"]),
            close_time_ms=normalize_epoch_ms(kline["T"]),
        )

    def _handle_mark(self, event: dict[str, Any]) -> None:
        symbol = str(event.get("s", "")).upper()
        if symbol not in self.funding_stream:
            return
        current = {
            "nextFundingTime": normalize_epoch_ms(event["T"]),
            "fundingRate": float(event["r"]),
            "markPrice": float(event["p"]),
            "eventTime": normalize_epoch_ms(event["E"]),
        }
        previous = self.last_mark.get(symbol)
        if previous and previous["nextFundingTime"] != current["nextFundingTime"]:
            funding_time = int(previous["nextFundingTime"])
            self.funding_stream[symbol][funding_time] = {
                "symbol": symbol,
                "fundingTime": funding_time,
                "fundingRate": str(previous["fundingRate"]),
                "markPrice": str(previous["markPrice"]),
                "source": "BINANCE_USDM_MARK_PRICE_STREAM",
            }
            self.funding_stream[symbol] = dict(
                sorted(self.funding_stream[symbol].items())[-300:]
            )
        self.last_mark[symbol] = current

    def _handle_message(self, message: dict[str, Any]) -> None:
        event = message.get("data", message)
        if not isinstance(event, dict):
            return
        with self.condition:
            event_type = event.get("e")
            if event_type == "kline":
                kline = event.get("k", {})
                symbol = str(kline.get("s", event.get("s", ""))).upper()
                if symbol not in self.daily_stream:
                    return
                candle = self._candle_from_stream(kline)
                if kline.get("i") == "1d":
                    self.daily_stream[symbol][candle.open_time_ms] = candle
                    self.daily_stream[symbol] = dict(
                        sorted(self.daily_stream[symbol].items())[-5:]
                    )
                elif kline.get("i") == "1m" and bool(kline.get("x")):
                    self.minute_stream[symbol][candle.open_time_ms] = candle
                    self.minute_stream[symbol] = dict(
                        sorted(self.minute_stream[symbol].items())[-3000:]
                    )
            elif event_type == "markPriceUpdate":
                self._handle_mark(event)
            self.last_event_ms = int(time.time() * 1000)
            self.condition.notify_all()
        self._save_stream_cache()

    def _stream_loop(self) -> None:
        try:
            import websocket  # type: ignore[import-not-found]
        except ImportError:
            self.stream_state = "DEPENDENCY_MISSING"
            self.stream_error = "websocket-client is not installed"
            return
        backoff = 1.0
        while True:
            connection = None
            try:
                connection = websocket.create_connection(
                    self._stream_url(), timeout=30, enable_multithread=True
                )
                self.stream_state = "CONNECTED"
                self.stream_error = None
                backoff = 1.0
                while True:
                    try:
                        raw = connection.recv()
                    except websocket.WebSocketTimeoutException:
                        connection.ping()
                        continue
                    if raw:
                        self._handle_message(json.loads(raw))
            except Exception as exc:
                self.stream_state = "RECONNECTING"
                self.stream_error = f"{type(exc).__name__}: {exc}"
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

    def _current_daily(self, symbol: str, wait_seconds: float = 10.0) -> Candle:
        current_open = int(time.time() * 1000) // DAY_MS * DAY_MS
        deadline = time.monotonic() + wait_seconds
        with self.condition:
            while current_open not in self.daily_stream[symbol]:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise RuntimeError(
                        f"futures daily stream not ready for {symbol}: {self.stream_state}"
                    )
                self.condition.wait(remaining)
            return self.daily_stream[symbol][current_open]

    def daily_candles_with_source(
        self, symbol: str, limit: int = 260
    ) -> tuple[list[Candle], str]:
        symbol = symbol.upper()
        history = self.archive.load(symbol)
        self._current_daily(symbol)
        with self.lock:
            merged = {candle.open_time_ms: candle for candle in history}
            merged.update(self.daily_stream[symbol])
            candles = [candle for _, candle in sorted(merged.items())][-limit:]
        if len(candles) < limit:
            raise RuntimeError(f"insufficient futures candles for {symbol}: {len(candles)}")
        return candles, SOURCE_USDM_FUTURES

    def daily_candles(self, symbol: str, limit: int = 260) -> list[Candle]:
        candles, _ = self.daily_candles_with_source(symbol, limit)
        return candles

    def minute_candles_with_source(
        self, symbol: str, start_ms: int, end_ms: int, max_minutes: int
    ) -> tuple[list[Candle], str]:
        symbol = symbol.upper()
        if end_ms < start_ms:
            return [], SOURCE_USDM_FUTURES
        if (end_ms - start_ms) // MINUTE_MS + 1 > max_minutes:
            raise RuntimeError(f"minute replay exceeds limit for {symbol}")
        first_open = (start_ms + MINUTE_MS - 1) // MINUTE_MS * MINUTE_MS
        last_open = (end_ms - (MINUTE_MS - 1)) // MINUTE_MS * MINUTE_MS
        if last_open < first_open:
            return [], SOURCE_USDM_FUTURES
        with self.lock:
            candles = [
                self.minute_stream[symbol][timestamp]
                for timestamp in range(first_open, last_open + 1, MINUTE_MS)
                if timestamp in self.minute_stream[symbol]
            ]
        expected = (last_open - first_open) // MINUTE_MS + 1
        if len(candles) != expected:
            raise RuntimeError(
                f"futures minute stream gap for {symbol}: {len(candles)}/{expected}"
            )
        return candles, SOURCE_USDM_FUTURES

    def minute_candles(
        self, symbol: str, start_ms: int, end_ms: int, max_minutes: int
    ) -> list[Candle]:
        candles, _ = self.minute_candles_with_source(
            symbol, start_ms, end_ms, max_minutes
        )
        return candles

    def funding_rates(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        if end_ms < start_ms:
            return []
        with self.lock:
            return [
                item
                for timestamp, item in sorted(
                    self.funding_stream[symbol.upper()].items()
                )
                if start_ms <= timestamp <= end_ms
            ]

    def ticker_price_with_source(self, symbol: str) -> tuple[float, str]:
        candle = self._current_daily(symbol.upper())
        return candle.close, SOURCE_USDM_FUTURES

    def ticker_price(self, symbol: str) -> float:
        value, _ = self.ticker_price_with_source(symbol)
        return value
