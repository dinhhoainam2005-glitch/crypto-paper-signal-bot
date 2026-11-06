import unittest

from market_structure import SYMBOL
from market_structure.probe import ENDPOINTS, Endpoint, validate_payload


class ProbeContractTests(unittest.TestCase):
    def test_universe_is_btc_only(self) -> None:
        self.assertEqual(SYMBOL, "BTCUSDT")
        for endpoint in ENDPOINTS:
            symbol = endpoint.params.get("symbol")
            if symbol is not None:
                self.assertEqual(symbol, SYMBOL)

    def test_binance_book_validation(self) -> None:
        endpoint = Endpoint("binance", "book", "https://example.test", "/depth", {})
        checks = validate_payload(
            endpoint,
            {"lastUpdateId": 42, "bids": [["100.0", "1"]], "asks": [["100.1", "2"]]},
        )
        self.assertEqual(checks["update_id"], 42)
        self.assertGreater(checks["spread_bps"], 0)

    def test_bybit_book_preserves_sequences_and_times(self) -> None:
        endpoint = Endpoint("bybit", "book", "https://example.test", "/book", {})
        checks = validate_payload(
            endpoint,
            {
                "retCode": 0,
                "result": {
                    "s": SYMBOL,
                    "b": [["100.0", "1"]],
                    "a": [["100.1", "2"]],
                    "ts": 1000,
                    "cts": 999,
                    "u": 10,
                    "seq": 20,
                },
            },
        )
        self.assertEqual(checks["exchange_time_ms"], 1000)
        self.assertEqual(checks["matching_engine_time_ms"], 999)
        self.assertEqual(checks["update_id"], 10)
        self.assertEqual(checks["cross_sequence"], 20)

    def test_crossed_book_is_rejected(self) -> None:
        endpoint = Endpoint("binance", "book", "https://example.test", "/depth", {})
        with self.assertRaisesRegex(ValueError, "below best ask"):
            validate_payload(
                endpoint,
                {"lastUpdateId": 1, "bids": [["101", "1"]], "asks": [["100", "1"]]},
            )


if __name__ == "__main__":
    unittest.main()
