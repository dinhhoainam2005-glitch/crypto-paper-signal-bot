from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


RETRYABLE_HTTP_CODES = {403, 418, 429, 500, 502, 503, 504}


class BinanceFuturesClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        retries: int | None = None,
        retry_sleep_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("BINANCE_FAPI_BASE_URL") or "https://fapi.binance.com").rstrip("/")
        configured_base_urls = os.getenv("BINANCE_FAPI_BASE_URLS")
        if configured_base_urls:
            base_urls = [item.strip().rstrip("/") for item in configured_base_urls.split(",") if item.strip()]
        else:
            base_urls = [
                self.base_url,
                "https://fapi1.binance.com",
                "https://fapi2.binance.com",
                "https://fapi3.binance.com",
                "https://fapi4.binance.com",
            ]
        self.base_urls = tuple(dict.fromkeys(base_urls))
        self.spot_market_base_url = (
            os.getenv("BINANCE_SPOT_MARKET_BASE_URL") or "https://data-api.binance.vision"
        ).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = int(retries if retries is not None else os.getenv("BINANCE_HTTP_RETRIES") or "2")
        self.retry_sleep_seconds = float(
            retry_sleep_seconds if retry_sleep_seconds is not None else os.getenv("BINANCE_HTTP_RETRY_SLEEP_SECONDS") or "0.5"
        )

    @staticmethod
    def _retryable_source_error(exc: Exception) -> bool:
        if isinstance(exc, urllib.error.HTTPError):
            return int(exc.code) in RETRYABLE_HTTP_CODES
        return isinstance(exc, (TimeoutError, urllib.error.URLError, OSError))

    def _request_json(self, base_url: str, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        url = f"{base_url}{path}?{query}" if query else f"{base_url}{path}"
        request = urllib.request.Request(url, headers={"User-Agent": "crypto-paper-signal-bot/0.1"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)

    def _get(self, path: str, params: dict[str, Any], spot_fallback_path: str | None = None) -> Any:
        attempts = max(self.retries + 1, 1)
        last_error: Exception | None = None
        for base_url in self.base_urls:
            for attempt in range(attempts):
                try:
                    return self._request_json(base_url, path, params)
                except Exception as exc:
                    last_error = exc
                    if not self._retryable_source_error(exc):
                        raise
                    if isinstance(exc, urllib.error.HTTPError) and int(exc.code) in {403, 418, 429}:
                        break
                    if attempt + 1 >= attempts:
                        break
                    time.sleep(self.retry_sleep_seconds * (attempt + 1))
        if spot_fallback_path is not None:
            try:
                return self._request_json(self.spot_market_base_url, spot_fallback_path, params)
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def klines(self, symbol: str, interval: str, limit: int = 200) -> list[list[Any]]:
        return self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
            spot_fallback_path="/api/v3/klines",
        )

    def depth(self, symbol: str, limit: int = 100) -> dict[str, Any]:
        return self._get(
            "/fapi/v1/depth",
            {"symbol": symbol, "limit": limit},
            spot_fallback_path="/api/v3/depth",
        )

    def ticker_price(self, symbol: str) -> dict[str, Any]:
        return self._get(
            "/fapi/v2/ticker/price",
            {"symbol": symbol},
            spot_fallback_path="/api/v3/ticker/price",
        )

    def open_interest(self, symbol: str) -> dict[str, Any]:
        return self._get("/fapi/v1/openInterest", {"symbol": symbol})

    def open_interest_hist(self, symbol: str, period: str = "5m", limit: int = 30) -> list[dict[str, Any]]:
        return self._get("/futures/data/openInterestHist", {"symbol": symbol, "period": period, "limit": limit})

    def premium_index_klines(self, symbol: str, interval: str, limit: int = 80) -> list[list[Any]]:
        return self._get("/fapi/v1/premiumIndexKlines", {"symbol": symbol, "interval": interval, "limit": limit})

    def derivatives_state_available(self, symbol: str) -> bool:
        try:
            open_interest = self._get("/fapi/v1/openInterest", {"symbol": symbol})
            premium_index = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        except Exception:
            return False
        required_open_interest = bool(open_interest.get("openInterest"))
        required_premium = all(
            premium_index.get(key) not in (None, "")
            for key in ["markPrice", "indexPrice", "lastFundingRate", "nextFundingTime"]
        )
        return required_open_interest and required_premium
