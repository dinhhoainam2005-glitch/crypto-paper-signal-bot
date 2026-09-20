from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RETRYABLE = {403, 418, 429, 500, 502, 503, 504}
SOURCE_USDM_FUTURES = "BINANCE_USDM_FUTURES"
SOURCE_SPOT_FALLBACK = "BINANCE_SPOT_FALLBACK"
SOURCE_MIXED = "MIXED_MARKET_DATA"


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    close_time_ms: int


def parse_candles(rows: list[list[Any]]) -> list[Candle]:
    return [
        Candle(
            open_time_ms=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            close_time_ms=int(row[6]),
        )
        for row in rows
    ]


class BinanceClient:
    def __init__(self, timeout_seconds: float = 10.0, retries: int = 2) -> None:
        configured = os.getenv("CORE4_BINANCE_FAPI_BASE_URLS", "")
        defaults = (
            "https://fapi.binance.com",
            "https://fapi1.binance.com",
            "https://fapi2.binance.com",
            "https://fapi3.binance.com",
            "https://fapi4.binance.com",
            "https://www.binance.com",
        )
        self.base_urls = tuple(
            dict.fromkeys(
                item.strip().rstrip("/")
                for item in (configured.split(",") if configured else defaults)
                if item.strip()
            )
        )
        self.spot_market_base_url = os.getenv(
            "CORE4_BINANCE_SPOT_MARKET_BASE_URL", "https://data-api.binance.vision"
        ).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def _request_json(self, base_url: str, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{base_url}{path}?{query}",
            headers={"User-Agent": "core4-v7-paper-forward/1.0"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request_with_source(
        self,
        path: str,
        params: dict[str, Any],
        spot_fallback_path: str | None = None,
    ) -> tuple[Any, str]:
        last_error: Exception | None = None
        for base_url in self.base_urls:
            for attempt in range(self.retries + 1):
                try:
                    return self._request_json(base_url, path, params), SOURCE_USDM_FUTURES
                except Exception as exc:
                    last_error = exc
                    retryable = not isinstance(exc, urllib.error.HTTPError) or exc.code in RETRYABLE
                    if not retryable or attempt >= self.retries:
                        break
                    time.sleep(0.25 * (attempt + 1))
        if spot_fallback_path is not None:
            try:
                return (
                    self._request_json(
                        self.spot_market_base_url, spot_fallback_path, params
                    ),
                    SOURCE_SPOT_FALLBACK,
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def _request(
        self,
        path: str,
        params: dict[str, Any],
        spot_fallback_path: str | None = None,
    ) -> Any:
        payload, _ = self._request_with_source(path, params, spot_fallback_path)
        return payload

    def daily_candles(self, symbol: str, limit: int = 260) -> list[Candle]:
        candles, _ = self.daily_candles_with_source(symbol, limit)
        return candles

    def daily_candles_with_source(
        self, symbol: str, limit: int = 260
    ) -> tuple[list[Candle], str]:
        payload, source = self._request_with_source(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": "1d", "limit": limit},
            spot_fallback_path="/api/v3/klines",
        )
        return parse_candles(payload), source

    def minute_candles(
        self, symbol: str, start_ms: int, end_ms: int, max_minutes: int
    ) -> list[Candle]:
        candles, _ = self.minute_candles_with_source(
            symbol, start_ms, end_ms, max_minutes
        )
        return candles

    def minute_candles_with_source(
        self, symbol: str, start_ms: int, end_ms: int, max_minutes: int
    ) -> tuple[list[Candle], str]:
        return self._minute_candles_with_source(
            symbol,
            start_ms,
            end_ms,
            max_minutes,
            spot_fallback_path="/api/v3/klines",
        )

    def futures_minute_candles_with_source(
        self, symbol: str, start_ms: int, end_ms: int, max_minutes: int
    ) -> tuple[list[Candle], str]:
        return self._minute_candles_with_source(
            symbol,
            start_ms,
            end_ms,
            max_minutes,
            spot_fallback_path=None,
        )

    def _minute_candles_with_source(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
        max_minutes: int,
        spot_fallback_path: str | None,
    ) -> tuple[list[Candle], str]:
        if end_ms < start_ms:
            return [], SOURCE_USDM_FUTURES
        if (end_ms - start_ms) // 60_000 + 1 > max_minutes:
            raise RuntimeError(f"minute replay exceeds limit for {symbol}")
        output: list[list[Any]] = []
        sources: set[str] = set()
        cursor = start_ms
        while cursor <= end_ms:
            page, source = self._request_with_source(
                "/fapi/v1/klines",
                {
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1500,
                },
                spot_fallback_path=spot_fallback_path,
            )
            sources.add(source)
            if not page:
                break
            output.extend(page)
            next_cursor = int(page[-1][0]) + 60_000
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(page) < 1500:
                break
        deduplicated = {int(row[0]): row for row in output}
        source = next(iter(sources)) if len(sources) == 1 else SOURCE_MIXED
        return parse_candles([deduplicated[key] for key in sorted(deduplicated)]), source

    def ticker_price(self, symbol: str) -> float:
        value, _ = self.ticker_price_with_source(symbol)
        return value

    def ticker_price_with_source(self, symbol: str) -> tuple[float, str]:
        payload, source = self._request_with_source(
            "/fapi/v2/ticker/price",
            {"symbol": symbol},
            spot_fallback_path="/api/v3/ticker/price",
        )
        return float(payload["price"]), source

    def funding_rates(self, symbol: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        if end_ms < start_ms:
            return []
        return list(
            self._request(
                "/fapi/v1/fundingRate",
                {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
            )
        )


def market_client_from_env() -> Any:
    mode = os.getenv("CORE4_BINANCE_DATA_MODE", "rest").strip().lower()
    if mode != "stream_archive":
        return BinanceClient()
    from .futures_stream import BinanceFuturesStreamClient

    state_path = Path(
        os.getenv("CORE4_STATE_PATH", "data/core4_v7_forward_state.json")
    )
    cache_path = Path(
        os.getenv(
            "CORE4_FUTURES_STREAM_CACHE_PATH",
            str(state_path.with_name("core4_v7_futures_stream.json")),
        )
    )
    archive_root = Path(
        os.getenv(
            "CORE4_FUTURES_ARCHIVE_CACHE_DIR",
            str(state_path.parent / "core4_v7_futures_archive"),
        )
    )
    return BinanceFuturesStreamClient(cache_path, archive_root)
