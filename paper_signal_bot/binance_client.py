from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any


class BinanceFuturesClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        self.base_url = (base_url or os.getenv("BINANCE_FAPI_BASE_URL") or "https://fapi.binance.com").rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{query}" if query else f"{self.base_url}{path}"
        request = urllib.request.Request(url, headers={"User-Agent": "crypto-paper-signal-bot/0.1"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)

    def klines(self, symbol: str, interval: str, limit: int = 200) -> list[list[Any]]:
        return self._get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})

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
