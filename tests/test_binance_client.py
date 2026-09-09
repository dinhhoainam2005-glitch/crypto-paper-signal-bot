from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import patch

from paper_signal_bot.binance_client import BinanceFuturesClient


class FakeResponse:
    def __init__(self, body: str) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.body.encode("utf-8")


def http_error(code: int = 418) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://fapi.binance.com/fapi/v1/klines",
        code=code,
        msg="I'm a teapot",
        hdrs=None,
        fp=None,
    )


class BinanceClientTests(unittest.TestCase):
    def test_klines_falls_back_to_spot_market_data_after_futures_gateway_418(self) -> None:
        client = BinanceFuturesClient(
            base_url="https://fapi.example.test",
            timeout_seconds=1,
            retries=0,
        )
        client.base_urls = ("https://fapi.example.test",)
        rows = [[1, "100", "101", "99", "100.5", "10", 2, "1000", 1, "5", "500", "0"]]

        with patch(
            "urllib.request.urlopen",
            side_effect=[http_error(418), FakeResponse(str(rows).replace("'", '"'))],
        ) as urlopen:
            result = client.klines("BTCUSDT", "1h", 1)

        self.assertEqual(result, rows)
        first_url = urlopen.call_args_list[0].args[0].full_url
        second_url = urlopen.call_args_list[1].args[0].full_url
        self.assertIn("/fapi/v1/klines", first_url)
        self.assertIn("/api/v3/klines", second_url)

    def test_futures_only_open_interest_does_not_use_spot_fallback(self) -> None:
        client = BinanceFuturesClient(
            base_url="https://fapi.example.test",
            timeout_seconds=1,
            retries=0,
        )
        client.base_urls = ("https://fapi.example.test",)

        with patch("urllib.request.urlopen", side_effect=[http_error(418)]) as urlopen:
            with self.assertRaises(urllib.error.HTTPError):
                client.open_interest_hist("BTCUSDT", "5m", 1)

        self.assertEqual(len(urlopen.call_args_list), 1)
        self.assertIn("/futures/data/openInterestHist", urlopen.call_args_list[0].args[0].full_url)


if __name__ == "__main__":
    unittest.main()
