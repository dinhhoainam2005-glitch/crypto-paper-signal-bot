import unittest

from market_structure import SYMBOL
from market_structure.normalize import normalize_record


def raw(venue, payload):
    return {"venue": venue, "receipt_time_ns": 1_000_000_000, "payload": payload}


class NormalizeTests(unittest.TestCase):
    def test_binance_depth_snapshot(self) -> None:
        event = normalize_record(
            raw(
                "binance",
                {"lastUpdateId": 42, "bids": [["100", "1"]], "asks": [["101", "2"]]},
            )
        )[0]
        self.assertEqual(event["event_type"], "book")
        self.assertEqual(event["data"]["update_kind"], "snapshot")
        self.assertEqual(event["data"]["last_update_id"], 42)

    def test_binance_trade_aggressor_side(self) -> None:
        event = normalize_record(
            raw(
                "binance",
                {
                    "e": "aggTrade", "E": 10, "T": 9, "s": SYMBOL, "a": 1,
                    "f": 2, "l": 3, "p": "100.1", "q": "0.2", "m": True,
                },
            )
        )[0]
        self.assertEqual(event["event_type"], "trade")
        self.assertEqual(event["data"]["aggressor_side"], "SELL")
        self.assertEqual(event["data"]["price"], "100.1")

    def test_bybit_trade_batch_expands(self) -> None:
        events = normalize_record(
            raw(
                "bybit",
                {
                    "topic": f"publicTrade.{SYMBOL}", "ts": 10,
                    "data": [
                        {"T": 9, "s": SYMBOL, "S": "Buy", "v": "1", "p": "100", "i": "a", "seq": 7},
                        {"T": 9, "s": SYMBOL, "S": "Sell", "v": "2", "p": "99", "i": "b", "seq": 7},
                    ],
                },
            )
        )
        self.assertEqual(len(events), 2)
        self.assertEqual([event["data"]["aggressor_side"] for event in events], ["BUY", "SELL"])

    def test_binance_liquidation_maps_closing_order(self) -> None:
        event = normalize_record(
            raw(
                "binance",
                {
                    "e": "forceOrder", "E": 10,
                    "o": {"s": SYMBOL, "S": "SELL", "T": 9, "p": "100", "q": "1"},
                },
            )
        )[0]
        self.assertEqual(event["data"]["liquidated_position_side"], "LONG")

    def test_bybit_liquidation_uses_documented_position_side(self) -> None:
        event = normalize_record(
            raw(
                "bybit",
                {
                    "topic": f"allLiquidation.{SYMBOL}", "ts": 10,
                    "data": [{"T": 9, "s": SYMBOL, "S": "Buy", "v": "1", "p": "100"}],
                },
            )
        )[0]
        self.assertEqual(event["data"]["liquidated_position_side"], "LONG")

    def test_non_btc_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unexpected symbol"):
            normalize_record(
                raw(
                    "binance",
                    {"e": "aggTrade", "E": 10, "T": 9, "s": "NOTBTC", "a": 1,
                     "f": 2, "l": 3, "p": "1", "q": "1", "m": False},
                )
            )


if __name__ == "__main__":
    unittest.main()
