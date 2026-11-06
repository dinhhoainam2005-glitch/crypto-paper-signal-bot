import unittest

from market_structure.book_replay import ReplayError
from market_structure.state_frames import build_frames


def book(venue, ns, kind, **data):
    return {
        "venue": venue,
        "receipt_time_ns": ns,
        "event_type": "book",
        "data": {"update_kind": kind, **data},
    }


def trade(venue, ns, side, price, quantity):
    return {
        "venue": venue,
        "receipt_time_ns": ns,
        "event_type": "trade",
        "data": {"aggressor_side": side, "price": price, "quantity": quantity},
    }


class StateFrameTests(unittest.TestCase):
    def test_frame_uses_two_books_and_flow(self) -> None:
        events = [
            book("binance", 1_100_000_000, "snapshot", last_update_id=10, bids=[["100", "2"]], asks=[["101", "3"]]),
            book("bybit", 1_200_000_000, "snapshot", update_id=20, bids=[["100.2", "1"]], asks=[["100.8", "4"]]),
            trade("binance", 1_300_000_000, "BUY", "100.5", "2"),
            book("bybit", 2_100_000_000, "delta", update_id=21, bids=[], asks=[]),
        ]
        frames = build_frames(events)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["consolidated_mid"], "100.5")
        self.assertEqual(frames[0]["venues"]["binance"]["aggressive_buy_notional"], "201.0")
        self.assertEqual(frames[0]["venues"]["bybit"]["aggressive_buy_notional"], "0")

    def test_non_monotonic_input_fails(self) -> None:
        events = [
            book("binance", 2_000_000_000, "snapshot", last_update_id=1, bids=[["100", "1"]], asks=[["101", "1"]]),
            book("bybit", 1_000_000_000, "snapshot", update_id=1, bids=[["100", "1"]], asks=[["101", "1"]]),
        ]
        with self.assertRaisesRegex(ReplayError, "not ordered"):
            build_frames(events)


if __name__ == "__main__":
    unittest.main()
