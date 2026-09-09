from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


class HyperliquidClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        retries: int | None = None,
        retry_sleep_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("HYPERLIQUID_API_BASE_URL") or "https://api.hyperliquid.xyz").rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = int(retries if retries is not None else os.getenv("HYPERLIQUID_HTTP_RETRIES") or "1")
        self.retry_sleep_seconds = float(
            retry_sleep_seconds
            if retry_sleep_seconds is not None
            else os.getenv("HYPERLIQUID_HTTP_RETRY_SLEEP_SECONDS") or "0.5"
        )

    def _post_info(self, payload: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            f"{self.base_url}/info",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "crypto-paper-signal-bot/0.1",
            },
        )
        attempts = max(self.retries + 1, 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(self.retry_sleep_seconds * (attempt + 1))
        assert last_error is not None
        raise last_error

    def l2_book(self, coin: str, n_sig_figs: int | None = 5, mantissa: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "l2Book", "coin": coin}
        if n_sig_figs is not None:
            payload["nSigFigs"] = n_sig_figs
        if mantissa is not None:
            payload["mantissa"] = mantissa
        return self._post_info(payload)
