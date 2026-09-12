from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


RETRYABLE = {403, 418, 429, 500, 502, 503, 504}


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

    def _request(
        self,
        path: str,
        params: dict[str, Any],
        spot_fallback_path: str | None = None,
    ) -> Any:
        last_error: Exception | None = None
        futures_urls = self.base_urls
        if spot_fallback_path is not None:
            # Market data must not stall a scan while every futures alias is
            # blocked by the hosting region. Probe the primary once, then use
            # Binance's public market-data host immediately.
            try:
                return self._request_json(self.base_urls[0], path, params)
            except Exception as exc:
                last_error = exc
            try:
                return self._request_json(
                    self.spot_market_base_url, spot_fallback_path, params
                )
            except Exception as exc:
                last_error = exc
            futures_urls = self.base_urls[1:]

        for base_url in futures_urls:
            for attempt in range(self.retries + 1):
                try:
                    return self._request_json(base_url, path, params)
                except Exception as exc:
                    last_error = exc
                    retryable = not isinstance(exc, urllib.error.HTTPError) or exc.code in RETRYABLE
                    if not retryable or attempt >= self.retries:
                        break
                    time.sleep(0.25 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def daily_candles(self, symbol: str, limit: int = 260) -> list[Candle]:
        return parse_candles(
            self._request(
                "/fapi/v1/klines",
                {"symbol": symbol, "interval": "1d", "limit": limit},
                spot_fallback_path="/api/v3/klines",
            )
        )

    def minute_candles(
        self, symbol: str, start_ms: int, end_ms: int, max_minutes: int
    ) -> list[Candle]:
        if end_ms < start_ms:
            return []
        if (end_ms - start_ms) // 60_000 + 1 > max_minutes:
            raise RuntimeError(f"minute replay exceeds limit for {symbol}")
        output: list[list[Any]] = []
        cursor = start_ms
        while cursor <= end_ms:
            page = self._request(
                "/fapi/v1/klines",
                {
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1500,
                },
                spot_fallback_path="/api/v3/klines",
            )
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
        return parse_candles([deduplicated[key] for key in sorted(deduplicated)])

    def ticker_price(self, symbol: str) -> float:
        payload = self._request(
            "/fapi/v2/ticker/price",
            {"symbol": symbol},
            spot_fallback_path="/api/v3/ticker/price",
        )
        return float(payload["price"])

    def funding_rates(self, symbol: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        if end_ms < start_ms:
            return []
        return list(
            self._request(
                "/fapi/v1/fundingRate",
                {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
            )
        )
